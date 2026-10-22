"""Compute every tactical signal with a stable, citable ID.

``compute_signals`` is a pure function of the events (and optional position
snapshots) seen so far, so the same code serves the live analyst (pass
``until``) and the half-time and full-time agents. Each signal is computed for
each complete five-minute window and cumulatively for each half; player-level
signals and shares are per half only. Formulas are documented in
docs/formulas.md.
"""

from __future__ import annotations

import bisect
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from second_act.analytics.frames import (
    FINAL_THIRD_X,
    Third,
    channel_of,
    enters_final_third,
    third_of,
    to_team_frame,
)
from second_act.analytics.models import (
    expected_goals,
    is_progressive,
    opponents_bypassed,
    pass_difficulty,
)
from second_act.analytics.windows import (
    WINDOW_S,
    WINDOWS_PER_PERIOD,
    NormEvent,
    attacks_high_x,
    normalize,
    period_time,
)
from second_act.schema.events import PITCH_LENGTH, Event, EventType
from second_act.schema.metadata import MatchMetadata
from second_act.schema.signals import Signal
from second_act.schema.snapshots import Snapshot

ON_BALL = (EventType.PASS, EventType.CARRY, EventType.SHOT)
CHANNELS = ("left", "centre", "right")
THIRDS: tuple[Third, ...] = ("defensive", "middle", "final")
MIRROR: dict[Third, Third] = {"defensive": "final", "middle": "middle", "final": "defensive"}
PPDA_OPP_ZONE_X = 0.6 * PITCH_LENGTH  # opponent's own defensive 60%
SPRINT_KMH = 25.0
FAST_SHOT_KMH = 100.0
DIFFICULT_PASS = 60.0
CONTROL_TURNOVER_WEIGHT = 3.0
MIN_PLAYER_PASSES = 10
MIN_NODE_TOUCHES = 5
TOP_PAIRS = 5

# Team-level metrics computed per window: the ones predictions can be tested on.
WINDOWED_METRICS = frozenset(
    {
        "channel_entries",
        "final_third_entries",
        "possession",
        "field_tilt",
        "ppda",
        "turnover_height",
        "high_regains",
        "progressive_passes",
        "shots",
        "xg",
        "pass_completion",
        "pass_difficulty",
        "sequence_length",
        "turnovers_per_min",
        "control_index",
        "def_line_height",
        "team_width",
    }
)
WMEAN_SUFFIX = "_wmean"

DIGITS = {
    "per_window": 2,
    "count": 0,
    "pct": 1,
    "m": 1,
    "ratio": 1,
    "xg": 2,
    "index": 1,
    "per_min": 2,
    "kmh": 1,
    "actions": 2,
    "rating": 1,
    "km": 2,
}


def make_id(
    metric: str,
    team: str | None = None,
    qualifier: str | None = None,
    period: int | None = None,
    window: int | None = None,
) -> str:
    """Build a signal ID such as ``sig-channel_entries-HAR-left-p1w4``."""
    parts = ["sig", metric]
    if team:
        parts.append(team)
    if qualifier:
        parts.append(qualifier)
    if period is not None:
        parts.append(f"p{period}" + (f"w{window}" if window is not None else ""))
    return "-".join(parts)


def player_qualifier(player_id: str) -> str:
    """``HAR-11`` -> ``n11`` (the shirt number, used as a signal qualifier)."""
    return "n" + player_id.split("-")[1]


class SignalSet:
    """An indexed, read-only collection of signals."""

    def __init__(self, signals: Iterable[Signal] = ()) -> None:
        self._by_id: dict[str, Signal] = {s.signal_id: s for s in signals}

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[Signal]:
        return iter(self._by_id.values())

    def __contains__(self, signal_id: object) -> bool:
        return signal_id in self._by_id

    def get(self, signal_id: str) -> Signal | None:
        """Return the signal with this ID, if computed."""
        return self._by_id.get(signal_id)

    def value(
        self,
        metric: str,
        team: str | None = None,
        qualifier: str | None = None,
        period: int | None = None,
        window: int | None = None,
    ) -> float | None:
        """Look up a value by its ID parts."""
        sig = self._by_id.get(make_id(metric, team, qualifier, period, window))
        return sig.value if sig else None

    def find(self, **fields: object) -> list[Signal]:
        """All signals whose fields equal the given values (e.g. ``metric="xg", period=1``)."""
        return [s for s in self if all(getattr(s, k) == v for k, v in fields.items())]

    def window_values(
        self, metric: str, team: str | None, qualifier: str | None, period: int
    ) -> list[float]:
        """Values of a windowed signal for a period, in window order (complete windows only)."""
        out = []
        for w in range(WINDOWS_PER_PERIOD):
            v = self.value(metric, team, qualifier, period, w)
            if v is not None:
                out.append(v)
        return out

    def to_list(self) -> list[Signal]:
        """All signals, sorted by ID."""
        return sorted(self, key=lambda s: s.signal_id)


@dataclass(frozen=True)
class _Scope:
    period: int
    window: int | None
    start: float
    end: float

    @property
    def minutes(self) -> float:
        return max((self.end - self.start) / 60.0, 1e-9)


class _Builder:
    def __init__(self) -> None:
        self.signals: list[Signal] = []

    def add(
        self,
        metric: str,
        value: float | None,
        unit: str,
        scope: _Scope,
        team: str | None = None,
        qualifier: str | None = None,
        player_id: str | None = None,
    ) -> None:
        if value is None or math.isnan(value):
            return
        self.signals.append(
            Signal(
                signal_id=make_id(metric, team, qualifier, scope.period, scope.window),
                metric=metric,
                value=round(float(value), DIGITS[unit]),
                unit=unit,
                team=team,
                qualifier=qualifier,
                player_id=player_id,
                period=scope.period,
                window=scope.window,
                scope="window" if scope.window is not None else "half",
            )
        )


def _share(part: float, whole: float) -> float | None:
    return 100.0 * part / whole if whole else None


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


# ----- snapshot index -------------------------------------------------------


class _Tracking:
    """Snapshots indexed by period and time, with the possession timeline."""

    def __init__(self, snapshots: Sequence[Snapshot], norm: Sequence[NormEvent]) -> None:
        self.snaps = list(snapshots)
        self.by_time: dict[tuple[int, int], Snapshot] = {
            (s.period, round(s.t)): s for s in self.snaps
        }
        poss = [
            (n.period, n.t, n.team)
            for n in norm
            if n.team and (n.event.type in ON_BALL or n.event.type is EventType.POSSESSION_CHANGE)
        ]
        self.poss_keys = [(p, t) for p, t, _ in poss]
        self.poss_team = [team for _, _, team in poss]
        self._speeds: dict[int, dict[str, list[tuple[float, float]]]] = {}

    def speeds(self, period: int) -> dict[str, list[tuple[float, float]]]:
        """Per player, (time, km/h) between consecutive snapshots of a period (cached)."""
        if period not in self._speeds:
            self._speeds[period] = _speeds(self.snaps, period)
        return self._speeds[period]

    def team_in_possession(self, period: int, t: float) -> str | None:
        i = bisect.bisect_right(self.poss_keys, (period, t)) - 1
        return self.poss_team[i] if i >= 0 and self.poss_keys[i][0] == period else None

    def at(self, period: int, t: float) -> Snapshot | None:
        return self.by_time.get((period, round(t)))


# ----- main entry point -----------------------------------------------------


def compute_signals(
    events: Sequence[Event],
    meta: MatchMetadata,
    snapshots: Sequence[Snapshot] | None = None,
    until: tuple[int, float] | None = None,
) -> SignalSet:
    """Compute all signals from events (and snapshots) up to ``until`` = (period, seconds).

    Windows are emitted only once complete; the half signal for a period in
    progress is cumulative so far.
    """
    if until is not None:
        events = [e for e in events if (e.period, period_time(e)) <= until]
        if snapshots is not None:
            snapshots = [s for s in snapshots if (s.period, s.t) <= until]
    norm = normalize(events, meta)
    tracking = _Tracking(snapshots, norm) if snapshots else None
    b = _Builder()
    gk = {p.player_id for t in (meta.home, meta.away) for p in t.squad if p.position == "GK"}

    for period in (1, 2):
        in_period = [n for n in norm if n.period == period]
        if not in_period:
            continue
        ended = any(n.event.type is EventType.PERIOD_END for n in in_period)
        if ended:
            elapsed = meta.period_lengths_s[period]
        elif until is not None and until[0] == period:
            elapsed = until[1]
        else:
            elapsed = max(n.t for n in in_period)
        half = _Scope(period, None, 0.0, elapsed)
        scopes = [half]
        for w in range(WINDOWS_PER_PERIOD):
            last = w == WINDOWS_PER_PERIOD - 1
            start, end = w * WINDOW_S, (elapsed if last else (w + 1) * WINDOW_S)
            if (last and ended) or (not last and end <= elapsed):
                scopes.append(_Scope(period, w, start, end))
        for scope in scopes:
            evs = [
                n
                for n in in_period
                if scope.start <= n.t < scope.end
                or (scope.window in (None, WINDOWS_PER_PERIOD - 1) and n.t >= scope.start)
            ]
            _event_signals(b, scope, evs, meta, tracking)
            if tracking is not None:
                _tracking_signals(b, scope, tracking, meta, gk)
        _half_only_signals(b, half, in_period, meta, tracking)
        _window_means(b, half)
    return SignalSet(b.signals)


def _window_means(b: _Builder, half: _Scope) -> None:
    """Mean over the half's complete windows of each windowed team metric.

    These are the before/after values prediction tests compare
    (``sig-channel_entries_wmean-HAR-left-p1`` vs ``...-p2``). Counts become a
    rate per five-minute window, so their unit is ``per_window``.
    """
    groups: dict[tuple[str, str | None, str | None], list[Signal]] = defaultdict(list)
    for sig in b.signals:
        if (
            sig.period == half.period
            and sig.window is not None
            and sig.player_id is None
            and sig.metric in WINDOWED_METRICS
        ):
            groups[(sig.metric, sig.team, sig.qualifier)].append(sig)
    for (metric, team, qualifier), sigs in sorted(groups.items(), key=lambda kv: str(kv[0])):
        unit = "per_window" if sigs[0].unit == "count" else sigs[0].unit
        b.add(
            metric + WMEAN_SUFFIX,
            sum(s.value for s in sigs) / len(sigs),
            unit,
            half,
            team,
            qualifier,
        )


# ----- event-based signals --------------------------------------------------


def _sequences(evs: Sequence[NormEvent]) -> list[tuple[str, int]]:
    """Runs of consecutive on-ball events by the same team: (team, length)."""
    runs: list[tuple[str, int]] = []
    for n in evs:
        if n.event.type not in ON_BALL or n.team is None:
            continue
        if runs and runs[-1][0] == n.team:
            runs[-1] = (n.team, runs[-1][1] + 1)
        else:
            runs.append((n.team, 1))
    return runs


def _bypassed(n: NormEvent, meta: MatchMetadata, tracking: _Tracking | None) -> int | None:
    if tracking is None or n.x is None or n.end_x is None or n.team is None:
        return None
    snap = tracking.at(n.period, n.t)
    if snap is None:
        return None
    high = attacks_high_x(meta, n.team, n.period)
    opponents = [
        to_team_frame(p.x, p.y, high) for p in snap.players if not p.player_id.startswith(n.team)
    ]
    assert n.y is not None and n.end_y is not None
    return opponents_bypassed((n.x, n.y), (n.end_x, n.end_y), opponents)


def _difficulty(n: NormEvent, meta: MatchMetadata, tracking: _Tracking | None) -> float:
    assert n.x is not None and n.y is not None and n.end_x is not None and n.end_y is not None
    return pass_difficulty(
        (n.x, n.y), (n.end_x, n.end_y), n.event.under_pressure, _bypassed(n, meta, tracking)
    )


def _event_signals(
    b: _Builder,
    scope: _Scope,
    evs: Sequence[NormEvent],
    meta: MatchMetadata,
    tracking: _Tracking | None,
) -> None:
    teams = (meta.home.code, meta.away.code)
    by_team: dict[str, list[NormEvent]] = {t: [] for t in teams}
    for n in evs:
        if n.team in by_team:
            by_team[n.team].append(n)
    on_ball = {t: [n for n in by_team[t] if n.event.type in ON_BALL] for t in teams}
    passes = {t: [n for n in by_team[t] if n.event.type is EventType.PASS] for t in teams}
    final_third_passes = {
        t: sum(n.x is not None and n.x >= FINAL_THIRD_X for n in passes[t]) for t in teams
    }
    runs = _sequences(evs)
    total_on_ball = sum(len(v) for v in on_ball.values())
    lost_open_play: Counter[str] = Counter()
    for n in evs:
        if n.event.type is EventType.POSSESSION_CHANGE and n.event.outcome == "open_play":
            lost_open_play[meta.opponent(n.team or "")] += 1

    for team in teams:
        opp = meta.opponent(team)
        mine = by_team[team]
        b.add("possession", _share(len(on_ball[team]), total_on_ball), "pct", scope, team)
        b.add("passes", len(passes[team]), "count", scope, team)
        complete = [n for n in passes[team] if n.event.outcome == "complete"]
        b.add("pass_completion", _share(len(complete), len(passes[team])), "pct", scope, team)
        b.add(
            "field_tilt",
            _share(final_third_passes[team], sum(final_third_passes.values())),
            "pct",
            scope,
            team,
        )

        # PPDA: opponent passes in their own defensive 60% per defensive action there.
        opp_passes = sum(n.x is not None and n.x < PPDA_OPP_ZONE_X for n in passes[opp])
        actions = sum(
            n.x is not None
            and n.x >= PITCH_LENGTH - PPDA_OPP_ZONE_X
            and (
                (n.event.type is EventType.TACKLE and n.event.outcome == "won")
                or n.event.type in (EventType.INTERCEPTION, EventType.FOUL)
            )
            for n in mine
        )
        b.add("ppda", opp_passes / actions if actions else None, "ratio", scope, team)

        wins = [
            n.x
            for n in mine
            if n.event.type is EventType.POSSESSION_CHANGE
            and n.event.outcome == "open_play"
            and n.x is not None
        ]
        b.add("turnover_height", _mean(wins), "m", scope, team)
        b.add("high_regains", sum(x >= FINAL_THIRD_X for x in wins), "count", scope, team)

        entries: Counter[str] = Counter()
        for n in mine:
            if (
                n.event.type in (EventType.PASS, EventType.CARRY)
                and n.event.outcome == "complete"
                and n.x is not None
                and n.end_x is not None
                and n.end_y is not None
                and enters_final_third(n.x, n.end_x)
            ):
                entries[channel_of(n.end_y)] += 1
        for channel in CHANNELS:
            b.add("channel_entries", entries[channel], "count", scope, team, channel)
        b.add("final_third_entries", sum(entries.values()), "count", scope, team)

        progressive = sum(
            is_progressive(n.x, n.y, n.end_x, n.end_y)  # type: ignore[arg-type]
            for n in complete
            if None not in (n.x, n.y, n.end_x, n.end_y)
        )
        b.add("progressive_passes", progressive, "count", scope, team)

        shots = [n for n in mine if n.event.type is EventType.SHOT]
        b.add("shots", len(shots), "count", scope, team)
        b.add(
            "shots_on_target",
            sum(n.event.outcome in ("goal", "saved") for n in shots),
            "count",
            scope,
            team,
        )
        b.add("goals", sum(n.event.type is EventType.GOAL for n in mine), "count", scope, team)
        xg = sum(
            expected_goals(n.x, n.y, n.event.body_part)
            for n in shots
            if n.x is not None and n.y is not None
        )
        b.add("xg", xg, "xg", scope, team)

        difficulties = [_difficulty(n, meta, tracking) for n in complete if n.end_x is not None]
        b.add("pass_difficulty", _mean(difficulties), "rating", scope, team)

        lengths = [length for t, length in runs if t == team]
        seq_len = _mean(lengths)
        turnover_rate = lost_open_play[team] / scope.minutes
        b.add("sequence_length", seq_len, "actions", scope, team)
        b.add("turnovers_per_min", turnover_rate, "per_min", scope, team)
        if seq_len is not None:
            b.add("control_index", _control_index(seq_len, turnover_rate), "index", scope, team)

    all_lengths = [length for _, length in runs]
    if all_lengths:
        # Match-level: the per-team turnover rate, so it is on the same scale as team values.
        rate = sum(lost_open_play.values()) / len(teams) / scope.minutes
        b.add(
            "control_index",
            _control_index(sum(all_lengths) / len(all_lengths), rate),
            "index",
            scope,
        )


def _control_index(seq_len: float, turnovers_per_min: float) -> float:
    """0-100: high when possessions are long and turnovers rare (control), low in chaos."""
    return 100.0 * seq_len / (seq_len + CONTROL_TURNOVER_WEIGHT * turnovers_per_min)


# ----- half-only signals ----------------------------------------------------


class PassNetworkNode(BaseModel):
    """A player's average on-ball location (team frame) and touch count."""

    player_id: str
    x: float
    y: float
    touches: int


class PassNetworkEdge(BaseModel):
    """Completed passes between two players, in either direction."""

    a: str
    b: str
    passes: int


class PassNetwork(BaseModel):
    """Average positions and pass counts for one team in one half."""

    team: str
    period: int
    nodes: list[PassNetworkNode]
    edges: list[PassNetworkEdge]


def pass_network(
    events: Sequence[Event], meta: MatchMetadata, team: str, period: int
) -> PassNetwork:
    """Build a team's pass network for a half from on-ball events and pass receptions."""
    norm = [n for n in normalize(events, meta) if n.period == period and n.team == team]
    return _pass_network(norm, team, period)


def _pass_network(norm: Sequence[NormEvent], team: str, period: int) -> PassNetwork:
    spots: dict[str, list[tuple[float, float]]] = defaultdict(list)
    pairs: Counter[tuple[str, str]] = Counter()
    for n in norm:
        e = n.event
        if e.type in ON_BALL and e.player_id and n.x is not None and n.y is not None:
            spots[e.player_id].append((n.x, n.y))
        if e.type is EventType.PASS and e.outcome == "complete" and e.player_id:
            receiver = e.related_player_id
            if receiver and n.end_x is not None and n.end_y is not None:
                spots[receiver].append((n.end_x, n.end_y))
                a, b_ = sorted((e.player_id, receiver))
                pairs[(a, b_)] += 1
    nodes = [
        PassNetworkNode(
            player_id=pid,
            x=round(sum(x for x, _ in pts) / len(pts), 1),
            y=round(sum(y for _, y in pts) / len(pts), 1),
            touches=len(pts),
        )
        for pid, pts in sorted(spots.items())
    ]
    edges = [PassNetworkEdge(a=a, b=b_, passes=c) for (a, b_), c in pairs.most_common()]
    return PassNetwork(team=team, period=period, nodes=nodes, edges=edges)


def _half_only_signals(
    b: _Builder,
    half: _Scope,
    in_period: Sequence[NormEvent],
    meta: MatchMetadata,
    tracking: _Tracking | None,
) -> None:
    evs = [n for n in in_period if n.t >= half.start]
    for team in (meta.home.code, meta.away.code):
        opp = meta.opponent(team)
        mine = [n for n in evs if n.team == team]
        theirs = [n for n in evs if n.team == opp]

        # Possession share per zone: my on-ball events in my third Z versus the
        # opponent's in the same physical area (their mirror third).
        for zone in THIRDS:
            me = sum(
                n.event.type in ON_BALL and n.x is not None and third_of(n.x) == zone for n in mine
            )
            them = sum(
                n.event.type in ON_BALL and n.x is not None and third_of(n.x) == MIRROR[zone]
                for n in theirs
            )
            b.add("possession", _share(me, me + them), "pct", half, team, f"{zone}_third")

        entries: Counter[str] = Counter()
        for n in mine:
            if (
                n.event.type in (EventType.PASS, EventType.CARRY)
                and n.event.outcome == "complete"
                and n.x is not None
                and n.end_x is not None
                and n.end_y is not None
                and enters_final_third(n.x, n.end_x)
            ):
                entries[channel_of(n.end_y)] += 1
        total = sum(entries.values())
        for channel in CHANNELS:
            b.add("channel_share", _share(entries[channel], total), "pct", half, team, channel)

        network = _pass_network(mine, team, half.period)
        for node in network.nodes:
            if node.touches >= MIN_NODE_TOUCHES:
                q = player_qualifier(node.player_id)
                b.add("avg_position_x", node.x, "m", half, team, q, node.player_id)
                b.add("avg_position_y", node.y, "m", half, team, q, node.player_id)
        for edge in network.edges[:TOP_PAIRS]:
            q = f"{player_qualifier(edge.a)}_{player_qualifier(edge.b)}"
            b.add("pass_pair", edge.passes, "count", half, team, q)

        per_player: dict[str, list[float]] = defaultdict(list)
        for n in mine:
            if (
                n.event.type is EventType.PASS
                and n.event.outcome == "complete"
                and n.event.player_id
                and n.end_x is not None
            ):
                per_player[n.event.player_id].append(_difficulty(n, meta, tracking))
        for pid, values in sorted(per_player.items()):
            if len(values) >= MIN_PLAYER_PASSES:
                q = player_qualifier(pid)
                b.add("pass_difficulty", _mean(values), "rating", half, team, q, pid)
                b.add(
                    "difficult_passes",
                    sum(v >= DIFFICULT_PASS for v in values),
                    "count",
                    half,
                    team,
                    q,
                    pid,
                )

        shots = [n for n in mine if n.event.type is EventType.SHOT and n.event.speed_kmh]
        b.add(
            "fast_shots",
            sum(n.event.speed_kmh > FAST_SHOT_KMH for n in shots),  # type: ignore[operator]
            "count",
            half,
            team,
        )
        fastest: dict[str, float] = {}
        for n in shots:
            pid, speed = n.event.player_id, n.event.speed_kmh
            if pid and speed:
                fastest[pid] = max(fastest.get(pid, 0.0), speed)
        for pid, speed in sorted(fastest.items()):
            b.add("max_shot_speed", speed, "kmh", half, team, player_qualifier(pid), pid)

    if tracking is not None:
        _player_tracking_signals(b, half, tracking, meta)


# ----- snapshot-based signals -----------------------------------------------


def _team_frame_positions(
    snap: Snapshot, team: str, meta: MatchMetadata, gk: set[str]
) -> list[tuple[float, float]]:
    high = attacks_high_x(meta, team, snap.period)
    return [
        to_team_frame(p.x, p.y, high)
        for p in snap.players
        if p.player_id.startswith(team + "-") and p.player_id not in gk
    ]


def _tracking_signals(
    b: _Builder, scope: _Scope, tracking: _Tracking, meta: MatchMetadata, gk: set[str]
) -> None:
    snaps = [
        s for s in tracking.snaps if s.period == scope.period and scope.start <= s.t <= scope.end
    ]
    for team in (meta.home.code, meta.away.code):
        lines, widths = [], []
        for s in snaps:
            pts = _team_frame_positions(s, team, meta, gk)
            if len(pts) < 4:
                continue
            widths.append(max(y for _, y in pts) - min(y for _, y in pts))
            if tracking.team_in_possession(s.period, s.t) not in (team, None):
                deepest = sorted(x for x, _ in pts)[:4]
                lines.append(sum(deepest) / 4)
        b.add("def_line_height", _mean(lines), "m", scope, team)
        b.add("team_width", _mean(widths), "m", scope, team)
        sprints = sum(
            count
            for pid, count in _sprint_efforts(tracking, scope).items()
            if pid.startswith(team + "-")
        )
        if snaps:
            b.add("sprints", sprints, "count", scope, team)


def _speeds(snaps: Sequence[Snapshot], period: int) -> dict[str, list[tuple[float, float]]]:
    """Per player, (time, km/h) between consecutive snapshots of a period."""
    out: dict[str, list[tuple[float, float]]] = defaultdict(list)
    prev: Snapshot | None = None
    for s in snaps:
        if s.period != period:
            continue
        if prev is not None and s.t > prev.t:
            before = {p.player_id: (p.x, p.y) for p in prev.players}
            dt = s.t - prev.t
            for p in s.players:
                if p.player_id in before:
                    kmh = math.dist((p.x, p.y), before[p.player_id]) / dt * 3.6
                    out[p.player_id].append((s.t, kmh))
        prev = s
    return out


def _sprint_efforts(tracking: _Tracking, scope: _Scope) -> Counter[str]:
    """Sprint efforts (runs above 25 km/h) starting inside the scope, per player."""
    efforts: Counter[str] = Counter()
    for pid, series in tracking.speeds(scope.period).items():
        fast_before = False
        for t, kmh in series:
            fast = kmh > SPRINT_KMH
            if fast and not fast_before and scope.start <= t <= scope.end:
                efforts[pid] += 1
            fast_before = fast
    return efforts


def _player_tracking_signals(
    b: _Builder, half: _Scope, tracking: _Tracking, meta: MatchMetadata
) -> None:
    speeds = tracking.speeds(half.period)
    efforts = _sprint_efforts(tracking, half)
    for pid, series in sorted(speeds.items()):
        team, q = pid.split("-")[0], player_qualifier(pid)
        b.add("top_speed", max(kmh for _, kmh in series), "kmh", half, team, q, pid)
        b.add("sprints", efforts.get(pid, 0), "count", half, team, q, pid)
        b.add("distance", sum(kmh / 3.6 for _, kmh in series) / 1000, "km", half, team, q, pid)
