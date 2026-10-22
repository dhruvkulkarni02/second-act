"""Schema contracts: the PRD examples validate and malformed records are rejected."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from second_act.schema import Cue, Event, Prediction, Signal

PRD_EVENT = {
    "event_id": "e-000412",
    "match_id": "m-001",
    "period": 1,
    "minute": 23,
    "second": 41,
    "type": "pass",
    "team": "HAR",
    "player_id": "HAR-11",
    "x": 38.2,
    "y": 12.5,
    "end_x": 61.0,
    "end_y": 9.8,
    "outcome": "complete",
    "under_pressure": True,
    "pressure_player_id": "KES-4",
    "speed_kmh": 54.0,
    "body_part": "foot",
}


def test_prd_event_example_validates() -> None:
    event = Event.model_validate(PRD_EVENT)
    assert event.clock_seconds == 23 * 60 + 41


@pytest.mark.parametrize(
    "change",
    [
        {"x": 106.0},
        {"y": -1.0},
        {"outcome": "great"},
        {"end_x": None},
        {"team": None},
        {"type": "pressure"},  # pressure events take no outcome
        {"under_pressure": False},  # pressure player without the flag
        {"event_id": "412"},
        {"body_part": "knee"},
    ],
)
def test_malformed_events_rejected(change: dict) -> None:
    with pytest.raises(ValidationError):
        Event.model_validate(PRD_EVENT | change)


def test_period_markers_need_no_team() -> None:
    Event(event_id="e-000001", match_id="m-001", period=1, minute=0, second=0, type="period_start")


def test_substitution_needs_incoming_player() -> None:
    base = {
        "event_id": "e-000002",
        "match_id": "m-001",
        "period": 2,
        "minute": 60,
        "second": 0,
        "type": "substitution",
        "team": "HAR",
        "player_id": "HAR-7",
        "x": 52.5,
        "y": 0.0,
    }
    with pytest.raises(ValidationError):
        Event.model_validate(base)
    Event.model_validate(base | {"related_player_id": "HAR-14"})


def test_prd_prediction_and_cue_examples_validate() -> None:
    Prediction.model_validate(
        {
            "prediction_id": "p-KES-2",
            "team": "KES",
            "claim": "Kestrel switch to a back five, reducing Harbour's left-side attacks",
            "metric": "channel_entries",
            "subject_team": "HAR",
            "scope": {"channel": "left"},
            "comparison": "second_half_window_mean_vs_first_half_mean",
            "direction": "decrease",
            "threshold_pct": 30,
            "evaluate_after_minute": 55,
            "status": "open",
        }
    )
    Cue.model_validate(
        {
            "cue_id": "c-0031",
            "match_time": "52:10",
            "duration_s": 8,
            "kind": "prediction_check",
            "audience": "casual",
            "language": "en",
            "club_focus": "KES",
            "headline": "The switch is working",
            "body": (
                "Kestrel's back five has cut Harbour's left-side attacks by half since the break."
            ),
            "evidence": ["sig-channel_entries-HAR-left-p2w2", "sig-channel_entries-HAR-left-p1"],
            "prediction_id": "p-KES-2",
            "status": "confirmed",
        }
    )


def test_signal_id_format() -> None:
    Signal(
        signal_id="sig-channel_entries-HAR-left-p1w4",
        metric="channel_entries",
        value=6,
        unit="count",
        team="HAR",
        qualifier="left",
        period=1,
        window=4,
        scope="window",
    )
    with pytest.raises(ValidationError):
        Signal(signal_id="channel entries", metric="x", value=1, unit="count", scope="half")
