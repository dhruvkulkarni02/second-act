"""Event engine: turns tactics into a seeded stream of match events.

The engine is a possession-by-possession Markov model. Each on-ball action is
sampled from probabilities that depend on where the ball is and on both
teams' current tactics:

- the defending team pressures the ball more often inside its pressing range
  (``press_height``, ``press_intensity``);
- pressure lowers pass completion and raises dispossession, offset by the
  attacking team's ``buildup_security``;
- passes entering the final third pick a channel from ``channel_weights`` and
  succeed less often against a strong ``wide_block``/``central_block``; a back
  five adds wide cover;
- ``tempo`` sets directness, carry speed and time on the ball;
- shot conversion comes from distance, goal-mouth angle and body part.

All randomness comes from one ``random.Random(seed)``, so the same config and
seed always produce the same match.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from second_act.analytics.frames import (
    CHANNEL_EDGES,
    FINAL_THIRD_X,
    channel_of,
    enters_final_third,
    from_team_frame,
)
from second_act.schema.events import PITCH_LENGTH, PITCH_WIDTH, Event, EventType
from second_act.schema.metadata import MatchMetadata, Player, TeamInfo
from second_act.sim.config import MatchConfig, MatchTime, Substitution, Tactics, TeamDef
from second_act.sim.formations import (
    BACK_FIVE_WIDE_BONUS,
    FORMATIONS,
    back_line_size,
    clamp,
    shape_targets,
)

GOAL_X, GOAL_Y, GOAL_WIDTH = PITCH_LENGTH, PITCH_WIDTH / 2, 7.32
BOX_X, BOX_Y_LOW, BOX_Y_HIGH = PITCH_LENGTH - 16.5, 13.84, PITCH_WIDTH - 13.84
CHANNEL_RANGES = {
    "left": (3.0, CHANNEL_EDGES[0] - 1.0),
    "centre": (CHANNEL_EDGES[0] + 1.0, CHANNEL_EDGES[1] - 1.0),
    "right": (CHANNEL_EDGES[1] + 1.0, PITCH_WIDTH - 3.0),
}
CHANNEL_CENTRES = {name: (lo + hi) / 2 for name, (lo, hi) in CHANNEL_RANGES.items()}


@dataclass
class BallKey:
    """Ball location at an instant, in the raw frame, with who holds it."""

    period: int
    t: float
    x: float
    y: float
    holder: str | None
    team: str | None


@dataclass
class ShapeChange:
    """A team's formation, roles and tactics from time ``t`` of ``period``."""

    period: int
    t: float
    team: str
    formation: str
    lineup: dict[str, str]
    tactics: Tactics


@dataclass
class SimResult:
    """Everything the engine produced for one match."""

    config: MatchConfig
    metadata: MatchMetadata
    events: list[Event]
    ball_track: list[BallKey]
    shape_log: list[ShapeChange]
    pace_kmh: dict[str, float]


@dataclass
class _TeamState:
    code: str
    formation: str
    lineup: dict[str, str]  # role -> player id
    tactics: Tactics
    bench: list[str]
    booked: set[str] = field(default_factory=set)

    def role_of(self, player_id: str) -> str | None:
        for role, pid in self.lineup.items():
            if pid == player_id:
                return role
        return None


@dataclass
class _Ball:
    """Current possession: team, holder and ball location in that team's frame."""

    team: str
    player: str
    x: float
    y: float
    dead: bool = False
    after_cross: bool = False


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def goal_angle(x: float, y: float) -> float:
    """Angle (radians) subtended by the goal mouth from team-frame (x, y)."""
    dx, dy = GOAL_X - x, abs(GOAL_Y - y)
    return math.atan2(GOAL_WIDTH * dx, dx * dx + dy * dy - (GOAL_WIDTH / 2) ** 2) % math.pi


class _Simulator:
    def __init__(self, config: MatchConfig, teams: dict[str, TeamDef]) -> None:
        self.cfg = config
        self.defs = {code: teams[code] for code in (config.home, config.away)}
        self.rng = random.Random(config.seed)
        self.events: list[Event] = []
        self.ball_track: list[BallKey] = []
        self.shape_log: list[ShapeChange] = []
        self.period = 1
        self.t = 0.0
        self.seq = 0
        self.score = {config.home: 0, config.away: 0}
        self.period_lengths: dict[int, float] = {}
        self.state = {code: self._initial_state(code) for code in self.defs}
        self.changes = self._schedule_changes()

    # ----- setup -------------------------------------------------------------

    def _pid(self, code: str, number: int) -> str:
        if not any(p.number == number for p in self.defs[code].squad):
            raise ValueError(f"{code} has no player with shirt {number}")
        return f"{code}-{number}"

    def _initial_state(self, code: str) -> _TeamState:
        team_def, plan = self.defs[code], self.cfg.teams[code]
        formation = plan.formation or team_def.default_formation
        lineup_numbers = plan.lineup or team_def.default_lineup
        if set(lineup_numbers) != set(FORMATIONS[formation]):
            raise ValueError(f"{code}: lineup roles do not match {formation}")
        lineup = {role: self._pid(code, n) for role, n in lineup_numbers.items()}
        starters = set(lineup.values())
        bench = [
            f"{code}-{p.number}" for p in team_def.squad if f"{code}-{p.number}" not in starters
        ]
        return _TeamState(code, formation, lineup, plan.tactics, bench)

    def _schedule_changes(self) -> list[tuple[int, float, int, str, object]]:
        """Return (period, t, order, team, change) sorted by time; subs before phases."""
        items: list[tuple[int, float, int, str, object]] = []
        for code, plan in self.cfg.teams.items():
            for sub in plan.substitutions:
                items.append((*self._period_time(sub.at), 0, code, sub))
            for phase in plan.phases:
                items.append((*self._period_time(phase.start), 1, code, phase))
        return sorted(items, key=lambda item: (item[0], item[1], item[2], item[3]))

    @staticmethod
    def _period_time(at: MatchTime) -> tuple[int, float]:
        offset = 0 if at.period == 1 else 45 * 60
        return at.period, max(0.0, at.clock_seconds() - offset)

    # ----- frames and output -------------------------------------------------

    def attacks_high(self, code: str, period: int | None = None) -> bool:
        period = period or self.period
        return (code == self.cfg.home) == (period == 1)

    def opponent(self, code: str) -> str:
        return self.cfg.away if code == self.cfg.home else self.cfg.home

    def raw(self, code: str, x: float, y: float) -> tuple[float, float]:
        return from_team_frame(x, y, self.attacks_high(code))

    def emit(
        self,
        etype: EventType,
        team: str | None = None,
        player: str | None = None,
        at: tuple[float, float] | None = None,
        end: tuple[float, float] | None = None,
        **extra: object,
    ) -> None:
        """Append an event; ``at``/``end`` are in ``team``'s frame."""
        fields: dict[str, object] = {}
        if at is not None and team is not None:
            rx, ry = self.raw(team, *at)
            fields.update(
                x=round(clamp(rx, 0, PITCH_LENGTH), 2), y=round(clamp(ry, 0, PITCH_WIDTH), 2)
            )
        if end is not None and team is not None:
            ex, ey = self.raw(team, *end)
            fields.update(
                end_x=round(clamp(ex, 0, PITCH_LENGTH), 2),
                end_y=round(clamp(ey, 0, PITCH_WIDTH), 2),
            )
        offset = 0 if self.period == 1 else 45 * 60
        clock = int(self.t) + offset
        self.events.append(
            Event(
                event_id=f"e-{len(self.events) + 1:06d}",
                match_id=self.cfg.match_id,
                period=self.period,
                minute=clock // 60,
                second=clock % 60,
                type=etype,
                team=team,
                player_id=player,
                sequence_id=self.seq,
                t=round(self.t, 2),
                **fields,
                **extra,
            )
        )

    def key(
        self, code: str, x: float, y: float, holder: str | None, t: float | None = None
    ) -> None:
        rx, ry = self.raw(code, x, y)
        self.ball_track.append(
            BallKey(self.period, round(self.t if t is None else t, 2), rx, ry, holder, code)
        )

    def log_shape(self, code: str) -> None:
        st = self.state[code]
        self.shape_log.append(
            ShapeChange(self.period, self.t, code, st.formation, dict(st.lineup), st.tactics)
        )

    # ----- shape queries -----------------------------------------------------

    def positions(self, code: str, ball: tuple[float, float], in_possession: bool):
        st = self.state[code]
        return shape_targets(
            st.formation,
            st.lineup,
            def_line=st.tactics.def_line,
            width=st.tactics.width,
            pivot_drop=st.tactics.pivot_drop,
            ball=ball,
            in_possession=in_possession,
        )

    def nearest(
        self,
        code: str,
        point: tuple[float, float],
        ball: tuple[float, float],
        in_possession: bool,
        exclude: str | None = None,
    ) -> list[str]:
        """Players of ``code`` sorted by distance to ``point`` (all in ``code``'s frame)."""
        pos = self.positions(code, ball, in_possession)
        ranked = sorted(pos, key=lambda pid: math.dist(pos[pid], point))
        return [pid for pid in ranked if pid != exclude]

    def defender_near(self, code: str, point_att: tuple[float, float]) -> str:
        """Nearest player of the defending team to a point in the attacker's frame."""
        d = self.opponent(code)
        p = (PITCH_LENGTH - point_att[0], PITCH_WIDTH - point_att[1])
        return self.nearest(d, p, p, False)[0]

    def block(self, defending: str, channel: str) -> float:
        tac = self.state[defending].tactics
        if channel == "centre":
            return tac.central_block
        bonus = BACK_FIVE_WIDE_BONUS if back_line_size(self.state[defending].formation) >= 5 else 0
        return min(0.9, tac.wide_block + bonus)

    # ----- changes -----------------------------------------------------------

    def apply_due_changes(self) -> None:
        while self.changes and (
            self.changes[0][0] < self.period
            or (self.changes[0][0] == self.period and self.changes[0][1] <= self.t)
        ):
            _, _, _, code, change = self.changes.pop(0)
            st = self.state[code]
            if isinstance(change, Substitution):
                off, on = self._pid(code, change.player_off), self._pid(code, change.player_on)
                role = st.role_of(off)
                if role is None or on not in st.bench:
                    raise ValueError(f"{code}: invalid substitution {off}->{on}")
                st.lineup[role] = on
                st.bench.remove(on)
                self.emit(
                    EventType.SUBSTITUTION,
                    code,
                    off,
                    at=(PITCH_LENGTH / 2, 0.0),
                    related_player_id=on,
                )
            else:
                if change.formation:
                    st.formation = change.formation
                if change.lineup:
                    new = {role: self._pid(code, n) for role, n in change.lineup.items()}
                    if set(new) != set(FORMATIONS[st.formation]):
                        raise ValueError(f"{code}: phase lineup roles do not match {st.formation}")
                    if set(new.values()) != set(st.lineup.values()):
                        raise ValueError(f"{code}: phase lineup must use the players on the pitch")
                    st.lineup = new
                elif set(st.lineup) != set(FORMATIONS[st.formation]):
                    raise ValueError(f"{code}: formation change to {st.formation} needs a lineup")
                if change.tactics:
                    st.tactics = Tactics.model_validate(st.tactics.model_dump() | change.tactics)
            self.log_shape(code)

    # ----- possession helpers ------------------------------------------------

    def restart(self, code: str, x: float, y: float, delay: float, open_play: bool) -> _Ball:
        """Give ``code`` the ball at (x, y) in its frame after ``delay`` seconds."""
        self.seq += 1
        holder = self.nearest(code, (x, y), (x, y), True)[0]
        self.emit(
            EventType.POSSESSION_CHANGE,
            code,
            holder,
            at=(x, y),
            outcome="open_play" if open_play else "dead_ball",
        )
        self.key(code, x, y, holder)
        self.t += delay
        self.key(code, x, y, holder)
        return _Ball(code, holder, x, y, dead=not open_play)

    def turnover(self, att: str, point_att: tuple[float, float], open_play: bool, delay: float):
        d = self.opponent(att)
        return self.restart(
            d, PITCH_LENGTH - point_att[0], PITCH_WIDTH - point_att[1], delay, open_play
        )

    def hold_time(self, tac: Tactics) -> float:
        return 0.5 + (1.0 - tac.tempo) * 1.6 + self.rng.expovariate(1.0)

    # ----- actions -----------------------------------------------------------

    def step(self, ball: _Ball) -> _Ball:
        att, dfd = ball.team, self.opponent(ball.team)
        ta, td = self.state[att].tactics, self.state[dfd].tactics
        x, y = ball.x, ball.y
        is_gk = self.state[att].role_of(ball.player) == "GK"
        pressured, presser = False, None
        if not ball.dead:
            line = 25 + 55 * td.press_height
            engage = _sigmoid((line - x) / 7.0)
            near_box = 0.5 * _sigmoid((x - 86.0) / 4.0)
            if self.rng.random() < clamp(td.press_intensity * engage + near_box, 0, 0.92):
                pressured, presser = True, self.defender_near(att, (x, y))
                self.emit(EventType.PRESSURE, dfd, presser, at=(PITCH_LENGTH - x, PITCH_WIDTH - y))
                self.t += 0.4
                if self.rng.random() < 0.03 + 0.05 * td.press_intensity:
                    return self.foul(ball, presser)
        ball.dead = False

        in_box = x >= BOX_X and BOX_Y_LOW <= y <= BOX_Y_HIGH
        if is_gk:
            shot_p, carry_p = 0.0, 0.04
        else:
            if in_box:
                shot_p = 0.17
            elif x >= 78 and 20 <= y <= 48:
                shot_p = 0.09
            else:
                shot_p = 0.012 if x >= 72 else 0.0
            if pressured:
                shot_p *= 0.75
            carry_p = 0.22 + 0.06 * ta.tempo - (0.08 if pressured else 0.0)
        r = self.rng.random()
        pressure = {"under_pressure": True, "pressure_player_id": presser} if pressured else {}
        if r < shot_p:
            return self.shot(ball, pressured, pressure)
        if r < shot_p + carry_p:
            return self.carry(ball, pressured, presser, pressure)
        return self.pass_(ball, is_gk, pressured, pressure)

    def foul(self, ball: _Ball, fouler: str) -> _Ball:
        att, dfd = ball.team, self.opponent(ball.team)
        at_d = (PITCH_LENGTH - ball.x, PITCH_WIDTH - ball.y)
        self.emit(EventType.FOUL, dfd, fouler, at=at_d, related_player_id=ball.player)
        if fouler not in self.state[dfd].booked and self.rng.random() < 0.14:
            self.state[dfd].booked.add(fouler)
            self.emit(EventType.CARD, dfd, fouler, at=at_d, outcome="yellow")
        self.key(att, ball.x, ball.y, ball.player)
        self.t += self.rng.uniform(12, 25)
        self.key(att, ball.x, ball.y, ball.player)
        return _Ball(att, ball.player, ball.x, ball.y, dead=True)

    def _pull(self, tac: Tactics, x: float, y: float) -> float:
        if x < 40:
            return 0.0
        preferred = sum(w * CHANNEL_CENTRES[c] for c, w in tac.channel_weights.items())
        return 0.3 * (preferred - y)

    def pass_(self, ball: _Ball, is_gk: bool, pressured: bool, pressure: dict) -> _Ball:
        att = ball.team
        tac = self.state[att].tactics
        rng = self.rng
        x, y = ball.x, ball.y
        long_ball = False
        if is_gk:
            dx, dy = rng.gauss(26 + 16 * tac.tempo, 10), rng.gauss(0, 16)
            long_ball = dx > 30
        elif pressured and rng.random() < 0.28 * (1 - tac.buildup_security):
            dx, dy = rng.gauss(-7, 5), rng.gauss(0, 10)
        elif rng.random() < 0.05 + 0.10 * tac.tempo:
            dx, dy = rng.gauss(32, 8), rng.gauss(0, 15)
            long_ball = True
        else:
            dx, dy = rng.gauss(5 + 10 * tac.tempo, 8), rng.gauss(self._pull(tac, x, y), 12)
        ex, ey = clamp(x + dx, 1.0, PITCH_LENGTH - 1.0), clamp(y + dy, 1.0, PITCH_WIDTH - 1.0)
        entry = enters_final_third(x, ex)
        if entry:
            channel = rng.choices(
                list(tac.channel_weights), weights=list(tac.channel_weights.values())
            )[0]
            ey = rng.uniform(*CHANNEL_RANGES[channel])
        dist = math.dist((x, y), (ex, ey))

        completion = 1.03 - 0.0055 * dist
        if pressured:
            completion -= 0.30 * (1 - tac.buildup_security)
        if ex >= BOX_X:
            completion -= 0.26
        if long_ball:
            completion -= 0.12
        if entry:
            completion *= 1 - self.block(self.opponent(att), channel_of(ey))
        completion = clamp(completion, 0.08, 0.97)

        speed = clamp(24 + 1.5 * dist + rng.gauss(0, 5), 15, 110)
        flight = dist / (speed / 3.6)
        head = long_ball and rng.random() < 0.15
        common = dict(speed_kmh=round(speed, 1), body_part="head" if head else "foot", **pressure)
        cross = x > FINAL_THIRD_X and (y < 14 or y > PITCH_WIDTH - 14) and ex >= BOX_X

        if rng.random() < completion:
            mates = self.nearest(att, (ex, ey), (ex, ey), True, exclude=ball.player)
            receiver = rng.choices(mates[:3], weights=[0.75, 0.18, 0.07])[0]
            self.emit(
                EventType.PASS,
                att,
                ball.player,
                at=(x, y),
                end=(ex, ey),
                outcome="complete",
                related_player_id=receiver,
                **common,
            )
            self.key(att, x, y, ball.player)
            self.t += flight
            self.key(att, ex, ey, receiver)
            self.t += self.hold_time(tac)
            return _Ball(att, receiver, ex, ey, after_cross=cross)

        r = rng.random()
        if r < 0.22:
            ey_out = 0.0 if ey < PITCH_WIDTH / 2 else PITCH_WIDTH
            self.emit(
                EventType.PASS,
                att,
                ball.player,
                at=(x, y),
                end=(ex, ey_out),
                outcome="out",
                **common,
            )
            self.key(att, x, y, ball.player)
            self.t += flight
            return self.turnover(att, (ex, ey_out), open_play=False, delay=rng.uniform(8, 14))
        if ex >= BOX_X and r < 0.6:
            self.emit(
                EventType.PASS,
                att,
                ball.player,
                at=(x, y),
                end=(ex, ey),
                outcome="incomplete",
                **common,
            )
            self.key(att, x, y, ball.player)
            self.t += flight
            return self.clearance(att, (ex, ey))
        frac = rng.uniform(0.35, 0.95)
        point = (x + frac * (ex - x), y + frac * (ey - y))
        interceptor = self.defender_near(att, point)
        self.emit(
            EventType.PASS, att, ball.player, at=(x, y), end=point, outcome="incomplete", **common
        )
        self.key(att, x, y, ball.player)
        self.t += flight * frac
        dfd = self.opponent(att)
        point_d = (PITCH_LENGTH - point[0], PITCH_WIDTH - point[1])
        self.emit(EventType.INTERCEPTION, dfd, interceptor, at=point_d, outcome="won")
        return self.turnover(
            att, point, open_play=True, delay=self.hold_time(self.state[dfd].tactics)
        )

    def clearance(self, att: str, point_att: tuple[float, float]) -> _Ball:
        """Defending team clears a ball played into its box; the second ball is contested."""
        dfd = self.opponent(att)
        px, py = PITCH_LENGTH - point_att[0], PITCH_WIDTH - point_att[1]
        clearer = self.nearest(dfd, (px, py), (px, py), False)[0]
        land = (clamp(px + self.rng.gauss(35, 10), 20, 90), clamp(self.rng.uniform(5, 63), 1, 67))
        self.emit(EventType.CLEARANCE, dfd, clearer, at=(px, py), end=land, body_part="head")
        self.t += 2.0
        if self.rng.random() < 0.55:
            return self.restart(
                dfd, *land, delay=self.hold_time(self.state[dfd].tactics), open_play=True
            )
        back = (PITCH_LENGTH - land[0], PITCH_WIDTH - land[1])
        holder = self.nearest(att, back, back, True)[0]
        self.key(att, *back, holder)
        self.t += self.hold_time(self.state[att].tactics)
        return _Ball(att, holder, *back)

    def carry(self, ball: _Ball, pressured: bool, presser: str | None, pressure: dict) -> _Ball:
        att = ball.team
        tac = self.state[att].tactics
        rng = self.rng
        x, y = ball.x, ball.y
        dx = max(1.0, rng.gauss(4 + 7 * tac.tempo, 4))
        if tac.tempo > 0.6 and x < 60:
            dx += 4
        ex = clamp(x + dx, 1.0, PITCH_LENGTH - 1.0)
        ey = clamp(y + rng.gauss(self._pull(tac, x, y) * 0.6, 4), 1.0, PITCH_WIDTH - 1.0)
        entry = enters_final_third(x, ex)
        lose = 0.05 + (0.30 * (1 - tac.buildup_security) if pressured else 0.0)
        if entry:
            lose += 0.6 * self.block(self.opponent(att), channel_of(ey))
        if ex >= BOX_X:
            lose += 0.15
        pace = self.pace[ball.player]
        speed = clamp(rng.gauss(14 + 11 * tac.tempo, 3), 7, pace)
        dist = math.dist((x, y), (ex, ey))
        duration = dist / (speed / 3.6)

        if rng.random() < lose:
            frac = rng.uniform(0.3, 1.0)
            point = (x + frac * (ex - x), y + frac * (ey - y))
            tackler = presser or self.defender_near(att, point)
            dfd = self.opponent(att)
            point_d = (PITCH_LENGTH - point[0], PITCH_WIDTH - point[1])
            self.emit(
                EventType.CARRY,
                att,
                ball.player,
                at=(x, y),
                end=point,
                outcome="dispossessed",
                speed_kmh=round(speed, 1),
                **pressure,
            )
            self.key(att, x, y, ball.player)
            self.t += duration * frac
            self.key(att, *point, ball.player)
            if rng.random() < 0.15:
                self.emit(EventType.TACKLE, dfd, tackler, at=point_d, outcome="foul")
                return self.foul(_Ball(att, ball.player, *point), tackler)
            self.emit(EventType.TACKLE, dfd, tackler, at=point_d, outcome="won")
            return self.turnover(
                att, point, open_play=True, delay=self.hold_time(self.state[dfd].tactics)
            )

        self.emit(
            EventType.CARRY,
            att,
            ball.player,
            at=(x, y),
            end=(ex, ey),
            outcome="complete",
            speed_kmh=round(speed, 1),
            **pressure,
        )
        self.key(att, x, y, ball.player)
        self.t += duration
        self.key(att, ex, ey, ball.player)
        self.t += 0.3
        return _Ball(att, ball.player, ex, ey)

    def shot(self, ball: _Ball, pressured: bool, pressure: dict) -> _Ball:
        att, dfd = ball.team, self.opponent(ball.team)
        tac = self.state[att].tactics
        rng = self.rng
        x, y = ball.x, ball.y
        dist = math.dist((x, y), (GOAL_X, GOAL_Y))
        head = ball.after_cross and rng.random() < 0.6
        p_goal = _sigmoid(-1.9 - 0.11 * dist + 1.6 * goal_angle(x, y))
        p_goal *= (0.6 if head else 1.0) * tac.finishing * (0.8 if pressured else 1.0)
        speed = rng.gauss(50, 8) if head else rng.gauss(92, 15)
        speed = clamp(speed, 30, 128)
        r = rng.random()
        if r < p_goal:
            outcome, end = "goal", (GOAL_X, rng.uniform(30.8, 37.2))
        else:
            r2 = rng.random()
            if r2 < 0.28:
                outcome = "blocked"
                step = 2.0 / max(dist, 1.0)
                end = (x + (GOAL_X - x) * step, y + (GOAL_Y - y) * step)
            elif r2 < 0.60:
                outcome, end = "saved", (GOAL_X - 0.5, rng.uniform(30.5, 37.5))
            else:
                wide = rng.choice([rng.uniform(24, 30.2), rng.uniform(37.8, 44)])
                outcome, end = "off_target", (GOAL_X, wide)
        self.emit(
            EventType.SHOT,
            att,
            ball.player,
            at=(x, y),
            end=end,
            outcome=outcome,
            speed_kmh=round(speed, 1),
            body_part="head" if head else "foot",
            **pressure,
        )
        self.key(att, x, y, ball.player)
        self.t += math.dist((x, y), end) / (speed / 3.6)
        self.key(att, *end, None)

        if outcome == "goal":
            self.score[att] += 1
            self.emit(
                EventType.GOAL, att, ball.player, at=(x, y), body_part="head" if head else "foot"
            )
            return self.restart(dfd, PITCH_LENGTH / 2, PITCH_WIDTH / 2, rng.uniform(55, 80), False)
        if outcome == "off_target":
            return self.restart(dfd, 5.5, GOAL_Y + rng.uniform(-9, 9), rng.uniform(18, 28), False)
        if outcome == "saved":
            if rng.random() < 0.7:
                return self.restart(dfd, 5.0, GOAL_Y, rng.uniform(4, 8), False)
            loose = (GOAL_X - rng.uniform(8, 14), rng.uniform(22, 46))
        else:
            loose = end
        if rng.random() < 0.68:
            return self.turnover(
                att, loose, open_play=True, delay=self.hold_time(self.state[dfd].tactics)
            )
        holder = self.nearest(att, loose, loose, True)[0]
        self.key(att, *loose, holder)
        self.t += self.hold_time(tac)
        return _Ball(att, holder, *loose)

    # ----- match loop --------------------------------------------------------

    def run(self) -> SimResult:
        self.pace = {
            f"{code}-{p.number}": p.pace_kmh for code, d in self.defs.items() for p in d.squad
        }
        for code in self.state:
            self.log_shape(code)
        for period in (1, 2):
            self.period, self.t = period, 0.0
            length = 45 * 60 + self.cfg.stoppage_s.get(period, 0.0)
            self.emit(EventType.PERIOD_START)
            self.apply_due_changes()
            kickoff = self.cfg.home if period == 1 else self.cfg.away
            ball = self.restart(kickoff, PITCH_LENGTH / 2, PITCH_WIDTH / 2, 0.0, open_play=False)
            while self.t < length:
                self.apply_due_changes()
                ball = self.step(ball)
            self.emit(EventType.PERIOD_END)
            self.period_lengths[period] = round(self.t, 2)
        return SimResult(
            config=self.cfg,
            metadata=self.metadata(),
            events=self.events,
            ball_track=self.ball_track,
            shape_log=self.shape_log,
            pace_kmh=self.pace,
        )

    def metadata(self) -> MatchMetadata:
        def team_info(code: str) -> TeamInfo:
            d = self.defs[code]
            plan = self.cfg.teams[code]
            starters = set((plan.lineup or d.default_lineup).values())
            return TeamInfo(
                code=code,
                name=d.name,
                short_name=d.short_name,
                primary_colour=d.primary_colour,
                secondary_colour=d.secondary_colour,
                formation=plan.formation or d.default_formation,
                manager=d.manager,
                squad=[
                    Player(
                        player_id=f"{code}-{p.number}",
                        name=p.name,
                        shirt_number=p.number,
                        position=p.position,
                        starter=p.number in starters,
                    )
                    for p in d.squad
                ],
            )

        return MatchMetadata(
            match_id=self.cfg.match_id,
            title=self.cfg.title,
            home=team_info(self.cfg.home),
            away=team_info(self.cfg.away),
            venue=self.cfg.venue,
            kickoff=self.cfg.kickoff,
            seed=self.cfg.seed,
            period_lengths_s=self.period_lengths,
            attacking_toward_high_x={1: self.cfg.home, 2: self.cfg.away},
            has_snapshots=self.cfg.snapshots,
        )


def simulate(config: MatchConfig, teams: dict[str, TeamDef]) -> SimResult:
    """Simulate one match from its config and the club definitions."""
    return _Simulator(config, teams).run()
