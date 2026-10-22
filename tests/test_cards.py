"""Red cards and clinical finishing: signals, the live trigger and template rules."""

from __future__ import annotations

from second_act.agents.analyst import Analyst
from second_act.agents.tactician import template_briefing
from second_act.agents.verifier import check_briefing
from second_act.analytics import compute_signals
from second_act.schema.io import read_metadata
from tests.conftest import DATA
from tests.test_analytics import ended, ev

META = read_metadata(DATA / "m-001" / "metadata.json").model_copy(
    update={"period_lengths_s": {1: 2820.0, 2: 2820.0}}
)


def _with_red(period: int = 2, t: float = 500.0):
    return [
        ev("pass", "HAR", 50, 34, end=(55, 34), t=10, period=period),
        ev("card", "KES", 40, 30, t=t, period=period, outcome="red", player_id="KES-2"),
    ]


def test_players_on_pitch_counts_red_cards_across_halves() -> None:
    first = [ev("card", "KES", 40, 30, t=100, outcome="red", player_id="KES-2")]
    second = [ev("pass", "HAR", 50, 34, end=(55, 34), t=10, period=2)]
    events = [*ended(first), *ended(second, period=2, t=2820.0)]
    s = compute_signals(events, META)
    assert s.value("players_on_pitch", "KES", None, 1) == 10
    assert s.value("players_on_pitch", "KES", None, 2) == 10
    assert s.value("players_on_pitch", "HAR", None, 2) == 11


def test_red_card_trigger_fires_once_and_favours_the_opponent() -> None:
    names = {p.player_id: p.name for t in (META.home, META.away) for p in t.squad}
    analyst = Analyst(META, names)
    events = ended(_with_red(), period=2, t=2820.0)
    before = compute_signals(events, META, until=(2, 300.0))
    after = compute_signals(events, META, until=(2, 600.0))
    assert not [i for i in analyst.on_window(before, 2, 0, 50.0) if i.rule == "red_card"]
    [item] = [i for i in analyst.on_window(after, 2, 1, 55.0) if i.rule == "red_card"]
    assert item.team == "KES" and item.favours == "HAR"
    assert item.headline.render() == "Kestrel down to 10 men"
    analyst.last_trigger = -1e9
    assert not [i for i in analyst.on_window(after, 2, 2, 60.0) if i.rule == "red_card"]


def test_first_half_red_card_is_a_not_working_point() -> None:
    events = ended(
        [
            ev("card", "KES", 40, 30, t=100, outcome="red", player_id="KES-2"),
            ev("pass", "HAR", 50, 34, end=(55, 34), t=200),
        ]
    )
    s = compute_signals(events, META)
    draft = template_briefing(s, META)
    kes = next(t for t in draft.teams if t.team == "KES")
    assert kes.not_working[0].text == "Kestrel are down to 10 men after a red card."
    assert not [
        i for i in check_briefing(draft, s, META).issues if i.where.startswith("KES.not_working[0]")
    ]


def test_clinical_finishing_rule() -> None:
    """A goal from a long-range shot (low xG) makes the 'made their chance count' point."""
    events = ended(
        [
            ev("pass", "HAR", 50, 34, end=(55, 34), t=10),
            ev("shot", "KES", 105 - 75, 34, end=(0, 34), t=100, outcome="goal", player_id="KES-9"),
            ev("goal", "KES", 105 - 75, 34, t=100, player_id="KES-9"),
        ]
    )
    s = compute_signals(events, META)
    kes = next(t for t in template_briefing(s, META).teams if t.team == "KES")
    assert kes.working[0].text.startswith("Kestrel have made their chance count: a goal from")
