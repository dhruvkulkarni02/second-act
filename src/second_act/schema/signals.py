"""Computed signal values with stable, citable IDs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Signal(BaseModel):
    """One computed value in the shared match state.

    IDs look like ``sig-channel_entries-HAR-left-p1w4``: metric, team, optional
    qualifier, and scope (``p1`` for a whole half, ``p1w4`` for its fifth window).
    """

    model_config = ConfigDict(extra="forbid")

    signal_id: str = Field(pattern=r"^sig-[a-z0-9_]+(-[A-Za-z0-9_]+)*$")
    metric: str
    value: float
    unit: str
    team: str | None = None
    qualifier: str | None = None
    period: int | None = Field(default=None, ge=1, le=2)
    window: int | None = Field(default=None, ge=0)
    scope: str = Field(description="'half' or 'window'.")
