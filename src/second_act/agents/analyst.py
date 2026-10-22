"""Analyst: watches each completed five-minute window and raises live cues.

Live triggers fire when a window stands out against the baseline: the first-half
average (in the second half) or the half average so far (in the first half).
Player tags pick out speed (shots above 100 km/h, top sprint speeds) and pass
quality (difficult passes completed). Both are deterministic, cite the signals
they come from, and are rate-limited: one trigger per five match minutes, one tag
per ten, and each player is tagged at most once per kind.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from second_act.analytics.signals import WMEAN_SUFFIX, SignalSet, make_id, player_qualifier
from second_act.i18n.catalog import Chan, Num
from second_act.i18n.phrases import Phrase
from second_act.schema.metadata import MatchMetadata

MIN_GAP_MIN = 5.0
SURGE_FACTOR, SURGE_MIN = 2.0, 3.0
HEIGHT_JUMP_M = 12.0
TILT_HIGH, TILT_BASE_MAX = 80.0, 70.0
CHANCE_XG = 0.4
FAST_SHOT_KMH = 100.0
FAST_RUN_KMH = 34.0
TAG_GAP_MIN = 10.0
DIFFICULT_PASSES_MIN = 4


@dataclass
class LiveItem:
    """A live trigger or player tag, ready for the presenter."""

    kind: str  # "live_trigger" or "player_tag"
    rule: str
    period: int
    minute: float  # match minute when raised (end of the window)
    team: str
    headline: Phrase
    body: Phrase
    evidence: list[str]
    player_id: str | None = None
    favours: str | None = None


@dataclass
class Analyst:
    """Raises live triggers and player tags at each window close."""

    meta: MatchMetadata
    player_names: dict[str, str]
    min_gap_min: float = MIN_GAP_MIN
    last_trigger: float = -1e9
    last_tag: float = -1e9
    tagged: set[tuple[str, str, int]] = field(default_factory=set)

    def on_window(
        self, signals: SignalSet, period: int, window: int, minute: float
    ) -> list[LiveItem]:
        """Items for the window that just closed (at most one trigger and one tag)."""
        out: list[LiveItem] = []
        if minute - self.last_trigger >= self.min_gap_min:
            triggers = self._triggers(signals, period, window, minute)
            if triggers:
                out.append(triggers[0])
                self.last_trigger = minute
        if minute - self.last_tag >= TAG_GAP_MIN:
            tags = self._tags(signals, period, window, minute)
            if tags:
                out.append(tags[0])
                self.last_tag = minute
        return out

    # ----- triggers --------------------------------------------------------------

    def _baseline(self, signals, metric, team, q, period, window):
        """(value, id) of the comparison baseline for this window."""
        if period == 2:
            sid = make_id(metric + WMEAN_SUFFIX, team, q, 1)
            sig = signals.get(sid)
        else:
            if window < 2:
                return None
            sid = make_id(metric + WMEAN_SUFFIX, team, q, 1)
            sig = signals.get(sid)
        return (sig.value, sid) if sig is not None else None

    def _triggers(
        self, signals: SignalSet, period: int, window: int, minute: float
    ) -> list[LiveItem]:
        found: list[tuple[float, LiveItem]] = []
        for team in (self.meta.home.code, self.meta.away.code):
            opp = self.meta.opponent(team)
            names = {"tn": self.meta.team(team).short_name, "on": self.meta.team(opp).short_name}

            def window_value(metric, q=None, team=team):
                sid = make_id(metric, team, q, period, window)
                sig = signals.get(sid)
                return (sig.value, sid) if sig is not None else None

            for ch in ("left", "centre", "right"):
                now, base = (
                    window_value("channel_entries", ch),
                    self._baseline(signals, "channel_entries", team, ch, period, window),
                )
                if now and base and now[0] >= max(SURGE_MIN, SURGE_FACTOR * base[0]):
                    params = {**names, "ch": Chan(ch), "n": Num(now[0]), "avg": Num(base[0])}
                    found.append(
                        (
                            now[0] / max(base[0], 0.5),
                            self._item(
                                "live_trigger",
                                "channel_surge",
                                period,
                                minute,
                                team,
                                Phrase("t_channel.h", params),
                                Phrase("t_channel.b", params),
                                [now[1], base[1]],
                            ),
                        )
                    )
            now = window_value("high_regains")
            base = self._baseline(signals, "high_regains", team, None, period, window)
            if now and base and now[0] >= max(SURGE_MIN, SURGE_FACTOR * base[0]):
                params = {**names, "n": Num(now[0]), "avg": Num(base[0])}
                found.append(
                    (
                        now[0] / max(base[0], 0.5) + 0.1,
                        self._item(
                            "live_trigger",
                            "press_burst",
                            period,
                            minute,
                            team,
                            Phrase("t_press.h", params),
                            Phrase("t_press.b", params),
                            [now[1], base[1]],
                        ),
                    )
                )
            now = window_value("turnover_height")
            base = self._baseline(signals, "turnover_height", team, None, period, window)
            if now and base and now[0] >= base[0] + HEIGHT_JUMP_M:
                params = {**names, "h": Num(now[0]), "avg": Num(base[0])}
                found.append(
                    (
                        1.5,
                        self._item(
                            "live_trigger",
                            "height_jump",
                            period,
                            minute,
                            team,
                            Phrase("t_height.h", params),
                            Phrase("t_height.b", params),
                            [now[1], base[1]],
                        ),
                    )
                )
            now = window_value("field_tilt")
            base = self._baseline(signals, "field_tilt", team, None, period, window)
            if now and base and now[0] >= TILT_HIGH and base[0] < TILT_BASE_MAX:
                params = {**names, "tilt": Num(now[0])}
                found.append(
                    (
                        1.2,
                        self._item(
                            "live_trigger",
                            "tilt",
                            period,
                            minute,
                            team,
                            Phrase("t_tilt.h", params),
                            Phrase("t_tilt.b", params),
                            [now[1]],
                        ),
                    )
                )
            now = window_value("xg")
            if now and now[0] >= CHANCE_XG:
                params = {**names, "xg": Num(now[0])}
                found.append(
                    (
                        1.0 + now[0],
                        self._item(
                            "live_trigger",
                            "chances",
                            period,
                            minute,
                            team,
                            Phrase("t_chances.h", params),
                            Phrase("t_chances.b", params),
                            [now[1]],
                        ),
                    )
                )
        found.sort(key=lambda x: -x[0])
        return [item for _, item in found]

    # ----- player tags -------------------------------------------------------------

    def _tags(self, signals: SignalSet, period: int, window: int, minute: float) -> list[LiveItem]:
        found: list[tuple[float, LiveItem]] = []
        for sig in signals.find(metric="max_shot_speed", period=period, window=None):
            key = ("shot", sig.player_id or "", int(sig.value))
            if sig.value > FAST_SHOT_KMH and key not in self.tagged:
                found.append(
                    (
                        sig.value / 100 + 1,
                        self._tag(
                            "fast_shot",
                            "g_shot",
                            sig,
                            period,
                            minute,
                            {"kmh": Num(sig.value)},
                            [sig.signal_id],
                            key,
                        ),
                    )
                )
        for sig in signals.find(metric="top_speed", period=period, window=None):
            key = ("speed", sig.player_id or "", 0)
            if sig.value >= FAST_RUN_KMH and key not in self.tagged:
                sprints = signals.get(make_id("sprints", sig.team, sig.qualifier, period))
                params = {"kmh": Num(sig.value), "sprints": Num(sprints.value if sprints else 0)}
                ev = [sig.signal_id] + ([sprints.signal_id] if sprints else [])
                found.append(
                    (
                        sig.value / 34,
                        self._tag("top_speed", "g_speed", sig, period, minute, params, ev, key),
                    )
                )
        if window >= 3:
            for sig in signals.find(metric="difficult_passes", period=period, window=None):
                key = ("passing", sig.player_id or "", 0)
                if sig.value >= DIFFICULT_PASSES_MIN and key not in self.tagged:
                    avg = signals.get(make_id("pass_difficulty", sig.team, sig.qualifier, period))
                    params = {"n": Num(sig.value), "avg": Num(avg.value if avg else 0)}
                    ev = [sig.signal_id] + ([avg.signal_id] if avg else [])
                    found.append(
                        (
                            sig.value / 5,
                            self._tag("passing", "g_passing", sig, period, minute, params, ev, key),
                        )
                    )
        found.sort(key=lambda x: -x[0])
        if found:
            item = found[0][1]
            self.tagged.add(item._key)  # type: ignore[attr-defined]
        return [item for _, item in found]

    def _tag(self, rule, key, sig, period, minute, params, evidence, tag_key) -> LiveItem:
        player = self.player_names.get(sig.player_id or "", sig.player_id or "")
        params = {"player": player, **params}
        item = self._item(
            "player_tag",
            rule,
            period,
            minute,
            sig.team or "",
            Phrase(f"{key}.h", params),
            Phrase(f"{key}.b", params),
            evidence,
            sig.player_id,
        )
        item._key = tag_key  # type: ignore[attr-defined]
        return item

    @staticmethod
    def _item(kind, rule, period, minute, team, headline, body, evidence, player_id=None):
        return LiveItem(
            kind, rule, period, minute, team, headline, body, evidence, player_id, favours=team
        )


__all__ = ["Analyst", "LiveItem", "player_qualifier"]
