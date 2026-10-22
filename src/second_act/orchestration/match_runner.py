"""Match runner: replays a stored match through every agent and emits the stream.

Pipeline per message: ingest (events arrive) -> analyst (signals, live triggers,
player tags at each window close) -> watcher (prediction checks in the second half)
-> tactician + verifier (half-time briefing, full-time recap) -> presenter (cues in
every variant). Messages go to an async ``emit`` callback; the API turns them into
server-sent events, tests collect them.

Window-close agent work that may call a model runs as a background task, serialized
by a lock, so the event stream keeps flowing; the stream pauses only at half-time
while the briefing is produced, as the broadcast would.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from agent_framework import Agent

from second_act.agents.analyst import Analyst
from second_act.agents.presenter import (
    LOCALIZER_INSTRUCTIONS,
    BriefingVariant,
    ModelLocalizer,
    Presenter,
    RecapVariant,
)
from second_act.agents.recap import template_recap_phrases
from second_act.agents.tactician import template_phrases
from second_act.agents.watcher import WATCHER_INSTRUCTIONS, ModelExplainer, Watcher, final_notes
from second_act.analytics.windows import WINDOW_S, WINDOWS_PER_PERIOD, period_time, window_of
from second_act.orchestration.agent_log import AgentLog
from second_act.orchestration.cache import BriefingCache
from second_act.orchestration.foundry import FoundrySettings, build_chat_client
from second_act.orchestration.halftime import run_halftime
from second_act.orchestration.recap import run_recap
from second_act.schema.events import Event, EventType
from second_act.schema.metadata import MatchMetadata
from second_act.schema.snapshots import Snapshot
from second_act.state.match_state import MatchState

Emit = Callable[[str, dict], Awaitable[None]]
Pace = Callable[[int, float], Awaitable[None]]

EVENT_FIELDS = (
    "event_id",
    "period",
    "minute",
    "second",
    "type",
    "team",
    "player_id",
    "x",
    "y",
    "end_x",
    "end_y",
    "outcome",
)


async def _no_pace(period: int, t: float) -> None:
    return None


@dataclass
class MatchOutputs:
    """Everything a session exposes besides the stream."""

    briefing: list[BriefingVariant] = field(default_factory=list)
    briefing_status: str = "pending"  # pending | in_progress | partial | ready
    recap: list[RecapVariant] = field(default_factory=list)
    recap_status: str = "pending"


class MatchRunner:
    """Drives one replay of a stored match."""

    def __init__(
        self,
        events: Sequence[Event],
        meta: MatchMetadata,
        snapshots: Sequence[Snapshot] | None = None,
        *,
        offline: bool,
        log: AgentLog,
        emit: Emit,
        pace: Pace = _no_pace,
        cache: BriefingCache | None = None,
        online_overrides: dict[str, Any] | None = None,
    ) -> None:
        self.events, self.meta = list(events), meta
        self.snapshots = list(snapshots or [])
        self.offline, self.log, self.emit, self.pace, self.cache = offline, log, emit, pace, cache
        self.state = MatchState(meta=meta)
        self.outputs = MatchOutputs()
        names = {p.player_id: p.name for t in (meta.home, meta.away) for p in t.squad}
        self.analyst = Analyst(meta, names)
        explainer = localizer = None
        if not offline:
            overrides = online_overrides or {}
            explainer, localizer = overrides.get("explainer"), overrides.get("localizer")
            if explainer is None or localizer is None:
                settings = FoundrySettings.from_env()
                client = build_chat_client(settings)
                opts = settings.default_options()
                explainer = explainer or ModelExplainer(
                    Agent(
                        client=client,
                        name="Watcher",
                        instructions=WATCHER_INSTRUCTIONS,
                        default_options=opts,
                    )
                )
                localizer = localizer or ModelLocalizer(
                    Agent(
                        client=client,
                        name="Presenter",
                        instructions=LOCALIZER_INSTRUCTIONS,
                        default_options=opts,
                    )
                )
        self.watcher = Watcher(explainer, log)
        self.presenter = Presenter(meta, localizer, log)
        self._lock = asyncio.Lock()
        self._tasks: list[asyncio.Task] = []

    # ----- helpers -----

    def _snaps_until(self, period: int, t: float, start: tuple[int, float]) -> list[Snapshot]:
        return [s for s in self.snapshots if start < (s.period, s.t) <= (period, t)]

    async def _emit_cues(self, cues) -> None:
        self.state.cues.extend(cues)
        for cue in cues:
            await self.emit("cue", cue.model_dump(mode="json"))

    async def _emit_predictions(self) -> None:
        await self.emit(
            "predictions",
            {"predictions": [p.model_dump(mode="json") for p in self.state.predictions]},
        )

    # ----- window close -----

    async def _close_window(self, period: int, window: int, end_t: float) -> None:
        state = self.state
        state.clock = (period, end_t)
        signals = state.refresh_signals()
        minute = state.minute
        items = self.analyst.on_window(signals, period, window, minute)
        self.log.record(
            "analyst",
            "window",
            period=period,
            window=window,
            signals=len(signals),
            items=[i.rule for i in items],
        )
        for item in items:
            await self._emit_cues(self.presenter.live(item))
        if period == 2 and state.predictions:
            self._tasks.append(asyncio.create_task(self._watch(signals, minute)))

    async def _watch(self, signals, minute: float, final: bool = False) -> None:
        async with self._lock:
            state = self.state
            state.predictions, changes = await self.watcher.check(
                state.predictions, signals, self.meta, minute, final
            )
            for change in changes:
                state.prediction_notes[change.prediction.prediction_id] = (
                    change.explanation,
                    change.evaluation.evidence,
                )
                await self._emit_cues(await self.presenter.prediction(change, minute))
            if changes or final:
                await self._emit_predictions()

    async def _drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks)
            self._tasks.clear()

    # ----- half-time and full-time -----

    async def _halftime(self) -> None:
        state = self.state
        self.outputs.briefing_status = "in_progress"
        await self.emit("status", {"phase": "halftime", "briefing": "in_progress"})
        briefing = await run_halftime(state, offline=self.offline, log=self.log, cache=self.cache)
        template = (
            template_phrases(state.signals, self.meta) if briefing.source == "template" else None
        )
        if template is None:
            # The English analyst version needs no model call: send it at once.
            first = self.presenter.source_variants(briefing)
            self.outputs.briefing, self.outputs.briefing_status = first, "partial"
            await self.emit(
                "briefing",
                {"status": "partial", "variants": [v.model_dump(mode="json") for v in first]},
            )
        variants = await self.presenter.briefing_variants(briefing, state.signals, template)
        self.outputs.briefing, self.outputs.briefing_status = variants, "ready"
        await self.emit(
            "briefing",
            {
                "status": "ready",
                "source": briefing.source,
                "attempts": briefing.attempts,
                "removed_claims": briefing.removed_claims,
                "variants": [v.model_dump(mode="json") for v in variants],
            },
        )
        await self._emit_cues(self.presenter.briefing_cues(variants, state.minute))
        await self._emit_predictions()

    async def _fulltime(self) -> None:
        state = self.state
        await self._drain()
        await self._watch(state.signals, state.minute, final=True)
        state.prediction_notes.update(final_notes(state.predictions, state.signals, self.meta))
        self.outputs.recap_status = "in_progress"
        await self.emit("status", {"phase": "fulltime", "recap": "in_progress"})
        recap = await run_recap(state, offline=self.offline, log=self.log, cache=self.cache)
        template = template_recap_phrases(state) if recap.source == "template" else None
        variants = await self.presenter.recap_variants(
            recap, state.signals, template, state.predictions
        )
        self.outputs.recap, self.outputs.recap_status = variants, "ready"
        await self.emit(
            "recap",
            {
                "status": "ready",
                "source": recap.source,
                "attempts": recap.attempts,
                "removed_claims": recap.removed_claims,
                "variants": [v.model_dump(mode="json") for v in variants],
            },
        )
        await self._emit_cues(self.presenter.recap_cues(variants, state.minute))

    # ----- main loop -----

    async def run(self) -> MatchOutputs:
        """Replay the whole match."""
        state = self.state
        await self.emit("match", {"metadata": self.meta.model_dump(mode="json")})
        last_snap: tuple[int, float] = (0, -1.0)
        for period in (1, 2):
            period_events = [e for e in self.events if e.period == period]
            length = self.meta.period_lengths_s[period]
            next_window = 0
            buffer: list[Event] = []
            for event in period_events:
                t = period_time(event)
                while next_window < WINDOWS_PER_PERIOD - 1 and t >= (next_window + 1) * WINDOW_S:
                    end = (next_window + 1) * WINDOW_S
                    state.ingest(buffer, self._snaps_until(period, end, last_snap))
                    last_snap, buffer = (period, end), []
                    await self._close_window(period, next_window, end)
                    next_window += 1
                await self.pace(period, t)
                buffer.append(event)
                await self.emit("event", event.model_dump(mode="json", include=set(EVENT_FIELDS)))
                if event.type is EventType.GOAL:
                    state.ingest(buffer)
                    buffer = []
                    await self.emit(
                        "score", {"score": state.score, "minute": event.minute, "team": event.team}
                    )
                if event.type is EventType.PERIOD_END:
                    state.ingest(buffer, self._snaps_until(period, length, last_snap))
                    last_snap, buffer = (period, length), []
                    await self._close_window(period, window_of(t), length)
                    await self._drain()
            if period == 1:
                await self._halftime()
        await self._fulltime()
        await self.emit("end", {"score": state.score, "log": self.log.summary()})
        return self.outputs
