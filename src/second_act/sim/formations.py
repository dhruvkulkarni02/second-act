"""Formation templates and the team-shape model.

Slot coordinates are in the team frame (attacking toward x = 105) for a mid
block with the back line at x = 25. ``shape_targets`` moves and scales that
template from the ball position, possession state and tactics.
"""

from __future__ import annotations

from collections.abc import Mapping

from second_act.schema.events import PITCH_LENGTH, PITCH_WIDTH

Slot = tuple[float, float]

FORMATIONS: dict[str, dict[str, Slot]] = {
    "4-3-3": {
        "GK": (5, 34),
        "LB": (29, 8),
        "LCB": (25, 24),
        "RCB": (25, 44),
        "RB": (29, 60),
        "DM": (38, 34),
        "LCM": (49, 22),
        "RCM": (49, 46),
        "LW": (66, 8),
        "ST": (71, 34),
        "RW": (66, 60),
    },
    "4-2-3-1": {
        "GK": (5, 34),
        "LB": (29, 8),
        "LCB": (25, 24),
        "RCB": (25, 44),
        "RB": (29, 60),
        "LDM": (39, 26),
        "RDM": (39, 42),
        "LW": (61, 10),
        "AM": (60, 34),
        "RW": (61, 58),
        "ST": (72, 34),
    },
    "4-4-2": {
        "GK": (5, 34),
        "LB": (29, 8),
        "LCB": (25, 24),
        "RCB": (25, 44),
        "RB": (29, 60),
        "LM": (50, 9),
        "LCM": (45, 26),
        "RCM": (45, 42),
        "RM": (50, 59),
        "LST": (69, 27),
        "RST": (69, 41),
    },
    "5-3-2": {
        "GK": (5, 34),
        "LWB": (33, 6),
        "LCB": (24, 19),
        "CB": (22, 34),
        "RCB": (24, 49),
        "RWB": (33, 62),
        "LCM": (45, 21),
        "DM": (38, 34),
        "RCM": (45, 47),
        "LST": (66, 27),
        "RST": (66, 41),
    },
}

# Extra resistance to wide final-third entries from a back five.
BACK_FIVE_WIDE_BONUS = 0.45


def back_line_size(formation: str) -> int:
    """Number of outfield defenders in the formation string's first group."""
    return int(formation.split("-")[0])


def clamp(value: float, low: float, high: float) -> float:
    """Clamp value into [low, high]."""
    return max(low, min(high, value))


def shape_targets(
    formation: str,
    lineup: Mapping[str, str],
    *,
    def_line: float,
    width: float,
    pivot_drop: float,
    ball: tuple[float, float],
    in_possession: bool,
) -> dict[str, tuple[float, float]]:
    """Return each player's target position (team frame) for the given ball position.

    ``lineup`` maps formation role to player id. The back line follows the ball
    up and down the pitch; the block is longer and wider in possession.
    """
    bx, by = ball
    line = clamp(def_line + 0.38 * (bx - PITCH_LENGTH / 2), 8.0, 62.0)
    depth = 1.0 if in_possession else 0.78
    spread = width * (1.0 if in_possession else 0.72)
    slots = FORMATIONS[formation]
    out: dict[str, tuple[float, float]] = {}
    for role, player_id in lineup.items():
        sx, sy = slots[role]
        if role == "GK":
            x = clamp(4.0 + 0.1 * bx, 2.0, 16.0)
            y = clamp(34.0 + 0.12 * (by - 34.0), 28.0, 40.0)
        else:
            if role in ("DM", "LDM", "RDM"):
                sx -= pivot_drop
            x = line + (sx - 25.0) * depth
            y = 34.0 + (sy - 34.0) * spread + 0.25 * (by - 34.0)
        out[player_id] = (clamp(x, 0.5, PITCH_LENGTH - 0.5), clamp(y, 0.5, PITCH_WIDTH - 0.5))
    return out
