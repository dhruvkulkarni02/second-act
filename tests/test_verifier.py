"""Verifier: deterministic grounding and prediction checks."""

from __future__ import annotations

import pytest

from second_act.agents.tactician import BriefingOut, template_briefing, to_draft
from second_act.agents.verifier import (
    check_briefing,
    check_numbers,
    check_point,
    check_prediction,
    matches,
    normalize_draft,
    normalize_evidence,
    numbers_in,
)
from second_act.schema.briefing import Point
from second_act.schema.io import read_events, read_metadata, read_snapshots
from second_act.state import MatchState
from tests.conftest import DATA, MATCH_IDS


@pytest.fixture(scope="module")
def state() -> MatchState:
    folder = DATA / "m-001"
    return MatchState.at_first_half_end(
        read_events(folder / "events.jsonl"),
        read_metadata(folder / "metadata.json"),
        read_snapshots(folder / "snapshots.jsonl.gz"),
    )


@pytest.fixture
def draft(state: MatchState):
    return template_briefing(state.signals, state.meta)


def test_number_extraction_ignores_ids_formations_and_shirts() -> None:
    text = "In a 4-3-3, #11 Kerrigan (HAR-11, sig-xg-HAR-p1) made 19 of 70.6% by the 55th, 1-0."
    assert numbers_in(text) == [19.0, 70.6, 55.0, 1.0, 0.0]


def test_number_matching_tolerance_and_rounding() -> None:
    assert matches(70.6, 70.6) and matches(71, 70.6) and matches(70.62, 70.6)
    assert matches(3, 3.1)  # whole-number rounding is allowed
    assert not matches(72, 70.6)
    assert not matches(4, 3.1)


def test_derived_numbers_are_allowed() -> None:
    cited = {"sig-turnover_height-HAR-p1": 54.8, "sig-turnover_height-KES-p1": 28.8}
    assert not check_numbers("Harbour win it 26 m higher", cited, "x")  # difference
    assert not check_numbers("about 1.9 times higher", cited, "x")  # ratio
    assert check_numbers("Harbour win it 30 m higher", cited, "x")


def test_template_briefings_pass_on_all_matches() -> None:
    for match_id in MATCH_IDS:
        folder = DATA / match_id
        st = MatchState.at_first_half_end(
            read_events(folder / "events.jsonl"), read_metadata(folder / "metadata.json")
        )
        report = check_briefing(template_briefing(st.signals, st.meta), st.signals, st.meta)
        assert report.ok, (match_id, report.reasons())


def test_verifier_rejects_an_invented_number(state: MatchState, draft) -> None:
    """M3 done criterion: a number not traceable to a cited signal is rejected."""
    point = draft.teams[0].working[0]
    point.text = point.text + " They also hit the woodwork 4 times."
    report = check_briefing(draft, state.signals, state.meta)
    assert not report.ok
    [issue] = report.issues
    assert issue.where == "HAR.working[0]"
    assert "number 4 does not match any cited signal" in issue.message


def test_verifier_rejects_a_wrong_value(state: MatchState) -> None:
    point = Point(text="Harbour's PPDA was 2.4.", evidence=["sig-ppda-HAR-p1"])
    [issue] = check_point(point, state.signals, "p")
    assert "2.4" in issue.message and "sig-ppda-HAR-p1=3.1" in issue.message


def test_verifier_rejects_unknown_and_missing_evidence(state: MatchState) -> None:
    unknown = Point(text="Harbour dominated.", evidence=["sig-dominance-HAR-p1"])
    assert "unknown signal ID" in check_point(unknown, state.signals, "p")[0].message
    missing = Point(text="Harbour dominated.", evidence=[])
    assert "no evidence" in check_point(missing, state.signals, "p")[0].message


def test_verifier_rejects_second_half_signals_that_do_not_exist_yet(state: MatchState) -> None:
    point = Point(text="Harbour's xG rose to 0.4.", evidence=["sig-xg-HAR-p2"])
    assert "unknown signal ID sig-xg-HAR-p2" in check_point(point, state.signals, "p")[0].message


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"metric": "goals_scored"}, "not evaluable"),
        ({"scope": {}}, "needs scope"),
        ({"scope": {"channel": "wide"}}, "needs scope"),
        ({"evaluate_after_minute": 50}, "evaluate_after_minute must be 60-85"),
        ({"threshold_pct": 95}, "threshold_pct must be 5-80"),
        ({"subject_team": "XYZ"}, "subject_team must be one of"),
        ({"prediction_id": "p-KES-1"}, "prediction_id must look like p-HAR-1"),
        ({"claim": "Kestrel cut the left by 55%"}, "number 55 does not match"),
    ],
)
def test_malformed_predictions_rejected(state: MatchState, draft, change: dict, message: str):
    pred = draft.teams[1].predictions[0]  # KES: cut Harbour's left-channel entries
    pred = pred.model_copy(
        update=change | ({} if "prediction_id" in change else {"prediction_id": "p-HAR-1"})
    )
    issues = check_prediction(pred, "HAR", state.meta, state.signals, "pred")
    assert any(message in i.message for i in issues), [str(i) for i in issues]


def test_prediction_with_zero_baseline_cannot_decrease(state: MatchState, draft) -> None:
    pred = (
        draft.teams[0]
        .predictions[0]
        .model_copy(update={"metric": "goals", "subject_team": "KES", "scope": {}})
    )
    issues = check_prediction(pred, "HAR", state.meta, state.signals, "pred")
    assert any("not evaluable" in i.message for i in issues)


def test_structure_checks(state: MatchState, draft) -> None:
    draft.teams[0].not_working = []
    draft.teams[1].predictions = draft.teams[1].predictions[:1]
    reasons = check_briefing(draft, state.signals, state.meta).reasons()
    assert "HAR: needs at least one point in not_working" in reasons
    assert "KES: needs 2-3 predictions" in reasons


def test_duplicate_prediction_ids_rejected(state: MatchState, draft) -> None:
    draft.teams[0].predictions[1] = (
        draft.teams[0]
        .predictions[1]
        .model_copy(update={"prediction_id": draft.teams[0].predictions[0].prediction_id})
    )
    reasons = check_briefing(draft, state.signals, state.meta).reasons()
    assert any("duplicate prediction_id" in r for r in reasons)


def test_evidence_normalization(state: MatchState, draft) -> None:
    assert normalize_evidence(["sig-xg-HAR-p1=1.68", " sig-xg-HAR-p1 ", "`sig-ppda-KES-p1`"]) == [
        "sig-xg-HAR-p1",
        "sig-ppda-KES-p1",
    ]
    draft.headline_evidence = [f"{e}=decorated" for e in draft.headline_evidence]
    assert check_briefing(normalize_draft(draft), state.signals, state.meta).ok


def test_model_output_conversion_maps_names_and_ids(state: MatchState, draft) -> None:
    data = draft.model_dump(mode="json")
    for read in data["teams"]:
        read["team"] = state.meta.team(read["team"]).name  # "Harbour Town"
        for p in read["predictions"]:
            p["channel"] = p.pop("scope").get("channel", "none")
            p["prediction_id"] = "whatever"
            for key in ("team", "comparison", "status"):
                p.pop(key)
    converted = to_draft(BriefingOut.model_validate(data), state.meta)
    assert [t.team for t in converted.teams] == ["HAR", "KES"]
    assert converted.teams[1].predictions[0].prediction_id == "p-KES-1"
    assert check_briefing(converted, state.signals, state.meta).ok


def test_number_words_are_checked() -> None:
    assert numbers_in("5 shots with three on target") == [5.0, 3.0]
    assert numbers_in("one of their best spells, per five minutes") == []  # not quantities
    assert numbers_in("dos goles cada cinco minutos", decimal_comma=True) == [2.0]
    cited = {"sig-shots-LEI-p1": 5}
    assert check_numbers("5 shots with three on target", cited, "x")  # "three" is not cited
    assert not check_numbers("5 shots, two of them saved", cited | {"sig-s-LEI-p1": 2}, "x")


def test_kept_quiet_needs_a_clean_sheet() -> None:
    from second_act.agents.tactician import _Ctx, _working
    from second_act.analytics.signals import SignalSet
    from second_act.schema.signals import Signal

    def sig(metric, team, value):
        return Signal(
            signal_id=f"sig-{metric}-{team}-p1",
            metric=metric,
            value=value,
            unit="count",
            team=team,
            period=1,
            scope="half",
        )

    base = [sig("xg", "HAR", 1.2), sig("xg", "KES", 0.3), sig("shots", "KES", 4)]
    meta = read_metadata(DATA / "m-001" / "metadata.json")

    def keys(goals_against):
        signals = SignalSet([*base, sig("goals", "KES", goals_against)])
        return [p.phrase.key for p in _working(_Ctx(signals, meta), "HAR", "KES")]

    assert "w_quiet" in keys(0)
    assert "w_quiet" not in keys(1)


def test_comparisons_must_cite_both_teams(state: MatchState) -> None:
    from second_act.agents.verifier import team_names

    names = team_names(state.meta)
    one_sided = Point(text="Harbour press far harder than Kestrel.", evidence=["sig-ppda-HAR-p1"])
    [issue] = check_point(one_sided, state.signals, "p", names)
    assert "compares against KES but cites no KES signal" in issue.message
    both = Point(
        text="Harbour press far harder than Kestrel.",
        evidence=["sig-ppda-HAR-p1", "sig-ppda-KES-p1"],
    )
    assert check_point(both, state.signals, "p", names) == []
    within_team = Point(
        text="Harbour: 24 entries on the left versus 4 on the right.",
        evidence=["sig-channel_entries-HAR-left-p1", "sig-channel_entries-HAR-right-p1"],
    )
    assert check_point(within_team, state.signals, "p", names) == []


def test_ppda_read_as_being_pressed_is_rejected(state: MatchState) -> None:
    wrong = Point(text="Kestrel are being pressed less (PPDA 11.2).", evidence=["sig-ppda-KES-p1"])
    [issue] = check_point(wrong, state.signals, "p")
    assert "not how hard it is pressed" in issue.message
    right = Point(text="Kestrel press lightly (PPDA 11.2).", evidence=["sig-ppda-KES-p1"])
    assert check_point(right, state.signals, "p") == []


def test_signal_ids_in_prose_are_stripped() -> None:
    from second_act.agents.verifier import strip_inline_ids

    text = "Harbour attack down the left: 70.6% of entries (sig-channel_share-HAR-left-p1)."
    assert strip_inline_ids(text) == "Harbour attack down the left: 70.6% of entries."
    assert strip_inline_ids("xG 0.75 vs 0.04 (sig-xg-HAR-p1, sig-xg-KES-p1)") == "xG 0.75 vs 0.04"


def test_own_regains_read_as_losing_the_ball_is_rejected(state: MatchState) -> None:
    wrong = Point(
        text="Kestrel lose the ball high only 3 times.", evidence=["sig-high_regains-KES-p1"]
    )
    [issue] = check_point(wrong, state.signals, "KES.not_working[0]")
    assert "counts KES winning the ball high" in issue.message
    right = Point(
        text="Kestrel keep losing the ball in their own third: 19 times.",
        evidence=["sig-high_regains-HAR-p1"],
    )
    assert check_point(right, state.signals, "KES.not_working[0]") == []
