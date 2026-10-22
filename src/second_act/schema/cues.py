"""Overlay cue contract: the integration point for a broadcast graphics system."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from second_act.schema.predictions import PredictionStatus


class CueKind(StrEnum):
    """What a cue is about."""

    LIVE_TRIGGER = "live_trigger"
    PLAYER_TAG = "player_tag"
    HALFTIME_BRIEFING = "halftime_briefing"
    PREDICTION_CHECK = "prediction_check"
    FULLTIME_RECAP = "fulltime_recap"


class Cue(BaseModel):
    """A timed overlay card in one audience and language variant."""

    model_config = ConfigDict(extra="forbid")

    cue_id: str = Field(pattern=r"^c-\d{4,}$")
    match_time: str = Field(pattern=r"^\d{1,3}:\d{2}$")
    duration_s: float = Field(gt=0)
    kind: CueKind
    audience: Literal["analyst", "casual"]
    language: Literal["en", "es"]
    club_focus: str | None = None
    headline: str
    body: str
    evidence: list[str] = Field(default_factory=list)
    prediction_id: str | None = None
    status: PredictionStatus | None = None
