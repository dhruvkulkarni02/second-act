"""Analytics: every signal on small hand-built inputs, plus the demo-match story."""

from __future__ import annotations

import itertools
import math
import re

import pytest

from second_act.analytics import compute_signals, make_id, pass_network
from second_act.analytics.models import (
    expected_goals,
    goal_angle,
    is_progressive,
    opponents_bypassed,
    pass_difficulty,
)
from second_act.analytics.windows import normalize, period_time, window_bounds, window_of
from second_act.schema import Event, Snapshot
from second_act.schema.io import read_events, read_metadata, read_snapshots
from second_act.schema.metadata import MatchMetadata
from second_act.schema.snapshots import BallPosition, PlayerPosition
from tests.conftest import DATA

_ids = itertools.count(1)
NEEDS_OUTCOME = {
    "pass": "complete", "carry": "complete", "tackle": "won", "interception": "won",
    "possession_change": "open_play", "shot": "saved",
}  # fmt: skip


def ev(etype: str, team: str | None = None, x: float | None = None, y: float | None = None,
       end: tuple[float, float] | None = None, period: int = 1, t: float = 0.0,
       **fields: object) -> Event:  # fmt: skip
    """An event in raw coordinates (HAR attacks toward x = 105 in period 1)."""
    clock = int(t) + (0 if period == 1 else 45 * 60)
    data: dict[str, object] = {
        "event_id": f"e-{next(_ids):06d}", "match_id": "m-test", "period": period,
        "minute": clock // 60, "second": clock % 60, "type": etype, "team": team, "t": t,
    }  # fmt: skip
    if team is not None:
        data["player_id"] = f"{team}-9"
    if x is not None:
        data.update(x=x, y=y)
    if end is not None:
        data.update(end_x=end[0], end_y=end[1])
    if etype in NEEDS_OUTCOME:
        data["outcome"] = NEEDS_OUTCOME[etype]
    data.update(fields)
    return Event.model_validate(data)


def kes(x: float, y: float) -> tuple[float, float]:
    """Raw coordinates for a point given in Kestrel's period-1 frame."""
    return 105 - x, 68 - y


@pytest.fixture(scope="module")
def meta() -> MatchMetadata:
    base = read_metadata(DATA / "m-001" / "metadata.json")
    return base.model_copy(update={"period_lengths_s": {1: 2820.0, 2: 2700.0}})


def ended(events: list[Event], period: int = 1, t: float = 2820.0) -> list[Event]:
    return [*events, ev("period_end", period=period, t=t)]


# ----- windows and frames ---------------------------------------------------


def test_windows_fold_stoppage_into_last() -> None:
    assert [window_of(t) for t in (0, 299.9, 300, 2399, 2400, 2700, 3000)] == [
        0, 0, 1, 7, 8, 8, 8]  # fmt: skip
    assert window_bounds(8, 2820.0) == (2400.0, 2820.0)
    assert window_bounds(3, 2820.0) == (900.0, 1200.0)


def test_period_time_falls_back_to_clock() -> None:
    e = Event(event_id="e-000001", match_id="m", period=2, minute=50, second=30, type="period_end")
    assert period_time(e) == 330


def test_normalize_rotates_second_half(meta: MatchMetadata) -> None:
    # HAR attacks toward x = 0 in period 2, so raw (10, 60) is (95, 8) in HAR's frame.
    n = normalize([ev("carry", "HAR", 10, 60, end=(5, 62), period=2, t=5)], meta)[0]
    assert (n.x, n.y, n.end_x, n.end_y) == (95, 8, 100, 6)


def test_signal_id_format() -> None:
    assert make_id("channel_entries", "HAR", "left", 1, 4) == "sig-channel_entries-HAR-left-p1w4"
    assert make_id("control_index", period=2) == "sig-control_index-p2"


# ----- event signals --------------------------------------------------------


def test_possession_share_and_zones(meta: MatchMetadata) -> None:
    events = ended(
        [
            ev("pass", "HAR", 80, 34, end=(85, 30), t=10),  # HAR final third
            ev("pass", "HAR", 50, 34, end=(55, 30), t=20),  # HAR middle third
            ev("carry", "HAR", 90, 34, end=(92, 34), t=30),  # HAR final third
            ev("pass", "KES", *kes(20, 34), end=kes(25, 30), t=40),  # KES own third = HAR final
        ]
    )
    s = compute_signals(events, meta)
    assert s.value("possession", "HAR", None, 1) == 75.0
    assert s.value("possession", "HAR", "final_third", 1) == pytest.approx(66.7)
    assert s.value("possession", "KES", "defensive_third", 1) == pytest.approx(33.3)
    assert s.value("possession", "HAR", None, 1, 0) == 75.0


def test_field_tilt_counts_passes_started_in_final_third(meta: MatchMetadata) -> None:
    events = ended(
        [
            ev("pass", "HAR", 75, 20, end=(80, 20), t=10),
            ev("pass", "HAR", 72, 50, end=(60, 50), t=20, outcome="incomplete"),
            ev("pass", "HAR", 40, 20, end=(50, 20), t=30),
            ev("pass", "KES", *kes(71, 34), end=kes(80, 34), t=40),
        ]
    )
    assert compute_signals(events, meta).value("field_tilt", "HAR", None, 1) == pytest.approx(66.7)


def test_ppda(meta: MatchMetadata) -> None:
    # Four KES passes in KES's own defensive 60% (x < 63), one in HAR's half.
    kes_passes = [ev("pass", "KES", *kes(x, 30), end=kes(x + 5, 30), t=10 + i)
                  for i, x in enumerate((10, 30, 50, 62, 70))]  # fmt: skip
    har_actions = [
        ev("tackle", "HAR", 60, 30, t=30),  # x >= 42: counts
        ev("interception", "HAR", 80, 30, t=31),  # counts
        ev("tackle", "HAR", 30, 30, t=32),  # HAR's own half: excluded
        ev("tackle", "HAR", 70, 30, t=33, outcome="foul"),  # tackle-fouls count via the foul
        ev("foul", "HAR", 70, 30, t=33),  # counts
    ]
    s = compute_signals(ended(kes_passes + har_actions), meta)
    assert s.value("ppda", "HAR", None, 1) == pytest.approx(4 / 3, abs=0.05)
    assert s.value("ppda", "KES", None, 1) is None  # no KES defensive actions


def test_turnover_height_and_high_regains(meta: MatchMetadata) -> None:
    events = ended(
        [
            ev("possession_change", "HAR", 80, 30, t=10),
            ev("possession_change", "HAR", 40, 30, t=20),
            ev("possession_change", "HAR", 90, 30, t=30, outcome="dead_ball"),  # restart: excluded
            ev("possession_change", "KES", *kes(20, 30), t=40),
        ]
    )
    s = compute_signals(events, meta)
    assert s.value("turnover_height", "HAR", None, 1) == 60.0
    assert s.value("high_regains", "HAR", None, 1) == 1
    assert s.value("turnover_height", "KES", None, 1) == 20.0


def test_channel_entries_per_window_and_share(meta: MatchMetadata) -> None:
    events = ended(
        [
            ev("pass", "HAR", 60, 30, end=(75, 10), t=1210),  # w4, left
            ev("carry", "HAR", 68, 10, end=(72, 12), t=1220),  # w4, left
            ev("pass", "HAR", 60, 30, end=(80, 34), t=1230),  # w4, centre
            ev("pass", "HAR", 60, 30, end=(80, 60), t=1240, outcome="incomplete"),  # not complete
            ev("pass", "HAR", 72, 30, end=(80, 60), t=1250),  # already in final third
            ev("pass", "HAR", 60, 30, end=(75, 60), t=100),  # w0, right
        ]
    )
    s = compute_signals(events, meta)
    assert s.get("sig-channel_entries-HAR-left-p1w4").value == 2  # type: ignore[union-attr]
    assert s.value("channel_entries", "HAR", "centre", 1, 4) == 1
    assert s.value("channel_entries", "HAR", "right", 1, 0) == 1
    assert s.value("final_third_entries", "HAR", None, 1) == 4
    assert s.value("channel_share", "HAR", "left", 1) == 50.0


def test_progressive_passes(meta: MatchMetadata) -> None:
    assert is_progressive(50, 34, 60, 34)
    assert not is_progressive(50, 34, 59, 34)
    # Distance-based: cutting inside from the byline counts even with little forward gain.
    assert is_progressive(90, 5, 92, 34)
    events = ended(
        [
            ev("pass", "HAR", 50, 34, end=(61, 34), t=10),
            ev("pass", "HAR", 50, 34, end=(55, 34), t=20),
            ev("pass", "HAR", 50, 34, end=(80, 34), t=30, outcome="incomplete"),
        ]
    )
    assert compute_signals(events, meta).value("progressive_passes", "HAR", None, 1) == 1


def test_shots_xg_goals_and_fast_shots(meta: MatchMetadata) -> None:
    events = ended(
        [
            ev(
                "shot",
                "HAR",
                94,
                34,
                end=(105, 34),
                t=10,
                outcome="goal",
                speed_kmh=110.0,
                body_part="foot",
            ),
            ev("goal", "HAR", 94, 34, t=10),
            ev("shot", "HAR", 80, 34, end=(105, 40), t=20, outcome="off_target", speed_kmh=90.0),
            ev("shot", "HAR", 99, 30, end=(105, 34), t=30, body_part="head", speed_kmh=50.0),
        ]
    )
    s = compute_signals(events, meta)
    expected = expected_goals(94, 34) + expected_goals(80, 34) + expected_goals(99, 30, "head")
    assert s.value("xg", "HAR", None, 1) == round(expected, 2)
    assert s.value("shots", "HAR", None, 1) == 3
    assert s.value("shots_on_target", "HAR", None, 1) == 2
    assert s.value("goals", "HAR", None, 1) == 1
    assert s.value("fast_shots", "HAR", None, 1) == 1
    assert s.value("max_shot_speed", "HAR", "n9", 1) == 110.0


def test_xg_model_shape() -> None:
    assert expected_goals(99.5, 34) > expected_goals(94, 34) > expected_goals(80, 34)
    assert expected_goals(94, 34) > expected_goals(94, 5)  # tight angle
    assert expected_goals(94, 34, "head") == pytest.approx(0.7 * expected_goals(94, 34))
    assert expected_goals(94, 34) == pytest.approx(0.176, abs=0.005)  # penalty spot
    assert goal_angle(94, 34) == pytest.approx(2 * math.atan(3.66 / 11), abs=1e-9)


def test_pass_difficulty_formula() -> None:
    assert pass_difficulty((50, 34), (50, 34), False, bypassed=0) == 0
    assert pass_difficulty((20, 34), (70, 34), True, bypassed=5) == 100
    # 20 m forward, no pressure, 2 opponents bypassed:
    # 0.30*(20/40) + 0.20*0 + 0.20*(20/30) + 0.30*(2/5) = 0.4033
    assert pass_difficulty((40, 34), (60, 34), False, bypassed=2) == 40.3
    # Without snapshots, bypassed is estimated as forward metres / 10.
    assert pass_difficulty((40, 34), (60, 34), False) == 40.3


def test_opponents_bypassed_corridor() -> None:
    opponents = [(45, 34), (50, 60), (55, 40), (30, 34), (65, 34)]
    assert opponents_bypassed((40, 34), (60, 34), opponents) == 2
    assert opponents_bypassed((60, 34), (40, 34), opponents) == 0  # backward pass


def test_rhythm_and_control_index(meta: MatchMetadata) -> None:
    events = ended(
        [
            ev("pass", "HAR", 30, 30, end=(40, 30), t=0),
            ev("pass", "HAR", 40, 30, end=(50, 30), t=5),
            ev("pass", "HAR", 50, 30, end=(60, 30), t=10, outcome="incomplete"),
            ev("interception", "KES", *kes(45, 38), t=11),
            ev("possession_change", "KES", *kes(45, 38), t=11),
            ev("pass", "KES", *kes(45, 38), end=kes(50, 38), t=15),
            ev("possession_change", "HAR", 60, 30, t=20),
            ev("carry", "HAR", 60, 30, end=(65, 30), t=21),
        ],
        t=60.0,
    )
    events_meta = meta.model_copy(update={"period_lengths_s": {1: 60.0, 2: 2700.0}})
    s = compute_signals(events, events_meta)
    assert s.value("sequence_length", "HAR", None, 1) == 2.0  # runs of 3 and 1
    assert s.value("sequence_length", "KES", None, 1) == 1.0
    assert s.value("turnovers_per_min", "HAR", None, 1) == 1.0  # lost once in one minute
    assert s.value("control_index", "HAR", None, 1) == pytest.approx(100 * 2 / (2 + 3 * 1), 0.01)
    # Match level: mean run 5/3, per-team turnover rate (1 + 1) / 2 per minute.
    assert s.value("control_index", None, None, 1) == pytest.approx(
        100 * (5 / 3) / (5 / 3 + 3 * 1), abs=0.05)  # fmt: skip


def test_pass_network(meta: MatchMetadata) -> None:
    events = [
        ev("pass", "HAR", 30, 20, end=(40, 20), t=1, player_id="HAR-4", related_player_id="HAR-8"),
        ev("pass", "HAR", 40, 20, end=(30, 20), t=2, player_id="HAR-8", related_player_id="HAR-4"),
        ev("pass", "HAR", 30, 20, end=(60, 10), t=3, player_id="HAR-4", related_player_id="HAR-11"),
    ]
    net = pass_network(events, meta, "HAR", 1)
    assert [(e.a, e.b, e.passes) for e in net.edges] == [
        ("HAR-4", "HAR-8", 2), ("HAR-11", "HAR-4", 1)]  # fmt: skip
    node = {n.player_id: n for n in net.nodes}["HAR-4"]
    assert (node.x, node.y, node.touches) == (30.0, 20.0, 3)


# ----- snapshot signals -----------------------------------------------------


def _snap(t: float, har: list[tuple[str, float, float]], kes_: list[tuple[str, float, float]]):
    players = [PlayerPosition(player_id=p, x=x, y=y) for p, x, y in har + kes_]
    return Snapshot(
        match_id="m-test", period=1, t=t, players=players, ball=BallPosition(x=50, y=34)
    )


def test_defensive_line_width_and_sprints(meta: MatchMetadata) -> None:
    events_meta = meta.model_copy(update={"period_lengths_s": {1: 10.0, 2: 2700.0}})
    events = ended([ev("pass", "HAR", 50, 34, end=(55, 34), t=0)], t=10.0)  # HAR in possession
    kes_line = [("KES-2", 80, 10), ("KES-3", 82, 30), ("KES-4", 84, 40), ("KES-5", 86, 60),
                ("KES-8", 60, 34)]  # fmt: skip
    har_xs = [0, 8, 16, 24, 24, 24, 32, 32, 32, 32, 32]  # HAR-7 sprints twice
    snaps = []
    for t, x7 in enumerate(har_xs):
        har = [("HAR-7", float(x7), 34.0), ("HAR-2", 30, 5), ("HAR-3", 30, 60), ("HAR-4", 25, 30),
               ("HAR-5", 25, 40), ("HAR-1", 5, 34)]  # fmt: skip
        snaps.append(_snap(float(t), har, kes_line))
    snaps[7].players[0] = PlayerPosition(player_id="HAR-7", x=40.0, y=34.0)
    s = compute_signals(events, events_meta, snaps)
    # KES is out of possession: deepest four in KES frame are 19, 21, 23, 25.
    assert s.value("def_line_height", "KES", None, 1) == 22.0
    assert s.value("def_line_height", "HAR", None, 1) is None  # HAR always in possession
    assert s.value("team_width", "KES", None, 1) == 50.0  # GK excluded for HAR, not needed here
    assert s.value("team_width", "HAR", None, 1) == 55.0  # HAR-1 (GK) excluded
    assert s.value("sprints", "HAR", "n7", 1) == 2
    assert s.value("top_speed", "HAR", "n7", 1) == 28.8


# ----- live prefixes --------------------------------------------------------


def test_until_emits_only_complete_windows(meta: MatchMetadata) -> None:
    events = [ev("pass", "HAR", 60, 30, end=(75, 10), t=float(t)) for t in range(0, 2800, 100)]
    s = compute_signals(ended(events), meta, until=(1, 1000.0))
    assert len(s.window_values("channel_entries", "HAR", "left", 1)) == 3  # w0-w2 complete
    assert s.value("channel_entries", "HAR", "left", 1) == 11  # t = 0..1000 inclusive
    full = compute_signals(ended(events), meta)
    assert len(full.window_values("channel_entries", "HAR", "left", 1)) == 9


# ----- the demo match -------------------------------------------------------


@pytest.fixture(scope="module")
def demo():
    folder = DATA / "m-001"
    return compute_signals(
        read_events(folder / "events.jsonl"),
        read_metadata(folder / "metadata.json"),
        read_snapshots(folder / "snapshots.jsonl.gz"),
    )


def test_signal_ids_are_unique_and_readable(demo) -> None:
    ids = [s.signal_id for s in demo]
    assert len(ids) == len(set(ids)) > 1000
    assert all(re.fullmatch(r"sig-[a-z_]+(-[A-Za-z0-9_]+)*", i) for i in ids)


def test_demo_harbour_left_overload_first_half(demo) -> None:
    assert demo.value("channel_share", "HAR", "left", 1) >= 50
    left = demo.value("channel_entries", "HAR", "left", 1)
    assert left >= 2 * demo.value("channel_entries", "HAR", "centre", 1)
    assert left >= 2 * demo.value("channel_entries", "HAR", "right", 1)


def test_demo_kestrel_low_turnover_height(demo) -> None:
    assert demo.value("turnover_height", "KES", None, 1) < demo.value(
        "turnover_height", "HAR", None, 1) - 20  # fmt: skip
    assert demo.value("high_regains", "HAR", None, 1) >= 5 * demo.value(
        "high_regains", "KES", None, 1)  # fmt: skip
    assert demo.value("ppda", "HAR", None, 1) < demo.value("ppda", "KES", None, 1)


def test_demo_second_half_left_entries_drop(demo) -> None:
    first = demo.window_values("channel_entries", "HAR", "left", 1)
    second = demo.window_values("channel_entries", "HAR", "left", 2)
    assert len(first) == len(second) == 9
    assert sum(second) / len(second) <= 0.7 * sum(first) / len(first)
