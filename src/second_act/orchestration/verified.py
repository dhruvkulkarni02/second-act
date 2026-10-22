"""Generic verified-writing workflow (Microsoft Agent Framework).

    StartRequest -> [writer] --DraftMessage (N parallel candidates)--> [verifier] --> output
                       ^                                                  |
                       +----------------- RejectionMessage ---------------+   up to MAX_RETRIES
                                                                          |
                                          FallbackMessage ----------------+--> [fallback] --> output

A ``WritingTask`` says what is being written (the half-time briefing, the full-time
recap): how to check a draft deterministically, which claims the model support check
should judge, how to prune failing claims, the template fallback, and how to wrap the
accepted draft. The writer, verifier and fallback executors are shared.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Never, Protocol

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler

from second_act.agents.verifier import Issue
from second_act.orchestration.agent_log import AgentLog, usage_fields

MAX_RETRIES = 2
SUPPORT_CHECK_ESTIMATE_S = 8.0


class WritingTask(Protocol):
    """What the workflow writes and how it is verified."""

    name: str
    writer: str

    def normalize(self, draft: Any) -> Any: ...
    def check(self, draft: Any) -> list[Issue]: ...
    def claims(self, draft: Any) -> list[dict]: ...
    def prune(self, draft: Any, issues: list[Issue]) -> tuple[Any, list[str]] | None: ...
    def template(self) -> Any: ...
    def finalize(
        self,
        draft: Any,
        source: str,
        attempts: int,
        fallback_reason: str | None,
        removed: list[str],
    ) -> Any: ...
    def start_info(self) -> dict: ...


class Drafter(Protocol):
    """Writes one draft from the shared state."""

    source: str

    async def draft(
        self, state: Any, feedback: list[str] | None = None, previous: Any = None
    ) -> Any: ...


class SupportChecker(Protocol):
    """Layer-2 check: is each claim supported by the values of its evidence?"""

    async def check(self, claims: list[dict]) -> tuple[list[Issue], dict]: ...


@dataclass
class StartRequest:
    """Starts a verified-writing run."""

    state: Any


@dataclass
class Candidate:
    """One draft (or the error that prevented it)."""

    draft: Any
    source: str
    error: str | None = None


@dataclass
class DraftMessage:
    """One drafting round: one or more parallel candidates."""

    candidates: list[Candidate]
    attempt: int


@dataclass
class RejectionMessage:
    """The verifier's reasons, sent back to the writer for a retry."""

    reasons: list[str]
    attempt: int
    previous: Any


@dataclass
class FallbackMessage:
    """Give up on drafting: use the template built straight from the signals."""

    reason: str
    attempts: int


@dataclass
class Run:
    """Shared run context for the executors of one workflow run."""

    task: WritingTask
    state: Any
    log: AgentLog
    budget_s: float
    parallel: int = 1
    started: float = field(default_factory=time.perf_counter)
    last_draft_s: float = 0.0
    drafts_written: int = 0

    def elapsed(self) -> float:
        return time.perf_counter() - self.started


class WriterExecutor(Executor):
    """Writes (and rewrites) the draft."""

    def __init__(self, run: Run, drafter: Drafter) -> None:
        super().__init__(id=run.task.writer)
        self.run, self.drafter = run, drafter

    async def _one(self, attempt: int, index: int, feedback, previous) -> tuple[Candidate, float]:
        log, agent = self.run.log, self.run.task.writer
        try:
            result = await self.drafter.draft(self.run.state, feedback, previous)
        except Exception as exc:  # model or parsing failure counts as a failed draft
            reasons = getattr(exc, "reasons", None) or [f"{type(exc).__name__}: {exc}"[:500]]
            latency = getattr(exc, "latency_s", None) or 0.0
            log.record(
                agent,
                "draft",
                task=self.run.task.name,
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
            agent,
            "draft",
            task=self.run.task.name,
            attempt=attempt,
            candidate=index,
            source=result.source,
            latency_s=result.latency_s,
            **usage_fields(result.usage),
            output=result.draft.model_dump(mode="json"),
        )
        return Candidate(result.draft, result.source), result.latency_s

    async def _round(
        self, attempt: int, ctx: WorkflowContext[DraftMessage], feedback=None, previous=None
    ) -> None:
        """Write ``parallel`` independent drafts concurrently; the verifier keeps a good one."""
        results = await asyncio.gather(
            *(self._one(attempt, i, feedback, previous) for i in range(self.run.parallel))
        )
        self.run.drafts_written += len(results)
        self.run.last_draft_s = max(latency for _, latency in results)
        await ctx.send_message(DraftMessage([c for c, _ in results], attempt))

    @handler
    async def start(self, request: StartRequest, ctx: WorkflowContext[DraftMessage]) -> None:
        """First round."""
        self.run.log.record(
            self.run.task.writer, "start", task=self.run.task.name, **self.run.task.start_info()
        )
        await self._round(1, ctx)

    @handler
    async def retry(self, rejection: RejectionMessage, ctx: WorkflowContext[DraftMessage]) -> None:
        """Redraft using the verifier's reasons."""
        self.run.log.record(
            self.run.task.writer,
            "retry",
            task=self.run.task.name,
            attempt=rejection.attempt + 1,
            reasons=rejection.reasons,
        )
        await self._round(rejection.attempt + 1, ctx, rejection.reasons, rejection.previous)


class VerifierExecutor(Executor):
    """Checks each draft; approves it, repairs it, sends it back, or calls the fallback."""

    def __init__(self, run: Run, supporter: SupportChecker | None) -> None:
        super().__init__(id="verifier")
        self.run, self.supporter = run, supporter

    async def _support(self, draft: Any, attempt: int, index: int) -> list[Issue]:
        log = self.run.log
        assert self.supporter is not None
        try:
            issues, info = await self.supporter.check(self.run.task.claims(draft))
        except Exception as exc:  # the deterministic layer already passed; do not block on it
            log.record(
                "verifier",
                "support_check_error",
                task=self.run.task.name,
                attempt=attempt,
                candidate=index,
                error=f"{type(exc).__name__}: {exc}"[:500],
            )
            return []
        log.record(
            "verifier",
            "support_check",
            task=self.run.task.name,
            attempt=attempt,
            candidate=index,
            latency_s=info["latency_s"],
            **usage_fields(info["usage"]),
            unsupported=[str(i) for i in issues],
        )
        return issues

    async def _repair(self, message, drafts, issues, layers) -> Any | None:
        """Drop the failing claims instead of the whole draft, when the rest still stands.

        Candidates rejected by the model check are pruned and accepted directly: their other
        claims were already judged supported. Candidates rejected by the deterministic layer
        are pruned and then model-checked, if the budget still allows a support check.
        """
        task, log = self.run.task, self.run.log
        order = sorted(range(len(drafts)), key=lambda i: (layers[i] != "model", len(issues[i])))
        for i in order:
            if drafts[i] is None:
                continue
            result = task.prune(drafts[i], issues[i])
            if result is None or task.check(result[0]):
                continue
            pruned, removed = result
            if layers[i] != "model" and self.supporter is not None:
                if self.run.elapsed() + SUPPORT_CHECK_ESTIMATE_S > self.run.budget_s:
                    continue
                found = await self._support(pruned, message.attempt, i)
                if found:
                    second = task.prune(pruned, found)
                    if second is None or task.check(second[0]):
                        continue
                    pruned, removed = second[0], removed + second[1]
            log.record(
                "verifier",
                "pruned",
                task=task.name,
                attempt=message.attempt,
                candidate=i,
                removed=removed,
            )
            return task.finalize(
                pruned, message.candidates[i].source, self.run.drafts_written, None, removed
            )
        return None

    @handler
    async def verify(
        self, message: DraftMessage, ctx: WorkflowContext[RejectionMessage | FallbackMessage, Any]
    ) -> None:
        """Deterministic checks on every candidate, then the model support check (in parallel)
        on those that pass; the first fully verified candidate wins."""
        task, log = self.run.task, self.run.log
        drafts = [
            task.normalize(c.draft) if c.draft is not None else None for c in message.candidates
        ]
        issues: list[list[Issue]] = [
            [Issue("draft", f"no usable draft: {c.error}")] if d is None else task.check(d)
            for c, d in zip(message.candidates, drafts, strict=True)
        ]
        layers = ["error" if d is None else "deterministic" for d in drafts]
        passed = [i for i, found in enumerate(issues) if not found]
        if passed and self.supporter is not None:
            checks = await asyncio.gather(
                *(self._support(drafts[i], message.attempt, i) for i in passed)
            )
            for i, found in zip(passed, checks, strict=True):
                issues[i], layers[i] = found, "model"
        for i, found in enumerate(issues):
            log.record(
                "verifier",
                "verdict",
                task=task.name,
                attempt=message.attempt,
                candidate=i,
                ok=not found,
                layer=layers[i],
                issues=[str(x) for x in found],
            )
        winner = next((i for i, found in enumerate(issues) if not found), None)
        if winner is not None:
            await ctx.yield_output(
                task.finalize(
                    drafts[winner],
                    message.candidates[winner].source,
                    self.run.drafts_written,
                    None,
                    [],
                )
            )
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
        best = min(range(len(issues)), key=lambda i: (drafts[i] is None, len(issues[i])))
        await ctx.send_message(
            RejectionMessage([str(x) for x in issues[best]], message.attempt, drafts[best])
        )


class FallbackExecutor(Executor):
    """Builds the template output directly from the signals."""

    def __init__(self, run: Run) -> None:
        super().__init__(id="fallback")
        self.run = run

    @handler
    async def fallback(self, message: FallbackMessage, ctx: WorkflowContext[Never, Any]) -> None:
        """Template output; grounded by construction and re-checked for the log."""
        task = self.run.task
        draft = task.template()
        issues = task.check(draft)
        self.run.log.record(
            "fallback",
            "fallback",
            task=task.name,
            reason=message.reason,
            ok=not issues,
            issues=[str(i) for i in issues],
        )
        await ctx.yield_output(
            task.finalize(draft, "template", message.attempts, message.reason, [])
        )


def build_verified_workflow(run: Run, drafter: Drafter, supporter: SupportChecker | None):
    """Wire writer, verifier and fallback into an Agent Framework workflow."""
    writer = WriterExecutor(run, drafter)
    verifier = VerifierExecutor(run, supporter)
    fallback = FallbackExecutor(run)
    return (
        WorkflowBuilder(start_executor=writer, name=f"second-act-{run.task.name}")
        .add_edge(writer, verifier)
        .add_edge(verifier, writer, condition=lambda m: isinstance(m, RejectionMessage))
        .add_edge(verifier, fallback, condition=lambda m: isinstance(m, FallbackMessage))
        .build()
    )


async def run_verified(
    task: WritingTask,
    state: Any,
    log: AgentLog,
    drafter: Drafter,
    supporter: SupportChecker | None,
    budget_s: float,
    parallel: int = 1,
) -> Any:
    """Run the workflow and return the verified output."""
    run = Run(task, state, log, budget_s=budget_s, parallel=max(1, parallel))
    started = time.perf_counter()
    result = await build_verified_workflow(run, drafter, supporter).run(StartRequest(state))
    outputs = result.get_outputs()
    if not outputs:
        raise RuntimeError(f"{task.name} workflow produced no output")
    output = outputs[-1]
    log.record(
        "workflow",
        f"{task.name}_done",
        source=output.source,
        attempts=output.attempts,
        fallback_reason=output.fallback_reason,
        latency_s=round(time.perf_counter() - started, 2),
    )
    return output
