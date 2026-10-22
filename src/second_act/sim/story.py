"""Raw-event checks that a generated match tells the story its config intends.

These are deliberately simple counts over the raw event file, independent of
the analytics package, so the generator can be validated (and the demo seed
chosen) before any signal is computed.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from second_act.analytics.frames import (
    FINAL_THIRD_X,
    channel_of,
    enters_final_third,
    to_team_frame,
)
from second_act.schema.events import Event, EventType
from second_act.schema.metadata import MatchMetadata


@dataclass
class HalfSummary:
    """Per-team, per-half counts used by the story checks."""

    passes: int = 0
    passes_complete: int = 0
    shots: int = 0
    goals: int = 0
    on_ball: int = 0
    entries: Counter[str] = field(default_factory=Counter)
    wins_open_play: int = 0
    win_x_total: float = 0.0
    wins_in_final_third: int = 0
    pressures: int = 0

    @property
    def completion(self) -> float:
        return self.passes_complete / self.passes if self.passes else 0.0

    @property
    def mean_win_x(self) -> float:
        return self.win_x_total / self.wins_open_play if self.wins_open_play else 0.0


def summarize(events: Sequence[Event], meta: MatchMetadata) -> dict[tuple[str, int], HalfSummary]:
    """Count passes, shots, final-third entries and ball wins per (team, period)."""
    out: dict[tuple[str, int], HalfSummary] = {}
    for code in (meta.home.code, meta.away.code):
        for period in (1, 2):
            out[(code, period)] = HalfSummary()
    for e in events:
        if e.team is None or e.x is None or e.y is None:
            continue
        s = out[(e.team, e.period)]
        high = meta.attacking_toward_high_x[e.period] == e.team
        x, _ = to_team_frame(e.x, e.y, high)
        if e.type in (EventType.PASS, EventType.CARRY, EventType.SHOT):
            s.on_ball += 1
        if e.type is EventType.PASS:
            s.passes += 1
            s.passes_complete += e.outcome == "complete"
        elif e.type is EventType.SHOT:
            s.shots += 1
        elif e.type is EventType.GOAL:
            s.goals += 1
        elif e.type is EventType.PRESSURE:
            s.pressures += 1
        elif e.type is EventType.POSSESSION_CHANGE and e.outcome == "open_play":
            s.wins_open_play += 1
            s.win_x_total += x
            s.wins_in_final_third += x >= FINAL_THIRD_X
        if (
            e.type in (EventType.PASS, EventType.CARRY)
            and e.outcome == "complete"
            and e.end_x is not None
            and e.end_y is not None
        ):
            ex, ey = to_team_frame(e.end_x, e.end_y, high)
            if enters_final_third(x, ex):
                s.entries[channel_of(ey)] += 1
    return out


@dataclass
class StoryReport:
    """Named pass/fail checks with a human-readable detail for each."""

    checks: dict[str, tuple[bool, str]]

    @property
    def passed(self) -> bool:
        return all(ok for ok, _ in self.checks.values())

    def __str__(self) -> str:
        return "\n".join(
            f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}"
            for name, (ok, detail) in self.checks.items()
        )


def check_demo_story(events: Sequence[Event], meta: MatchMetadata) -> StoryReport:
    """Check the PRD section 6.2 story for the demo match (HAR v KES)."""
    s = summarize(events, meta)
    h1, h2 = s[("HAR", 1)], s[("HAR", 2)]
    k1, k2 = s[("KES", 1)], s[("KES", 2)]
    checks: dict[str, tuple[bool, str]] = {}

    ht = (h1.goals, k1.goals)
    ft = (h1.goals + h2.goals, k1.goals + k2.goals)
    checks["halftime_score_1_0"] = (ht == (1, 0), f"HT {ht[0]}-{ht[1]}")
    checks["fulltime_score_1_1"] = (ft == (1, 1), f"FT {ft[0]}-{ft[1]}")
    goals = [(e.minute, e.team) for e in events if e.type is EventType.GOAL]
    checks["goal_timing"] = (
        goals == sorted(goals)
        and len(goals) == 2
        and goals[0][1] == "HAR"
        and 15 <= goals[0][0] < 45
        and goals[1][1] == "KES"
        and goals[1][0] >= 55,
        f"goals {goals} (want HAR after 15', KES after 55')",
    )

    left1, total1 = h1.entries["left"], sum(h1.entries.values())
    other1 = max(h1.entries["centre"], h1.entries["right"])
    checks["har_left_overload_h1"] = (
        total1 > 0 and left1 / total1 >= 0.45 and left1 >= 1.5 * other1,
        f"HAR H1 entries {dict(h1.entries)} (left share {left1 / max(total1, 1):.0%})",
    )

    checks["kes_lose_ball_in_own_third_h1"] = (
        h1.wins_in_final_third >= 15 and h1.wins_in_final_third >= 2 * k1.wins_in_final_third,
        f"HAR wins in KES third {h1.wins_in_final_third} vs KES wins in HAR third "
        f"{k1.wins_in_final_third}",
    )
    checks["kes_low_turnover_height_h1"] = (
        k1.mean_win_x <= h1.mean_win_x - 12,
        f"mean ball-win x: KES {k1.mean_win_x:.1f} m, HAR {h1.mean_win_x:.1f} m",
    )

    minutes1, minutes2 = meta.period_lengths_s[1] / 60, meta.period_lengths_s[2] / 60
    rate1, rate2 = h1.entries["left"] / minutes1, h2.entries["left"] / minutes2
    checks["har_left_entries_drop_h2"] = (
        rate2 <= 0.7 * rate1,
        f"HAR left entries/min {rate1:.2f} -> {rate2:.2f} ({rate2 / max(rate1, 1e-9) - 1:+.0%})",
    )

    loss1 = h1.wins_in_final_third / minutes1
    loss2 = h2.wins_in_final_third / minutes2
    loss_change = loss2 / max(loss1, 1e-9) - 1
    checks["deeper_pivot_little_effect"] = (
        abs(loss_change) <= 0.25,
        f"KES own-third losses/min {loss1:.2f} -> {loss2:.2f} ({loss_change:+.0%})",
    )
    return StoryReport(checks)


def format_summary(events: Sequence[Event], meta: MatchMetadata) -> str:
    """Return a compact table of the per-half summary, for the CLI."""
    rows = [
        f"{'team':4} {'p':1} {'pass':>5} {'cmp%':>5} {'onball%':>7} {'shots':>5} {'goals':>5} "
        f"{'press':>5} {'win_x':>6} {'win_F3':>6}  entries(L/C/R)"
    ]
    s = summarize(events, meta)
    for period in (1, 2):
        total_on_ball = sum(s[(c, period)].on_ball for c in (meta.home.code, meta.away.code))
        for code in (meta.home.code, meta.away.code):
            h = s[(code, period)]
            rows.append(
                f"{code:4} {period:1} {h.passes:5d} {h.completion:5.0%} "
                f"{h.on_ball / max(total_on_ball, 1):7.0%} {h.shots:5d} {h.goals:5d} "
                f"{h.pressures:5d} {h.mean_win_x:6.1f} {h.wins_in_final_third:6d}  "
                f"{h.entries['left']}/{h.entries['centre']}/{h.entries['right']}"
            )
    return "\n".join(rows)
