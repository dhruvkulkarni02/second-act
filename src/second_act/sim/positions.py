"""1 Hz player position snapshots derived from the engine's ball track and shapes.

Each second, every player has a target: their slot in the team shape for the
current ball position and possession state, except the ball holder (on the
ball), the intended receiver of a ball in flight (its landing point) and the
nearest defender (closing the ball down). Players move toward their target
with a lag, capped at their personal top speed, so sprints emerge from
transitions and long balls rather than being scripted.
"""

from __future__ import annotations

import bisect
import math
import random

from second_act.analytics.frames import from_team_frame, to_team_frame
from second_act.schema.events import PITCH_LENGTH, PITCH_WIDTH
from second_act.schema.snapshots import BallPosition, PlayerPosition, Snapshot
from second_act.sim.engine import BallKey, ShapeChange, SimResult
from second_act.sim.formations import clamp, shape_targets

LAG = 0.3  # share of the remaining distance a player covers per second
CHASE_LAG = 0.7  # holder, receiver and presser close in faster
PRESS_RADIUS = 18.0


def _ball_at(keys: list[BallKey], times: list[float], t: float) -> tuple[float, float, int]:
    """Interpolated raw ball position at ``t`` and the index of the last key at or before it."""
    i = max(0, bisect.bisect_right(times, t) - 1)
    k0 = keys[i]
    if i + 1 >= len(keys) or keys[i + 1].t <= k0.t:
        return k0.x, k0.y, i
    k1 = keys[i + 1]
    a = clamp((t - k0.t) / (k1.t - k0.t), 0.0, 1.0)
    return k0.x + a * (k1.x - k0.x), k0.y + a * (k1.y - k0.y), i


def _shape_at(log: list[ShapeChange], team: str, period: int, t: float) -> ShapeChange:
    current = None
    for change in log:
        if change.team == team and (change.period, change.t) <= (period, t):
            current = change
    assert current is not None, f"no shape for {team}"
    return current


def generate_snapshots(result: SimResult, hz: float = 1.0) -> list[Snapshot]:
    """Simulate player positions for both periods at ``hz`` samples per second."""
    meta = result.metadata
    rng = random.Random(result.config.seed * 7919 + 17)
    snapshots: list[Snapshot] = []
    codes = (meta.home.code, meta.away.code)
    dt = 1.0 / hz
    for period in (1, 2):
        keys = [k for k in result.ball_track if k.period == period]
        times = [k.t for k in keys]
        changes = sorted(
            (c for c in result.shape_log if c.period == period and c.t > 0), key=lambda c: c.t
        )
        shapes = {code: _shape_at(result.shape_log, code, period, 0.0) for code in codes}
        positions: dict[str, tuple[float, float]] = {}
        n = int(meta.period_lengths_s[period] * hz) + 1
        for step in range(n):
            t = step * dt
            while changes and changes[0].t <= t:
                change = changes.pop(0)
                old, new = shapes[change.team].lineup, change.lineup
                for role, pid in new.items():
                    gone = old.get(role)
                    if pid not in positions and gone in positions and gone not in new.values():
                        positions[pid] = positions.pop(gone)
                shapes[change.team] = change
            bx, by, i = _ball_at(keys, times, t)
            key = keys[i]
            nxt = keys[i + 1] if i + 1 < len(keys) else None
            in_flight = nxt is not None and nxt.holder != key.holder and nxt.t > t
            targets: dict[str, tuple[float, float]] = {}
            fast: set[str] = set()
            for code in codes:
                high = meta.attacking_toward_high_x[period] == code
                shape = shapes[code]
                tx = shape_targets(
                    shape.formation,
                    shape.lineup,
                    def_line=shape.tactics.def_line,
                    width=shape.tactics.width,
                    pivot_drop=shape.tactics.pivot_drop,
                    ball=to_team_frame(bx, by, high),
                    in_possession=key.team == code,
                )
                for pid, (x, y) in tx.items():
                    targets[pid] = from_team_frame(x, y, high)
            if key.holder in targets and not in_flight:
                targets[key.holder] = (bx, by)
                fast.add(key.holder)
            if in_flight and nxt is not None and nxt.holder in targets:
                targets[nxt.holder] = (nxt.x, nxt.y)
                fast.add(nxt.holder)
            defending = [c for c in codes if c != key.team]
            if defending and positions:
                d_ids = [p for p in shapes[defending[0]].lineup.values() if p in positions]
                if d_ids:
                    presser = min(d_ids, key=lambda p: math.dist(positions[p], (bx, by)))
                    if math.dist(positions[presser], (bx, by)) < PRESS_RADIUS:
                        targets[presser] = (bx, by)
                        fast.add(presser)
            players = []
            for code in codes:
                for pid in shapes[code].lineup.values():
                    tx, ty = targets[pid]
                    if pid not in fast:
                        tx, ty = tx + rng.gauss(0, 0.6), ty + rng.gauss(0, 0.6)
                    if pid not in positions:
                        positions[pid] = (tx, ty)
                    px, py = positions[pid]
                    dx, dy = tx - px, ty - py
                    dist = math.hypot(dx, dy)
                    vmax = result.pace_kmh[pid] / 3.6 * dt
                    move = min(dist * (CHASE_LAG if pid in fast else LAG), vmax)
                    if dist > 1e-9:
                        px, py = px + dx / dist * move, py + dy / dist * move
                    px, py = clamp(px, 0.0, PITCH_LENGTH), clamp(py, 0.0, PITCH_WIDTH)
                    positions[pid] = (px, py)
                    players.append(PlayerPosition(player_id=pid, x=round(px, 1), y=round(py, 1)))
            snapshots.append(
                Snapshot(
                    match_id=meta.match_id,
                    period=period,
                    t=round(t, 2),
                    players=players,
                    ball=BallPosition(
                        x=round(clamp(bx, 0, PITCH_LENGTH), 1),
                        y=round(clamp(by, 0, PITCH_WIDTH), 1),
                    ),
                )
            )
    return snapshots
