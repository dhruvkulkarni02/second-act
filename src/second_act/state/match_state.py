"""The shared match state every agent reads from and writes to."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from second_act.analytics.signals import SignalSet, compute_signals
from second_act.analytics.windows import period_time
from second_act.schema.briefing import Briefing
from second_act.schema.cues import Cue
from second_act.schema.events import Event, EventType
from second_act.schema.metadata import MatchMetadata
from second_act.schema.predictions import Prediction
from second_act.schema.snapshots import Snapshot


@dataclass
class MatchState:
    """Events seen so far, the signals computed from them, and what agents produced.

    The analyst appends events and refreshes signals; the tactician writes the
    briefing and predictions; the watcher updates prediction statuses; the
    presenter appends cues. Everything an agent cites must be in ``signals``.
    """

    meta: MatchMetadata
    events: list[Event] = field(default_factory=list)
    snapshots: list[Snapshot] = field(default_factory=list)
    signals: SignalSet = field(default_factory=SignalSet)
    clock: tuple[int, float] = (1, 0.0)
    briefing: Briefing | None = None
    predictions: list[Prediction] = field(default_factory=list)
    cues: list[Cue] = field(default_factory=list)

    def ingest(self, events: Sequence[Event], snapshots: Sequence[Snapshot] = ()) -> None:
        """Append newly arrived events (and snapshots) and advance the clock."""
        self.events.extend(events)
        self.snapshots.extend(snapshots)
        if events:
            last = events[-1]
            self.clock = (last.period, period_time(last))

    def refresh_signals(self) -> SignalSet:
        """Recompute every signal from what has been seen so far."""
        self.signals = compute_signals(
            self.events, self.meta, self.snapshots or None, until=self.clock
        )
        return self.signals

    @property
    def score(self) -> dict[str, int]:
        """Goals so far per team."""
        goals = {self.meta.home.code: 0, self.meta.away.code: 0}
        for e in self.events:
            if e.type is EventType.GOAL and e.team in goals:
                goals[e.team] += 1
        return goals

    @property
    def at_halftime(self) -> bool:
        """True once the first-half whistle has gone and the second half has not started."""
        p1_over = any(e.type is EventType.PERIOD_END and e.period == 1 for e in self.events)
        return p1_over and not any(e.period == 2 for e in self.events)

    def player_name(self, player_id: str) -> str:
        """Display name for a player ID."""
        for team in (self.meta.home, self.meta.away):
            for p in team.squad:
                if p.player_id == player_id:
                    return p.name
        return player_id

    @classmethod
    def at_first_half_end(
        cls,
        events: Sequence[Event],
        meta: MatchMetadata,
        snapshots: Sequence[Snapshot] | None = None,
    ) -> MatchState:
        """Build the state as it stands at the half-time whistle."""
        first = [e for e in events if e.period == 1]
        state = cls(meta=meta)
        state.ingest(first, [s for s in (snapshots or []) if s.period == 1])
        state.clock = (1, meta.period_lengths_s[1])
        state.refresh_signals()
        return state
