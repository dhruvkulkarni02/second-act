"""Full-time recap: how the tactical battle played out and which predictions held."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from second_act.schema.briefing import Point


class PredictionReview(BaseModel):
    """What became of one half-time prediction."""

    model_config = ConfigDict(extra="forbid")

    prediction_id: str
    status: Literal["confirmed", "contradicted"]
    text: str
    evidence: list[str]


class RecapDraft(BaseModel):
    """What the tactician writes at full time."""

    model_config = ConfigDict(extra="forbid")

    headline: str
    headline_evidence: list[str]
    first_half: list[Point] = Field(description="The first-half story.")
    changes: list[Point] = Field(description="What changed at the break.")
    predictions: list[PredictionReview] = Field(description="Every half-time prediction.")
    result: list[Point] = Field(description="How the result came about.")


class Recap(RecapDraft):
    """A recap that has passed the verifier."""

    source: Literal["model", "template"]
    attempts: int = Field(ge=1)
    fallback_reason: str | None = None
    removed_claims: list[str] = Field(default_factory=list)
