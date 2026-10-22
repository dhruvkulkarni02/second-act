"""Player position snapshots at roughly 1 Hz, in the same raw frame as events."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from second_act.schema.events import PITCH_LENGTH, PITCH_WIDTH


class PlayerPosition(BaseModel):
    """One player's location at a snapshot instant."""

    model_config = ConfigDict(extra="forbid")

    player_id: str
    x: float = Field(ge=0, le=PITCH_LENGTH)
    y: float = Field(ge=0, le=PITCH_WIDTH)


class BallPosition(BaseModel):
    """Ball location at a snapshot instant."""

    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=PITCH_LENGTH)
    y: float = Field(ge=0, le=PITCH_WIDTH)


class Snapshot(BaseModel):
    """All on-pitch players and the ball at time ``t`` seconds into ``period``."""

    model_config = ConfigDict(extra="forbid")

    match_id: str
    period: int = Field(ge=1, le=2)
    t: float = Field(ge=0)
    players: list[PlayerPosition]
    ball: BallPosition
