"""Per-action models: shot quality (xG) and pass difficulty.

Both are fixed, documented formulas (see docs/formulas.md), independent of the
generator's internal probabilities.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from second_act.schema.events import PITCH_LENGTH, PITCH_WIDTH

GOAL_X, GOAL_Y, GOAL_WIDTH = PITCH_LENGTH, PITCH_WIDTH / 2, 7.32

# xG = sigmoid(XG_INTERCEPT + XG_DIST * distance + XG_ANGLE * angle) * (XG_HEADER if header)
XG_INTERCEPT, XG_DIST, XG_ANGLE, XG_HEADER = -1.25, -0.10, 1.25, 0.7

# Pass difficulty weights (sum to 1) and scales.
PD_WEIGHTS = {"distance": 0.30, "pressure": 0.20, "forward": 0.20, "bypass": 0.30}
PD_DIST_SCALE, PD_FORWARD_SCALE, PD_BYPASS_SCALE = 40.0, 30.0, 5.0
PD_CORRIDOR_M = 15.0
PROGRESSIVE_M = 10.0


def goal_angle(x: float, y: float) -> float:
    """Angle in radians subtended by the goal mouth from team-frame (x, y)."""
    dx, dy = GOAL_X - x, abs(GOAL_Y - y)
    return math.atan2(GOAL_WIDTH * dx, dx * dx + dy * dy - (GOAL_WIDTH / 2) ** 2) % math.pi


def distance_to_goal(x: float, y: float) -> float:
    """Distance in metres from team-frame (x, y) to the centre of the goal being attacked."""
    return math.dist((x, y), (GOAL_X, GOAL_Y))


def expected_goals(x: float, y: float, body_part: str | None = "foot") -> float:
    """Probability a shot from team-frame (x, y) is scored, from distance, angle and body part."""
    z = XG_INTERCEPT + XG_DIST * distance_to_goal(x, y) + XG_ANGLE * goal_angle(x, y)
    p = 1.0 / (1.0 + math.exp(-z))
    return p * (XG_HEADER if body_part == "head" else 1.0)


def is_progressive(x: float, y: float, end_x: float, end_y: float) -> bool:
    """True when a movement brings the ball at least 10 m closer to the opponent's goal."""
    return distance_to_goal(x, y) - distance_to_goal(end_x, end_y) >= PROGRESSIVE_M


def opponents_bypassed(
    start: tuple[float, float],
    end: tuple[float, float],
    opponents: Sequence[tuple[float, float]],
) -> int:
    """Opponents (passer's frame) the ball travels past: between the start and end x,
    and within 15 m of the pass line laterally."""
    (x0, y0), (x1, y1) = start, end
    if x1 <= x0:
        return 0
    count = 0
    for ox, oy in opponents:
        if x0 < ox < x1:
            line_y = y0 + (y1 - y0) * (ox - x0) / (x1 - x0)
            count += abs(oy - line_y) <= PD_CORRIDOR_M
    return count


def pass_difficulty(
    start: tuple[float, float],
    end: tuple[float, float],
    under_pressure: bool,
    bypassed: int | None = None,
) -> float:
    """Pass difficulty on a 0-100 scale.

    ``bypassed`` is the number of opponents the pass goes past (from position
    snapshots); without snapshots it is estimated as forward distance / 10 m.
    """
    dist = math.dist(start, end)
    forward = end[0] - start[0]
    if bypassed is None:
        bypassed = max(0, int(forward // 10))
    terms = {
        "distance": min(dist / PD_DIST_SCALE, 1.0),
        "pressure": 1.0 if under_pressure else 0.0,
        "forward": min(max(forward / PD_FORWARD_SCALE, 0.0), 1.0),
        "bypass": min(bypassed / PD_BYPASS_SCALE, 1.0),
    }
    return round(100 * sum(PD_WEIGHTS[k] * v for k, v in terms.items()), 1)
