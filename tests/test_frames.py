"""Team-frame normalization, thirds and channels."""

from __future__ import annotations

from second_act.analytics.frames import channel_of, enters_final_third, third_of, to_team_frame


def test_rotation_is_identity_when_attacking_high_x() -> None:
    assert to_team_frame(10.0, 5.0, True) == (10.0, 5.0)


def test_rotation_keeps_left_on_the_left() -> None:
    # A team attacking toward x = 0 has its left at high raw y.
    x, y = to_team_frame(10.0, 60.0, False)
    assert (x, y) == (95.0, 8.0)
    assert third_of(x) == "final"
    assert channel_of(y) == "left"


def test_rotation_is_an_involution() -> None:
    assert to_team_frame(*to_team_frame(33.3, 44.4, False), False) == (33.3, 44.4)


def test_thirds_and_channels_boundaries() -> None:
    assert [third_of(x) for x in (0, 34.9, 35, 69.9, 70, 105)] == [
        "defensive",
        "defensive",
        "middle",
        "middle",
        "final",
        "final",
    ]
    assert [channel_of(y) for y in (0, 22.6, 22.7, 45.3, 45.4, 68)] == [
        "left",
        "left",
        "centre",
        "centre",
        "right",
        "right",
    ]
    assert enters_final_third(65, 72) and not enters_final_third(72, 80)
