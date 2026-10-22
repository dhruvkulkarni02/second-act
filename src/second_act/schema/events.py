"""Match event schema.

Coordinates are in metres on a 105 x 68 pitch in the raw broadcast frame: the
home team attacks toward x = 105 in period 1 and toward x = 0 in period 2.
Analytics normalize to each team's attacking direction (see ``MatchMetadata``).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

PITCH_LENGTH = 105.0
PITCH_WIDTH = 68.0


class EventType(StrEnum):
    """Every event type the generator emits."""

    PASS = "pass"
    CARRY = "carry"
    SHOT = "shot"
    TACKLE = "tackle"
    INTERCEPTION = "interception"
    PRESSURE = "pressure"
    CLEARANCE = "clearance"
    FOUL = "foul"
    CARD = "card"
    SUBSTITUTION = "substitution"
    POSSESSION_CHANGE = "possession_change"
    GOAL = "goal"
    PERIOD_START = "period_start"
    PERIOD_END = "period_end"


# Allowed outcome values per event type. Types missing here must have no outcome.
OUTCOMES: dict[EventType, set[str]] = {
    EventType.PASS: {"complete", "incomplete", "out"},
    EventType.CARRY: {"complete", "dispossessed"},
    EventType.SHOT: {"goal", "saved", "off_target", "blocked"},
    EventType.TACKLE: {"won", "foul"},
    EventType.INTERCEPTION: {"won"},
    EventType.CARD: {"yellow", "red"},
    # Extension: whether the ball was won in open play or awarded at a dead ball.
    EventType.POSSESSION_CHANGE: {"open_play", "dead_ball"},
}

# Types that describe ball movement and therefore need an end location.
NEEDS_END = {EventType.PASS, EventType.CARRY, EventType.SHOT}

# Types that are not tied to a single player.
TEAMLESS = {EventType.PERIOD_START, EventType.PERIOD_END}


class Event(BaseModel):
    """A single on-pitch event, one line of a match JSONL file."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    event_id: str = Field(pattern=r"^e-\d{6}$")
    match_id: str
    period: int = Field(ge=1, le=2)
    minute: int = Field(ge=0, le=130)
    second: int = Field(ge=0, le=59)
    type: EventType
    team: str | None = None
    player_id: str | None = None
    x: float | None = Field(default=None, ge=0, le=PITCH_LENGTH)
    y: float | None = Field(default=None, ge=0, le=PITCH_WIDTH)
    end_x: float | None = Field(default=None, ge=0, le=PITCH_LENGTH)
    end_y: float | None = Field(default=None, ge=0, le=PITCH_WIDTH)
    outcome: str | None = None
    under_pressure: bool = False
    pressure_player_id: str | None = None
    speed_kmh: float | None = Field(default=None, ge=0, le=200)
    body_part: str | None = Field(default=None, pattern=r"^(foot|head|other)$")
    # Extensions beyond the PRD example, all optional.
    sequence_id: int | None = Field(default=None, ge=0)
    related_player_id: str | None = None
    t: float | None = Field(default=None, ge=0, description="Seconds since period start.")

    @model_validator(mode="after")
    def _check_consistency(self) -> Event:
        allowed = OUTCOMES.get(self.type)
        if allowed is None and self.outcome is not None:
            raise ValueError(f"{self.type} events take no outcome, got {self.outcome!r}")
        if allowed is not None and self.outcome not in allowed:
            raise ValueError(f"{self.type} outcome must be one of {sorted(allowed)}")
        if self.type not in TEAMLESS and self.team is None:
            raise ValueError(f"{self.type} events need a team")
        if self.type in NEEDS_END and (self.end_x is None or self.end_y is None):
            raise ValueError(f"{self.type} events need end_x and end_y")
        if self.type not in TEAMLESS and (self.x is None or self.y is None):
            raise ValueError(f"{self.type} events need x and y")
        if self.pressure_player_id is not None and not self.under_pressure:
            raise ValueError("pressure_player_id set but under_pressure is false")
        if self.type is EventType.SUBSTITUTION and self.related_player_id is None:
            raise ValueError("substitution events need related_player_id (player coming on)")
        return self

    @property
    def clock_seconds(self) -> int:
        """Match clock in seconds (period 2 starts at 45:00)."""
        return self.minute * 60 + self.second
