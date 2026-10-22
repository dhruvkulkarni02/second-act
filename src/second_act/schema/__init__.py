"""Pydantic data contracts shared by every stage of the pipeline.

Events, position snapshots and match metadata describe the synthetic input;
signals, predictions and cues describe what the agents produce from it.
"""

from second_act.schema.cues import Cue, CueKind
from second_act.schema.events import (
    PITCH_LENGTH,
    PITCH_WIDTH,
    Event,
    EventType,
)
from second_act.schema.io import (
    read_events,
    read_metadata,
    read_snapshots,
    write_events,
    write_metadata,
    write_snapshots,
)
from second_act.schema.metadata import MatchMetadata, Player, TeamInfo
from second_act.schema.predictions import Prediction, PredictionStatus
from second_act.schema.signals import Signal
from second_act.schema.snapshots import BallPosition, PlayerPosition, Snapshot

__all__ = [
    "PITCH_LENGTH",
    "PITCH_WIDTH",
    "BallPosition",
    "Cue",
    "CueKind",
    "Event",
    "EventType",
    "MatchMetadata",
    "Player",
    "PlayerPosition",
    "Prediction",
    "PredictionStatus",
    "Signal",
    "Snapshot",
    "TeamInfo",
    "read_events",
    "read_metadata",
    "read_snapshots",
    "write_events",
    "write_metadata",
    "write_snapshots",
]
