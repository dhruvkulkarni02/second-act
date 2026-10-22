"""Half-time workflow (Microsoft Agent Framework): tactician -> verifier -> retry or fallback.

    HalftimeRequest -> [tactician] --DraftMessage--> [verifier] --Briefing--> output
                            ^                            |
                            +------RejectionMessage------+   (up to MAX_RETRIES)
                                                         |
                                       FallbackMessage --+--> [fallback] --Briefing--> output

Every agent reads the same ``MatchState``. Offline, the tactician is the rule
template and the verifier runs only its deterministic layer; online, a Foundry
model drafts and a second model call checks claim support. Every draft,
verdict, retry and fallback is written to the session ``AgentLog``.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any, Never, Protocol

from agent_framework import Agent, Executor, WorkflowBuilder, WorkflowContext, handler

from second_act.agents.tactician import (
    DraftResult,
    ModelDrafter,
    TemplateDrafter,
    tactician_instructions,
    template_briefing,
)
from second_act.agents.verifier import (
    SUPPORT_INSTRUCTIONS,
    Issue,
    ModelSupportChecker,
    check_briefing,
    normalize_draft,
    prune,
)
from second_act.orchestration.agent_log import AgentLog, usage_fields
from second_act.orchestration.cache import BriefingCache, briefing_key
from second_act.orchestration.foundry import FoundrySettings, build_chat_client
from second_act.schema.briefing import Briefing, BriefingDraft
from second_act.state.match_state import MatchState

MAX_RETRIES = 2
DEFAULT_BUDGET_S = 25.0
SUPPORT_CHECK_ESTIMATE_S = 8.0


class Drafter(Protocol):
    """Anything that can write a briefing draft from the match state."""

    source: str

    async def draft(
        self,
        state: MatchState,
        feedback: list[str] | None = None,
        previous: BriefingDraft | None = None,
    ) -> DraftResult: ...


class SupportChecker(Protocol):
    """Layer-2 claim support check."""

    async def check(self, draft: BriefingDraft, signals: Any) -> tuple[list[Issue], dict]: ...


@dataclass
class HalftimeRequest:
    """Starts the workflow at the half-time whistle."""

    state: MatchState


@dataclass
class Candidate:
    """One draft (or the error that prevented it)."""

    draft: BriefingDraft | None
    source: str
    error: str | None = None


@dataclass
class DraftMessage:
    """One drafting round from the tactician: one or more parallel candidates."""

    candidates: list[Candidate]
    attempt: int


@dataclass
class RejectionMessage:
    """The verifier's reasons, sent back to the tactician for a retry."""

    reasons: list[str]
    attempt: int
    previous: BriefingDraft | None


@dataclass
class FallbackMessage:
    """Give up on drafting: build the template briefing straight from the signals."""

    reason: str
    attempts: int


@dataclass
class _Run:
    state: MatchState
    log: AgentLog
    started: float = field(default_factory=time.perf_counter)
    budget_s: float = DEFAULT_BUDGET_S
    parallel: int = 1
    last_draft_s: float = 0.0
    drafts_written: int = 0

    def elapsed(self) -> float:
        return time.perf_counter() - self.started


class TacticianExecutor(Executor):
    """Writes (and rewrites) the briefing."""

    def __init__(self, run: _Run, drafter: Drafter) -> None:
        super().__init__(id="tactician")
        self.run, self.drafter = run, drafter

    async def _one(
        self,
        attempt: int,
        index: int,
        feedback: list[str] | None,
        previous: BriefingDraft | None,
    ) -> tuple[Candidate, float]:
        log = self.run.log
        try:
            result = await self.drafter.draft(self.run.state, feedback, previous)
        except Exception as exc:  # model or parsing failure counts as a failed draft
            reasons = getattr(exc, "reasons", None) or [f"{type(exc).__name__}: {exc}"[:500]]
            latency = getattr(exc, "latency_s", None) or 0.0
            log.record(
                "tactician",
                "draft",
                attempt=attempt,
                candidate=index,
                source=self.drafter.source,
                ok=False,
                error=reasons,
                latency_s=latency,
                **usage_fields(getattr(exc, "usage", None)),
            )
            return Candidate(None, self.drafter.source, "; ".join(reasons)), latency
        log.record(
            "tactician",
            "draft",
            attempt=attempt,
            candidate=index,
            source=result.source,
            latency_s=result.latency_s,
            **usage_fields(result.usage),
            output=result.draft.model_dump(mode="json"),
        )
        return Candidate(result.draft, result.source), result.latency_s

    async def _draft(
        self,
        attempt: int,
        ctx: WorkflowContext[DraftMessage],
        feedback: list[str] | None = None,
        previous: BriefingDraft | None = None,
    ) -> None:
        """Write ``parallel`` independent drafts concurrently; the verifier keeps a good one."""
        results = await asyncio.gather(
            *(self._one(attempt, i, feedback, previous) for i in range(self.run.parallel))
        )
        self.run.drafts_written += len(results)
        self.run.last_draft_s = max(latency for _, latency in results)
        await ctx.send_message(DraftMessage([c for c, _ in results], attempt))

    @handler
    async def start(self, request: HalftimeRequest, ctx: WorkflowContext[DraftMessage]) -> None:
        """First draft at the whistle."""
        state = request.state
        self.run.log.record(
            "tactician",
            "start",
            clock=list(state.clock),
            score=state.score,
            signals=len(state.signals),
        )
        await self._draft(1, ctx)

    @handler
    async def retry(self, rejection: RejectionMessage, ctx: WorkflowContext[DraftMessage]) -> None:
        """Redraft using the verifier's reasons."""
        self.run.log.record(
            "tactician", "retry", attempt=rejection.attempt + 1, reasons=rejection.reasons
        )
        await self._draft(rejection.attempt + 1, ctx, rejection.reasons, rejection.previous)


class VerifierExecutor(Executor):
    """Checks each draft; approves it, sends it back, or calls the fallback."""

    def __init__(self, run: _Run, supporter: SupportChecker | None) -> None:
        super().__init__(id="verifier")
        self.run, self.supporter = run, supporter

    def _briefing(self, draft: BriefingDraft, source: str, removed: list[str] | None = None):
        return Briefing(
            **draft.model_dump(),
            source=source,  # type: ignore[arg-type]
            attempts=self.run.drafts_written,
            removed_claims=removed or [],
        )

    async def _repair(
        self,
        message: DraftMessage,
        drafts: list[BriefingDraft | None],
        issues: list[list[Issue]],
        layers: list[str],
    ) -> Briefing | None:
        """Drop the failing claims instead of the whole draft, when the rest still stands.

        Candidates rejected by the model check are pruned and accepted directly: their other
        claims were already judged supported. Candidates rejected by the deterministic layer
        are pruned and then model-checked, if the budget still allows a support check.
        """
        state, log = self.run.state, self.run.log
        order = sorted(range(len(drafts)), key=lambda i: (layers[i] != "model", len(issues[i])))
        for i in order:
            draft = drafts[i]
            if draft is None:
                continue
            result = prune(draft, issues[i])
            if result is None:
                continue
            pruned, removed = result
            if not check_briefing(pruned, state.signals, state.meta).ok:
                continue
            if layers[i] != "model" and self.supporter is not None:
                if self.run.elapsed() + SUPPORT_CHECK_ESTIMATE_S > self.run.budget_s:
                    continue
                found = await self._support(pruned, message.attempt, i)
                if found:
                    second = prune(pruned, found)
                    if (
                        second is None
                        or not check_briefing(second[0], state.signals, state.meta).ok
                    ):
                        continue
                    pruned, removed = second[0], removed + second[1]
            log.record("verifier", "pruned", attempt=message.attempt, candidate=i, removed=removed)
            return self._briefing(pruned, message.candidates[i].source, removed)
        return None

    async def _support(self, draft: BriefingDraft, attempt: int, index: int) -> list[Issue]:
        log = self.run.log
        assert self.supporter is not None
        try:
            issues, info = await self.supporter.check(draft, self.run.state.signals)
        except Exception as exc:  # the deterministic layer already passed; do not block on it
            log.record(
                "verifier",
                "support_check_error",
                attempt=attempt,
                candidate=index,
                error=f"{type(exc).__name__}: {exc}"[:500],
            )
            return []
        log.record(
            "verifier",
            "support_check",
            attempt=attempt,
            candidate=index,
            latency_s=info["latency_s"],
            **usage_fields(info["usage"]),
            unsupported=[str(i) for i in issues],
        )
        return issues

    @handler
    async def verify(
        self,
        message: DraftMessage,
        ctx: WorkflowContext[RejectionMessage | FallbackMessage, Briefing],
    ) -> None:
        """Deterministic checks on every candidate, then the model support check (in parallel)
        on those that pass; the first fully verified candidate wins."""
        state, log = self.run.state, self.run.log
        drafts = [
            normalize_draft(c.draft) if c.draft is not None else None for c in message.candidates
        ]
        issues: list[list[Issue]] = []
        for c, draft in zip(message.candidates, drafts, strict=True):
            if draft is None:
                issues.append([Issue("draft", f"no usable draft: {c.error}")])
            else:
                issues.append(check_briefing(draft, state.signals, state.meta).issues)
        layers = ["error" if d is None else "deterministic" for d in drafts]
        passed = [i for i, found in enumerate(issues) if not found]
        if passed and self.supporter is not None:
            checks = await asyncio.gather(
                *(self._support(drafts[i], message.attempt, i) for i in passed)  # type: ignore[arg-type]
            )
            for i, found in zip(passed, checks, strict=True):
                issues[i], layers[i] = found, "model"
        for i, found in enumerate(issues):
            log.record(
                "verifier",
                "verdict",
                attempt=message.attempt,
                candidate=i,
                ok=not found,
                layer=layers[i],
                issues=[str(x) for x in found],
            )
        winner = next((i for i, found in enumerate(issues) if not found), None)
        if winner is not None:
            draft = drafts[winner]
            assert draft is not None
            await ctx.yield_output(self._briefing(draft, message.candidates[winner].source))
            return
        repaired = await self._repair(message, drafts, issues, layers)
        if repaired is not None:
            await ctx.yield_output(repaired)
            return
        # Predictive budget: retry only if another round like the last one still fits.
        out_of_time = self.run.elapsed() + self.run.last_draft_s > self.run.budget_s
        if message.attempt > MAX_RETRIES or out_of_time:
            why = (
                "time budget exceeded"
                if out_of_time
                else f"all drafts rejected after {message.attempt} rounds"
            )
            await ctx.send_message(FallbackMessage(why, self.run.drafts_written))
            return
        # Retry from the candidate closest to passing.
        best = min(range(len(issues)), key=lambda i: (drafts[i] is None, len(issues[i])))
        await ctx.send_message(
            RejectionMessage([str(x) for x in issues[best]], message.attempt, drafts[best])
        )


class FallbackExecutor(Executor):
    """Builds the template briefing directly from the signals."""

    def __init__(self, run: _Run) -> None:
        super().__init__(id="fallback")
        self.run = run

    @handler
    async def fallback(
        self, message: FallbackMessage, ctx: WorkflowContext[Never, Briefing]
    ) -> None:
        """Template briefing; it is grounded by construction and re-checked for the log."""
        state = self.run.state
        draft = template_briefing(state.signals, state.meta)
        report = check_briefing(draft, state.signals, state.meta)
        self.run.log.record(
            "fallback", "fallback", reason=message.reason, ok=report.ok, issues=report.reasons()
        )
        await ctx.yield_output(
            Briefing(
                **draft.model_dump(),
                source="template",
                attempts=message.attempts,
                fallback_reason=message.reason,
            )
        )


def build_halftime_workflow(
    state: MatchState,
    log: AgentLog,
    drafter: Drafter,
    supporter: SupportChecker | None,
    budget_s: float = DEFAULT_BUDGET_S,
    parallel: int = 1,
):
    """Wire tactician, verifier and fallback into an Agent Framework workflow."""
    run = _Run(state, log, budget_s=budget_s, parallel=max(1, parallel))
    tactician = TacticianExecutor(run, drafter)
    verifier = VerifierExecutor(run, supporter)
    fallback = FallbackExecutor(run)
    return (
        WorkflowBuilder(start_executor=tactician, name="second-act-halftime")
        .add_edge(tactician, verifier)
        .add_edge(verifier, tactician, condition=lambda m: isinstance(m, RejectionMessage))
        .add_edge(verifier, fallback, condition=lambda m: isinstance(m, FallbackMessage))
        .build()
    )


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
    budget = (
        budget_s
        if budget_s is not None
        else float(os.environ.get("SECOND_ACT_HALFTIME_BUDGET_S", DEFAULT_BUDGET_S))
    )
    if parallel_drafts is None:
        parallel_drafts = 1 if offline else int(os.environ.get("SECOND_ACT_PARALLEL_DRAFTS", 2))
    workflow = build_halftime_workflow(
        state, log, drafter, None if offline else supporter, budget, parallel_drafts
    )
    started = time.perf_counter()
    result = await workflow.run(HalftimeRequest(state))
    outputs = result.get_outputs()
    if not outputs:
        raise RuntimeError("half-time workflow produced no briefing")
    briefing: Briefing = outputs[-1]
    _store(state, briefing)
    log.record(
        "workflow",
        "halftime_done",
        source=briefing.source,
        attempts=briefing.attempts,
        fallback_reason=briefing.fallback_reason,
        latency_s=round(time.perf_counter() - started, 2),
    )
    if cache is not None and key is not None:
        path = cache.put(key, briefing, log.summary())
        log.record("workflow", "cache_store", key=key, path=str(path))
    return briefing


def _store(state: MatchState, briefing: Briefing) -> None:
    state.briefing = briefing
    state.predictions = [p for read in briefing.teams for p in read.predictions]
