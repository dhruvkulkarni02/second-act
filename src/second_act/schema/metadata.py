"""Match metadata: teams, squads, starting formations, kickoff and venue.

Metadata holds only what a broadcaster would know before kickoff. Tactical
parameters and planned changes stay in the generator config, so analytics and
agents cannot read the script.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Player(BaseModel):
    """A squad member."""

    model_config = ConfigDict(extra="forbid")

    player_id: str = Field(pattern=r"^[A-Z]{3}-\d{1,2}$")
    name: str
    shirt_number: int = Field(ge=1, le=99)
    position: str = Field(description="Broad role: GK, DF, MF or FW.")
    starter: bool


class TeamInfo(BaseModel):
    """A fictional club and its matchday squad."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[A-Z]{3}$")
    name: str
    short_name: str
    primary_colour: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    secondary_colour: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    formation: str = Field(description="Starting formation, for example '4-3-3'.")
    manager: str
    squad: list[Player]

    @model_validator(mode="after")
    def _check_squad(self) -> TeamInfo:
        starters = [p for p in self.squad if p.starter]
        if len(starters) != 11:
            raise ValueError(f"{self.code} needs 11 starters, has {len(starters)}")
        if sum(p.position == "GK" for p in starters) != 1:
            raise ValueError(f"{self.code} needs exactly one starting goalkeeper")
        ids = [p.player_id for p in self.squad]
        if len(set(ids)) != len(ids):
            raise ValueError(f"{self.code} has duplicate player ids")
        if any(not pid.startswith(self.code + "-") for pid in ids):
            raise ValueError(f"{self.code} player ids must start with the team code")
        return self


class MatchMetadata(BaseModel):
    """Everything about a match that is not an event."""

    model_config = ConfigDict(extra="forbid")

    match_id: str
    title: str
    home: TeamInfo
    away: TeamInfo
    venue: str
    kickoff: str = Field(description="ISO 8601 kickoff time (fictional).")
    seed: int
    period_lengths_s: dict[int, float] = Field(
        description="Actual length of each period in seconds, including stoppage time."
    )
    attacking_toward_high_x: dict[int, str] = Field(
        description="Per period, the team code attacking toward x = 105."
    )
    has_snapshots: bool = True

    def team(self, code: str) -> TeamInfo:
        """Return the team with the given code."""
        for team in (self.home, self.away):
            if team.code == code:
                return team
        raise KeyError(code)

    def opponent(self, code: str) -> str:
        """Return the code of the other team."""
        return self.away.code if code == self.home.code else self.home.code
