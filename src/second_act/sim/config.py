"""Generator configuration: squads, tactics, phases and substitutions (YAML-backed)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from second_act.sim.formations import FORMATIONS

CONFIG_DIR = Path(__file__).parent / "configs"


class SquadPlayer(BaseModel):
    """A player definition in teams.yaml."""

    model_config = ConfigDict(extra="forbid")

    number: int = Field(ge=1, le=99)
    name: str
    position: str = Field(pattern=r"^(GK|DF|MF|FW)$")
    pace_kmh: float = Field(ge=20, le=37, description="Top running speed.")


class TeamDef(BaseModel):
    """A fictional club with its squad and default lineup."""

    model_config = ConfigDict(extra="forbid")

    name: str
    short_name: str
    primary_colour: str
    secondary_colour: str
    manager: str
    default_formation: str
    default_lineup: dict[str, int] = Field(description="Formation role -> shirt number.")
    squad: list[SquadPlayer]


class Tactics(BaseModel):
    """Behaviour parameters for one team in one phase.

    All probabilities and weights are dimensionless 0-1 unless noted.
    """

    model_config = ConfigDict(extra="forbid")

    press_height: float = Field(ge=0, le=1, description="How far up the pitch the team presses.")
    press_intensity: float = Field(ge=0, le=1, description="Chance of pressing when in range.")
    def_line: float = Field(ge=12, le=45, description="Back-line x (m) with the ball at halfway.")
    width: float = Field(ge=0.6, le=1.3, description="Lateral spread of the shape.")
    tempo: float = Field(ge=0, le=1, description="Directness and speed of play.")
    channel_weights: dict[str, float] = Field(
        description="Preferred final-third entry channel: left, centre, right."
    )
    buildup_security: float = Field(ge=0, le=1, description="Press resistance in possession.")
    wide_block: float = Field(ge=0, le=0.9, description="Resistance to wide entries.")
    central_block: float = Field(ge=0, le=0.9, description="Resistance to central entries.")
    finishing: float = Field(default=1.0, ge=0.3, le=2.0)
    pivot_drop: float = Field(default=0.0, ge=0, le=15, description="Metres the DM drops deeper.")

    @model_validator(mode="after")
    def _check_channels(self) -> Tactics:
        if set(self.channel_weights) != {"left", "centre", "right"}:
            raise ValueError("channel_weights needs exactly left, centre and right")
        if abs(sum(self.channel_weights.values()) - 1.0) > 1e-6:
            raise ValueError("channel_weights must sum to 1")
        return self


class MatchTime(BaseModel):
    """A point in match time; period 2 minutes start at 45."""

    model_config = ConfigDict(extra="forbid")

    period: int = Field(ge=1, le=2)
    minute: int = Field(ge=0, le=95)

    def clock_seconds(self) -> int:
        """Return the match clock in seconds."""
        return self.minute * 60


class Phase(BaseModel):
    """A change of tactics, formation or role assignment from a given time."""

    model_config = ConfigDict(extra="forbid")

    start: MatchTime
    label: str = ""
    formation: str | None = None
    lineup: dict[str, int] | None = None
    tactics: dict[str, object] = Field(default_factory=dict)


class Substitution(BaseModel):
    """A player change; the incoming player takes the outgoing player's role."""

    model_config = ConfigDict(extra="forbid")

    at: MatchTime
    player_off: int
    player_on: int


class TeamMatchConfig(BaseModel):
    """One team's plan for a match."""

    model_config = ConfigDict(extra="forbid")

    formation: str | None = None
    lineup: dict[str, int] | None = None
    tactics: Tactics
    phases: list[Phase] = Field(default_factory=list)
    substitutions: list[Substitution] = Field(default_factory=list)


class MatchConfig(BaseModel):
    """A full match definition."""

    model_config = ConfigDict(extra="forbid")

    match_id: str
    title: str
    description: str = ""
    seed: int
    home: str
    away: str
    venue: str
    kickoff: str
    stoppage_s: dict[int, float] = Field(default_factory=lambda: {1: 120.0, 2: 240.0})
    snapshots: bool = True
    teams: dict[str, TeamMatchConfig]

    @model_validator(mode="after")
    def _check(self) -> MatchConfig:
        if set(self.teams) != {self.home, self.away}:
            raise ValueError("teams must contain exactly the home and away codes")
        for code, team in self.teams.items():
            formations = [team.formation, *(p.formation for p in team.phases)]
            for f in formations:
                if f is not None and f not in FORMATIONS:
                    raise ValueError(f"{code}: unknown formation {f}")
        return self


def load_teams(path: Path | None = None) -> dict[str, TeamDef]:
    """Load club definitions from teams.yaml."""
    data = yaml.safe_load((path or CONFIG_DIR / "teams.yaml").read_text())
    return {code: TeamDef.model_validate(body) for code, body in data.items()}


def load_match_config(path: Path) -> MatchConfig:
    """Load a match config YAML file."""
    return MatchConfig.model_validate(yaml.safe_load(path.read_text()))


def match_config_paths() -> list[Path]:
    """Return all bundled match configs, sorted by file name."""
    return sorted((CONFIG_DIR / "matches").glob("*.yaml"))
