"""Coordinate frames, thirds and channels.

The raw frame has the home team attacking toward x = 105 in period 1 and the
away team attacking toward x = 105 in period 2. A team frame rotates the pitch
180 degrees when needed so the team always attacks toward x = 105. Rotation
(not mirroring) keeps left and right consistent with the attacking direction:
in a team frame, left is y 0-22.67 and right is y 45.33-68.
"""

from __future__ import annotations

from typing import Literal

from second_act.schema.events import PITCH_LENGTH, PITCH_WIDTH

Third = Literal["defensive", "middle", "final"]
Channel = Literal["left", "centre", "right"]

FINAL_THIRD_X = PITCH_LENGTH * 2 / 3
DEFENSIVE_THIRD_X = PITCH_LENGTH / 3
CHANNEL_EDGES = (PITCH_WIDTH / 3, PITCH_WIDTH * 2 / 3)


def to_team_frame(x: float, y: float, attacks_high_x: bool) -> tuple[float, float]:
    """Convert raw coordinates to the frame of a team attacking toward x = 105."""
    if attacks_high_x:
        return x, y
    return PITCH_LENGTH - x, PITCH_WIDTH - y


# The rotation is its own inverse.
from_team_frame = to_team_frame


def third_of(x: float) -> Third:
    """Return the third containing team-frame x."""
    if x < DEFENSIVE_THIRD_X:
        return "defensive"
    if x < FINAL_THIRD_X:
        return "middle"
    return "final"


def channel_of(y: float) -> Channel:
    """Return the channel containing team-frame y."""
    if y < CHANNEL_EDGES[0]:
        return "left"
    if y < CHANNEL_EDGES[1]:
        return "centre"
    return "right"


def enters_final_third(x: float, end_x: float) -> bool:
    """True when a team-frame movement crosses into the final third."""
    return x < FINAL_THIRD_X <= end_x
