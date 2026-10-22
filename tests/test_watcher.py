"""Watcher: prediction evaluation rules, stability, explanations in every variant."""

from __future__ import annotations

import asyncio

import pytest

from second_act.agents.verifier import check_numbers
from second_act.agents.watcher import (
    Evaluation,
    Watcher,
    evaluate,
    explanation_phrase,
    final_notes,
    next_status,
    verdict_clash,
)
from second_act.analytics.signals import SignalSet
from second_act.i18n.catalog import AUDIENCES, LANGUAGES
from second_act.schema.io import read_metadata
from second_act.schema.predictions import Prediction, PredictionStatus
from second_act.schema.signals import Signal
from tests.conftest import DATA

META = read_metadata(DATA / "m-001" / "metadata.json")


def pred(direction="decrease", threshold=30, after_minute=65) -> Prediction:
    return Prediction(
        prediction_id="p-KES-1",
        team="KES",
        claim="Kestrel cut Harbour's left",
        metric="channel_entries",
        subject_team="HAR",
        scope={"channel": "left"},
        comparison="second_half_window_mean_vs_first_half_mean",
        direction=direction,
        threshold_pct=threshold,
        evaluate_after_minute=after_minute,
    )


def signals(before: float, after: float, windows: int = 4) -> SignalSet:
    def sig(metric, period, value, window=None):
        sid = f"sig-{metric}-HAR-left-p{period}" + (f"w{window}" if window is not None else "")
        return Signal(
            signal_id=sid,
            metric=metric,
            value=value,
            unit="count",
            team="HAR",
            qualifier="left",
            period=period,
            window=window,
            scope="window" if window is not None else "half",
        )

    items = [sig("channel_entries_wmean", 1, before), sig("channel_entries_wmean", 2, after)]
    items += [sig("channel_entries", 1, before, w) for w in range(9)]
    items += [sig("channel_entries", 2, after, w) for w in range(windows)]
    return SignalSet(items)


@pytest.mark.parametrize(
    ("before", "after", "status"),
    [
        (2.67, 1.5, PredictionStatus.CONFIRMED),  # -44% vs -30%
        (2.67, 2.6, PredictionStatus.CONTRADICTED),  # -3%: under half the threshold
        (2.67, 3.2, PredictionStatus.CONTRADICTED),  # wrong direction
        (2.67, 2.2, PredictionStatus.OPEN),  # -18%: borderline
    ],
)
def test_evaluation_rules(before, after, status) -> None:
    ev = evaluate(pred(), signals(before, after), minute=66)
    assert ev is not None and ev.status is status
    assert ev.evidence == [
        "sig-channel_entries_wmean-HAR-left-p1",
        "sig-channel_entries_wmean-HAR-left-p2",
    ]


def test_no_verdict_before_minute_or_with_too_few_windows() -> None:
    assert evaluate(pred(), signals(2.67, 1.5), minute=60) is None
    assert evaluate(pred(), signals(2.67, 1.5, windows=2), minute=70) is None


def test_full_time_decides_open_predictions() -> None:
    ev = evaluate(pred(), signals(2.67, 2.2, windows=2), minute=95, final=True)
    assert ev is not None and ev.status is PredictionStatus.CONTRADICTED


def test_stability_rule() -> None:
    open_ev = Evaluation("p", PredictionStatus.OPEN, 1, 1, 0, 4, ["a", "b"])
    contra = Evaluation("p", PredictionStatus.CONTRADICTED, 1, 1, 0, 4, ["a", "b"])
    assert next_status(PredictionStatus.CONFIRMED, open_ev) is PredictionStatus.CONFIRMED
    assert next_status(PredictionStatus.CONFIRMED, contra) is PredictionStatus.CONTRADICTED
    assert next_status(PredictionStatus.OPEN, open_ev) is PredictionStatus.OPEN


@pytest.mark.parametrize(
    ("after", "key"), [(1.5, "x_confirmed"), (2.6, "x_short"), (3.2, "x_opposite")]
)
def test_explanations_are_grounded_in_every_variant(after, key) -> None:
    p = pred()
    ev = evaluate(p, signals(2.67, after), minute=66, final=True)
    assert ev is not None
    phrase = explanation_phrase(p, ev, META)
    assert phrase.key == key
    cited = dict(zip(ev.evidence, (ev.before, ev.after), strict=True))
    for audience in AUDIENCES:
        for language in LANGUAGES:
            text = phrase.render(audience, language)
            assert not check_numbers(
                text, cited, "x", [p.threshold_pct], decimal_comma=language == "es"
            ), text
    assert "2,67" in phrase.render("analyst", "es")


class LyingExplainer:
    async def explain(self, pred, ev, meta):
        return "Kestrel's plan is holding nicely.", 0.1, {}


def test_model_explanation_that_contradicts_the_verdict_is_replaced() -> None:
    p = pred()
    watcher = Watcher(LyingExplainer())
    _, changes = asyncio.run(watcher.check([p], signals(2.67, 3.2), META, 70))
    [change] = changes
    assert change.prediction.status is PredictionStatus.CONTRADICTED
    assert change.source == "template" and "opposite" in change.explanation
    assert verdict_clash("It is holding", PredictionStatus.CONTRADICTED) == "holding"


def test_final_notes_use_full_time_values() -> None:
    p = pred().model_copy(update={"status": PredictionStatus.CONFIRMED})
    notes = final_notes([p], signals(2.67, 1.56, windows=9), META)
    text, evidence = notes["p-KES-1"]
    assert "1.56" in text and evidence[1].endswith("-p2")
