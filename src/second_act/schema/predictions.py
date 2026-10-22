"""Measurable prediction tests written by the tactician and checked by the watcher."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PredictionStatus(StrEnum):
    """Lifecycle of a prediction during the second half."""

    OPEN = "open"
    CONFIRMED = "confirmed"
    CONTRADICTED = "contradicted"


class Prediction(BaseModel):
    """A predicted adjustment expressed as a deterministic test over signals."""

    model_config = ConfigDict(extra="forbid")

    prediction_id: str = Field(pattern=r"^p-[A-Z]{3}-\d+$")
    team: str
    claim: str
    metric: str
    subject_team: str
    scope: dict[str, str] = Field(default_factory=dict)
    comparison: Literal["second_half_window_mean_vs_first_half_mean"]
    direction: Literal["increase", "decrease"]
    threshold_pct: float = Field(gt=0, le=100)
    evaluate_after_minute: int = Field(ge=45, le=90)
    status: PredictionStatus = PredictionStatus.OPEN
    # Extension: first-half signals that motivate the prediction.
    evidence: list[str] = Field(default_factory=list)
