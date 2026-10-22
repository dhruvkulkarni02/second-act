"""Half-time briefing: what's working for each team and predicted adjustments."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from second_act.schema.predictions import Prediction


class Point(BaseModel):
    """One claim with the signal IDs that support it."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(description="One or two sentences. Every number must come from evidence.")
    evidence: list[str] = Field(description="Signal IDs, e.g. sig-ppda-HAR-p1.")


class TeamRead(BaseModel):
    """The tactical read for one team."""

    model_config = ConfigDict(extra="forbid")

    team: str
    working: list[Point]
    not_working: list[Point]
    predictions: list[Prediction]


class BriefingDraft(BaseModel):
    """What the tactician produces (and the model returns as structured output)."""

    model_config = ConfigDict(extra="forbid")

    headline: str = Field(description="One line for the half. Numbers must come from evidence.")
    headline_evidence: list[str]
    teams: list[TeamRead]


class Briefing(BriefingDraft):
    """A briefing that has passed the verifier, with how it was produced."""

    source: Literal["model", "template"]
    attempts: int = Field(ge=1, description="Drafts written, including rejected ones.")
    fallback_reason: str | None = None
    removed_claims: list[str] = Field(
        default_factory=list,
        description="Claims the verifier removed (with reasons) rather than reject the draft.",
    )
