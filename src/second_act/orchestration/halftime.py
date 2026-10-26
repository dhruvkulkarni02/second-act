"""Half-time briefing: the verified-writing workflow applied to the tactician's briefing.

Every agent reads the same ``MatchState``. Offline, the tactician is the rule template
and the verifier runs only its deterministic layer; online, a Foundry model drafts and
a second model call checks claim support. See ``orchestration/verified.py`` for the
graph and docs/agents.md for the rules.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from agent_framework import Agent

from second_act.agents.tactician import ModelDrafter, TemplateDrafter, tactician_instructions
from second_act.agents.tactician import template_briefing as _template_briefing
from second_act.agents.verifier import (
    SUPPORT_INSTRUCTIONS,
    Issue,
    ModelSupportChecker,
    check_briefing,
    claims_for_support_check,
    normalize_draft,
    prune,
)
from second_act.orchestration.agent_log import AgentLog
from second_act.orchestration.cache import BriefingCache, briefing_key
from second_act.orchestration.foundry import FoundrySettings, build_chat_client
from second_act.orchestration.verified import (
    MAX_RETRIES,
    Drafter,
    SupportChecker,
    run_verified,
)
from second_act.schema.briefing import Briefing, BriefingDraft
from second_act.state.match_state import MatchState

__all__ = ["DEFAULT_BUDGET_S", "MAX_RETRIES", "HalftimeTask", "online_agents", "run_halftime"]

DEFAULT_BUDGET_S = 25.0


@dataclass
class HalftimeTask:
    """The half-time briefing as a ``WritingTask``."""

    state: MatchState
    name: str = "halftime"
    writer: str = "tactician"

    def normalize(self, draft: BriefingDraft) -> BriefingDraft:
        return normalize_draft(draft)

    def check(self, draft: BriefingDraft) -> list[Issue]:
        return check_briefing(draft, self.state.signals, self.state.meta).issues

    def claims(self, draft: BriefingDraft) -> list[dict]:
        return claims_for_support_check(draft, self.state.signals)

    def prune(self, draft: BriefingDraft, issues: list[Issue]):
        """Drop failing claims; a failing headline is replaced by the template headline,
        which is grounded by construction."""
        head = [i for i in issues if i.where == "headline"]
        rest = [i for i in issues if i.where != "headline"]
        result = prune(draft, rest) if rest else (draft.model_copy(deep=True), [])
        if result is None:
            return None
        pruned, removed = result
        if head:
            template = self.template()
            removed = [
                *removed,
                f"headline: {draft.headline} -- "
                + "; ".join(i.message for i in head)
                + " (replaced by the template headline)",
            ]
            pruned = pruned.model_copy(
                update={
                    "headline": template.headline,
                    "headline_evidence": template.headline_evidence,
                }
            )
        return pruned, removed

    def template(self) -> BriefingDraft:
        return _template_briefing(self.state.signals, self.state.meta)

    def finalize(self, draft, source, attempts, fallback_reason, removed) -> Briefing:
        return Briefing(
            **draft.model_dump(),
            source=source,
            attempts=attempts,
            fallback_reason=fallback_reason,
            removed_claims=removed,
        )

    def start_info(self) -> dict:
        return {
            "clock": list(self.state.clock),
            "score": self.state.score,
            "signals": len(self.state.signals),
        }


def online_agents(
    settings: FoundrySettings | None = None,
) -> tuple[ModelDrafter, ModelSupportChecker]:
    """Foundry-backed tactician and support checker sharing one chat client."""
    settings = settings or FoundrySettings.from_env()
    client = build_chat_client(settings)
    options = settings.default_options()
    tactician = Agent(
        client=client,
        name="Tactician",
        instructions=tactician_instructions(),
        default_options=options,
    )
    checker = Agent(
        client=client, name="Verifier", instructions=SUPPORT_INSTRUCTIONS, default_options=options
    )
    return ModelDrafter(tactician), ModelSupportChecker(checker)


def _budget(budget_s: float | None) -> float:
    if budget_s is not None:
        return budget_s
    return float(os.environ.get("SECOND_ACT_HALFTIME_BUDGET_S", DEFAULT_BUDGET_S))


async def run_halftime(
    state: MatchState,
    *,
    offline: bool,
    log: AgentLog,
    drafter: Drafter | None = None,
    supporter: SupportChecker | None = None,
    budget_s: float | None = None,
    parallel_drafts: int | None = None,
    cache: BriefingCache | None = None,
) -> Briefing:
    """Produce the verified half-time briefing and store it (and its predictions) in the state.

    With a ``cache``, an online briefing already verified for the same events, model and
    prompt version is reused without calling the model.
    """
    key = None
    if cache is not None and not offline and drafter is None:
        settings = FoundrySettings.from_env()
        key = briefing_key(state, settings.model, settings.reasoning_effort)
        cached = cache.get(key)
        if cached is not None:
            _store(state, cached)
            log.record(
                "workflow", "cache_hit", key=key, source=cached.source, attempts=cached.attempts
            )
            return cached
    if drafter is None:
        if offline:
            drafter = TemplateDrafter()
        else:
            drafter, supporter = online_agents()
    if parallel_drafts is None:
        parallel_drafts = 1 if offline else int(os.environ.get("SECOND_ACT_PARALLEL_DRAFTS", 2))
    briefing: Briefing = await run_verified(
        HalftimeTask(state),
        state,
        log,
        drafter,
        None if offline else supporter,
        _budget(budget_s),
        parallel_drafts,
    )
    _store(state, briefing)
    if cache is not None and key is not None:
        path = cache.put(key, briefing, log.summary())
        log.record("workflow", "cache_store", key=key, path=str(path))
    return briefing


def _store(state: MatchState, briefing: Briefing) -> None:
    state.briefing = briefing
    state.predictions = [p for read in briefing.teams for p in read.predictions]
