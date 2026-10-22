"""Five-minute analysis windows and event normalization into team frames.

Windows are numbered from 0 within each period: ``w0`` covers 0:00-5:00 of
period time, ``w8`` covers 40:00 to the final whistle (stoppage time is folded
into the last window so every window holds at least five minutes of play).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from second_act.analytics.frames import to_team_frame
from second_act.schema.events import Event
from second_act.schema.metadata import MatchMetadata

WINDOW_S = 300.0
WINDOWS_PER_PERIOD = 9
PERIOD_OFFSET_S = {1: 0.0, 2: 45 * 60.0}


def period_time(event: Event) -> float:
    """Seconds since the start of the event's period."""
    if event.t is not None:
        return event.t
    return event.clock_seconds - PERIOD_OFFSET_S[event.period]


def window_of(t: float) -> int:
    """Window index for a period time in seconds."""
    return min(int(t // WINDOW_S), WINDOWS_PER_PERIOD - 1)


def window_bounds(window: int, period_length_s: float) -> tuple[float, float]:
    """Start and end (period seconds) of a window; the last one runs to the whistle."""
    start = window * WINDOW_S
    end = period_length_s if window == WINDOWS_PER_PERIOD - 1 else start + WINDOW_S
    return start, max(end, start)


@dataclass(frozen=True)
class NormEvent:
    """An event with coordinates in the acting team's frame (attacking toward x = 105)."""

    event: Event
    t: float
    window: int
    x: float | None
    y: float | None
    end_x: float | None
    end_y: float | None

    @property
    def team(self) -> str | None:
        return self.event.team

    @property
    def period(self) -> int:
        return self.event.period


def attacks_high_x(meta: MatchMetadata, team: str, period: int) -> bool:
    """True when ``team`` attacks toward x = 105 in ``period``."""
    return meta.attacking_toward_high_x[period] == team


def normalize(events: Sequence[Event], meta: MatchMetadata) -> list[NormEvent]:
    """Rotate every team event into its team's frame and tag its window."""
    out: list[NormEvent] = []
    for e in events:
        t = period_time(e)
        x = y = ex = ey = None
        if e.team is not None:
            high = attacks_high_x(meta, e.team, e.period)
            if e.x is not None and e.y is not None:
                x, y = to_team_frame(e.x, e.y, high)
            if e.end_x is not None and e.end_y is not None:
                ex, ey = to_team_frame(e.end_x, e.end_y, high)
        out.append(NormEvent(e, t, window_of(t), x, y, ex, ey))
    return out
