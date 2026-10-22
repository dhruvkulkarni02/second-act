"""End-to-end offline replay: the M4 done criterion."""

from __future__ import annotations

from second_act.schema.predictions import PredictionStatus


def test_full_replay_produces_the_complete_stream(finished) -> None:
    runner, messages = finished
    kinds = [k for k, _ in messages]
    assert kinds[0] == "match" and kinds[-1] == "end"
    assert kinds.count("event") == len(runner.events)
    assert kinds.count("score") == 2
    for kind in ("briefing", "recap", "predictions", "cue"):
        assert kind in kinds
    assert kinds.index("briefing") < kinds.index("recap")


def test_at_least_one_prediction_confirmed_and_one_contradicted(finished) -> None:
    statuses = {p.status for p in finished[0].state.predictions}
    assert PredictionStatus.CONFIRMED in statuses
    assert PredictionStatus.CONTRADICTED in statuses
    assert PredictionStatus.OPEN not in statuses  # full time decides every prediction


def test_verified_recap_and_all_variants(finished) -> None:
    runner, _ = finished
    outputs = runner.outputs
    assert outputs.briefing_status == "ready" and len(outputs.briefing) == 12
    assert outputs.recap_status == "ready" and len(outputs.recap) == 12
    assert runner.state.recap is not None and runner.state.recap.fallback_reason is None
