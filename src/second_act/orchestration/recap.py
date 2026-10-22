"""Full-time recap: the verified-writing workflow applied to the recap."""

from __future__ import annotations

import os
from dataclasses import dataclass

from agent_framework import Agent

from second_act.agents.recap import (
    RECAP_INSTRUCTIONS,
    ModelRecapDrafter,
    TemplateRecapDrafter,
    check_recap,
    normalize_recap,
    prune_recap,
    recap_claims,
    template_recap,
)
from second_act.agents.verifier import SUPPORT_INSTRUCTIONS, Issue, ModelSupportChecker
from second_act.orchestration.agent_log import AgentLog
from second_act.orchestration.cache import BriefingCache, briefing_key
from second_act.orchestration.foundry import FoundrySettings, build_chat_client
from second_act.orchestration.verified import Drafter, SupportChecker, run_verified
from second_act.schema.recap import Recap, RecapDraft
from second_act.state.match_state import MatchState

DEFAULT_RECAP_BUDGET_S = 40.0


@dataclass
class RecapTask:
    """The full-time recap as a ``WritingTask``."""

    state: MatchState
    name: str = "recap"
    writer: str = "tactician"

    def normalize(self, draft: RecapDraft) -> RecapDraft:
        return normalize_recap(draft)

    def check(self, draft: RecapDraft) -> list[Issue]:
        return check_recap(draft, self.state)

    def claims(self, draft: RecapDraft) -> list[dict]:
        return recap_claims(draft, self.state.signals)

    def prune(self, draft: RecapDraft, issues: list[Issue]):
        return prune_recap(draft, issues, self.state)

    def template(self) -> RecapDraft:
        return template_recap(self.state)

    def finalize(self, draft, source, attempts, fallback_reason, removed) -> Recap:
        return Recap(
            **draft.model_dump(),
            source=source,
            attempts=attempts,
            fallback_reason=fallback_reason,
            removed_claims=removed,
        )

    def start_info(self) -> dict:
        return {
            "score": self.state.score,
            "signals": len(self.state.signals),
            "predictions": {p.prediction_id: p.status.value for p in self.state.predictions},
        }


def online_recap_agents(settings: FoundrySettings | None = None):
    """Foundry-backed recap writer and support checker sharing one chat client."""
    settings = settings or FoundrySettings.from_env()
    client = build_chat_client(settings)
    options = settings.default_options()
    writer = Agent(
        client=client, name="Tactician", instructions=RECAP_INSTRUCTIONS, default_options=options
    )
    checker = Agent(
        client=client, name="Verifier", instructions=SUPPORT_INSTRUCTIONS, default_options=options
    )
    return ModelRecapDrafter(writer), ModelSupportChecker(checker)


async def run_recap(
    state: MatchState,
    *,
    offline: bool,
    log: AgentLog,
    drafter: Drafter | None = None,
    supporter: SupportChecker | None = None,
    budget_s: float | None = None,
    parallel_drafts: int | None = None,
    cache: BriefingCache | None = None,
) -> Recap:
    """Produce the verified full-time recap and store it in the state."""
    key = None
    if cache is not None and not offline and drafter is None:
        settings = FoundrySettings.from_env()
        key = briefing_key(state, settings.model, settings.reasoning_effort, kind="recap")
        cached = cache.get(key, Recap)
        if cached is not None:
            state.recap = cached
            log.record("workflow", "cache_hit", key=key, task="recap", source=cached.source)
            return cached
    if drafter is None:
        if offline:
            drafter = TemplateRecapDrafter()
        else:
            drafter, supporter = online_recap_agents()
    budget = (
        budget_s
        if budget_s is not None
        else float(os.environ.get("SECOND_ACT_RECAP_BUDGET_S", DEFAULT_RECAP_BUDGET_S))
    )
    if parallel_drafts is None:
        parallel_drafts = 1 if offline else int(os.environ.get("SECOND_ACT_PARALLEL_DRAFTS", 2))
    recap: Recap = await run_verified(
        RecapTask(state),
        state,
        log,
        drafter,
        None if offline else supporter,
        budget,
        parallel_drafts,
    )
    state.recap = recap
    if cache is not None and key is not None:
        cache.put(key, recap, log.summary())
    return recap
