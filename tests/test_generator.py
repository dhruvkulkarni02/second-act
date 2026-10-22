"""Generator: determinism, schema validity of committed data, and the demo story."""

from __future__ import annotations

import gzip
import json
import math
from collections import Counter

import pytest

from second_act.schema import EventType
from second_act.schema.io import read_events, read_metadata, read_snapshots
from second_act.sim import load_match_config, load_teams, simulate
from second_act.sim.config import CONFIG_DIR, Tactics, match_config_paths
from second_act.sim.positions import generate_snapshots
from second_act.sim.story import check_demo_story, summarize
from tests.conftest import DATA, MATCH_IDS


def _config(match_id: str):
    return load_match_config(CONFIG_DIR / "matches" / f"{match_id}.yaml")


def _event_lines(events) -> list[str]:
    return [e.model_dump_json(exclude_none=True) for e in events]


def test_bundled_configs_cover_three_matches() -> None:
    assert [p.stem for p in match_config_paths()] == MATCH_IDS
    assert len({_config(m).seed for m in MATCH_IDS}) == 3


def test_same_seed_same_match() -> None:
    teams, config = load_teams(), _config("m-001")
    assert _event_lines(simulate(config, teams).events) == _event_lines(
        simulate(config, teams).events
    )


def test_different_seed_different_match() -> None:
    teams, config = load_teams(), _config("m-001")
    other = config.model_copy(update={"seed": config.seed + 1})
    assert _event_lines(simulate(config, teams).events) != _event_lines(
        simulate(other, teams).events
    )


@pytest.mark.parametrize("match_id", MATCH_IDS)
def test_committed_data_matches_generator(match_id: str) -> None:
    """The files in data/ are exactly what the generator produces today."""
    result = simulate(_config(match_id), load_teams())
    committed = (DATA / match_id / "events.jsonl").read_text().splitlines()
    assert committed == _event_lines(result.events)
    meta = json.loads((DATA / match_id / "metadata.json").read_text())
    assert meta == json.loads(result.metadata.model_dump_json())


@pytest.mark.parametrize("match_id", MATCH_IDS)
def test_committed_data_validates(match_id: str) -> None:
    folder = DATA / match_id
    events = read_events(folder / "events.jsonl")
    meta = read_metadata(folder / "metadata.json")
    snaps = read_snapshots(folder / "snapshots.jsonl.gz")
    assert len(events) > 1500
    ids = [e.event_id for e in events]
    assert len(set(ids)) == len(ids)
    squad = {p.player_id for t in (meta.home, meta.away) for p in t.squad}
    assert {e.player_id for e in events if e.player_id} <= squad
    assert [e.type for e in events if e.type in (EventType.PERIOD_START, EventType.PERIOD_END)] == [
        EventType.PERIOD_START,
        EventType.PERIOD_END,
        EventType.PERIOD_START,
        EventType.PERIOD_END,
    ]
    keys = [(e.period, e.t) for e in events]
    assert keys == sorted(keys), "events must be in time order"
    assert all(len(s.players) == 22 for s in snaps)
    for period in (1, 2):
        n = sum(s.period == period for s in snaps)
        assert n == int(meta.period_lengths_s[period]) + 1


def test_snapshot_file_is_reproducible() -> None:
    result = simulate(_config("m-003"), load_teams())
    regenerated = [s.model_dump_json() for s in generate_snapshots(result)]
    with gzip.open(DATA / "m-003" / "snapshots.jsonl.gz", "rt") as fh:
        assert fh.read().splitlines() == regenerated


def test_snapshot_speeds_respect_player_pace() -> None:
    teams = load_teams()
    pace = {f"{c}-{p.number}": p.pace_kmh for c, d in teams.items() for p in d.squad}
    snaps = read_snapshots(DATA / "m-001" / "snapshots.jsonl.gz")
    prev: dict[str, tuple[float, float]] = {}
    prev_period = 0
    for snap in snaps:
        cur = {p.player_id: (p.x, p.y) for p in snap.players}
        if snap.period == prev_period:
            for pid, xy in cur.items():
                if pid in prev:
                    assert math.dist(xy, prev[pid]) * 3.6 <= pace[pid] + 1.0
        prev, prev_period = cur, snap.period


def test_substitutes_appear_in_snapshots() -> None:
    events = read_events(DATA / "m-001" / "events.jsonl")
    snaps = read_snapshots(DATA / "m-001" / "snapshots.jsonl.gz")
    last = {p.player_id for p in snaps[-1].players}
    for sub in (e for e in events if e.type is EventType.SUBSTITUTION):
        assert sub.related_player_id in last and sub.player_id not in last


def test_demo_story_present_in_raw_events() -> None:
    events = read_events(DATA / "m-001" / "events.jsonl")
    report = check_demo_story(events, read_metadata(DATA / "m-001" / "metadata.json"))
    assert report.passed, str(report)


def test_kestrel_halftime_change_is_in_config_not_metadata() -> None:
    meta = read_metadata(DATA / "m-001" / "metadata.json")
    assert meta.away.formation == "4-3-3"
    assert _config("m-001").teams["KES"].phases[0].formation == "5-3-2"


def test_channel_preference_drives_entries() -> None:
    """Flipping a team's channel weights flips where its entries go, across seeds."""
    teams, base = load_teams(), _config("m-003")
    left: Counter[str] = Counter()
    right: Counter[str] = Counter()
    for seed in range(5):
        for weights, tally in (
            ({"left": 0.7, "centre": 0.15, "right": 0.15}, left),
            ({"left": 0.15, "centre": 0.15, "right": 0.7}, right),
        ):
            har = base.teams["HAR"]
            tactics = Tactics.model_validate(
                har.tactics.model_dump() | {"channel_weights": weights}
            )
            teams_cfg = base.teams | {"HAR": har.model_copy(update={"tactics": tactics})}
            config = base.model_copy(update={"seed": seed, "teams": teams_cfg})
            result = simulate(config, teams)
            for (code, _), half in summarize(result.events, result.metadata).items():
                if code == "HAR":
                    tally.update(half.entries)
    assert left["left"] > 2 * left["right"]
    assert right["right"] > 2 * right["left"]


def test_control_matches_differ_from_demo() -> None:
    """The control matches do not reproduce the demo's left overload."""

    def har_first_half(match_id: str):
        events = read_events(DATA / match_id / "events.jsonl")
        return summarize(events, read_metadata(DATA / match_id / "metadata.json"))[("HAR", 1)]

    demo = har_first_half("m-001")
    for match_id in ("m-002", "m-003"):
        har = har_first_half(match_id)
        share = har.entries["left"] / max(sum(har.entries.values()), 1)
        assert har.entries["left"] < 0.6 * demo.entries["left"]
        assert share < 0.5
