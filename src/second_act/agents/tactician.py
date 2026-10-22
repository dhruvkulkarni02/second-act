"""Tactician: writes the half-time briefing from first-half signals.

Online, a Foundry model drafts the briefing as structured output from a
digest of the signals. Offline (tests, CI) and as the verifier's last-resort
fallback, ``template_briefing`` builds it with fixed rules directly from the
signals, so every number is grounded by construction.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from second_act.analytics.signals import SignalSet, make_id
from second_act.analytics.windows import WINDOWS_PER_PERIOD
from second_act.schema.briefing import BriefingDraft, Point, TeamRead
from second_act.schema.metadata import MatchMetadata
from second_act.schema.predictions import Prediction

EVALUATE_AFTER = 65
CHANNELS = ("left", "centre", "right")
FLANK_FACING = {"left": "right", "centre": "central", "right": "left"}

METRIC_NOTES = {
    "possession": "share of on-ball events (pct); with a *_third qualifier, share in that zone",
    "passes": "passes attempted",
    "pass_completion": "pct of passes completed",
    "field_tilt": "share of final-third passes by both teams (pct)",
    "ppda": "opponent passes per defensive action in the press zone; LOWER = more intense press",
    "turnover_height": "mean x (m from own goal) where the team wins the ball; higher = higher up",
    "high_regains": "ball wins in the final third (i.e. the opponent losing it in its own third)",
    "channel_entries": "completed passes/carries into the final third via that channel",
    "final_third_entries": "all final-third entries",
    "channel_share": "pct of entries per channel",
    "progressive_passes": "completed passes ending >= 10 m closer to goal",
    "shots": "shots",
    "shots_on_target": "shots on target",
    "goals": "goals",
    "xg": "expected goals",
    "pass_difficulty": "mean difficulty (0-100) of completed passes",
    "sequence_length": "mean on-ball actions per possession",
    "turnovers_per_min": "open-play ball losses per minute",
    "control_index": "0-100; high = long possessions and few turnovers, low = chaos",
    "def_line_height": "out of possession: mean x of the four deepest outfielders (m)",
    "team_width": "mean width of the outfield shape (m)",
    "sprints": "sprint efforts > 25 km/h",
    "fast_shots": "shots above 100 km/h",
    "avg_position_x": "player's mean on-ball x (m)",
    "avg_position_y": "player's mean on-ball y (m; left = low y)",
    "pass_pair": "completed passes between two players",
    "top_speed": "top speed (km/h)",
    "difficult_passes": "completed passes rated >= 60 difficulty",
    "max_shot_speed": "fastest shot (km/h)",
    "distance": "distance covered (km)",
}

TACTICIAN_INSTRUCTIONS = """You are the half-time tactical analyst for a football broadcast.
You write a briefing as JSON for two audiences who will later get it rewritten for them:
analysts and casual fans. Be specific, concrete and fair to both teams.

Use the three-letter team codes (for example HAR, KES) in every "team" and
"subject_team" field; use names in prose.

Every team-level signal describes that team's own actions: sig-ppda-KES-p1 is how hard
Kestrel press (not how hard they are pressed); sig-high_regains-HAR-p1 counts Harbour
winning the ball in the opponent's third, which is the opponent losing it in its own
third; sig-turnover_height-KES-p1 is where Kestrel win the ball back.

Hard rules (a verifier rejects drafts that break them):
1. Every claim cites the signal IDs it relies on, copied exactly (e.g. sig-ppda-HAR-p1).
   Cite bare IDs only, never "id=value".
2. Every number you write must be the value of a signal you cite in that same point,
   or a difference, ratio or percentage change between two cited values. Do not write
   any other number: no minutes of the match, no counts you worked out yourself.
   Round as the value is given or to a whole number. Prefer words ("twice as many").
3. For each team: 1-3 points in "working", 1-3 in "not_working", and 2-3 predictions.
4. Predictions are what THAT team's manager will change at half-time, written as a
   measurable test the system will check in the second half:
   - metric: one of {metrics}
   - subject_team: the team whose metric should change (often the opponent).
   - channel: "left"|"centre"|"right" for channel_entries, otherwise "none".
   - direction: "increase" or "decrease"; threshold_pct: 5-80
   - evaluate_after_minute: 60-85 (use about {evaluate_after})
   - prediction_id: p-<TEAM>-1, p-<TEAM>-2, ...
   - evidence: the first-half signal IDs that motivate it.
   The claim may only contain numbers equal to threshold_pct, evaluate_after_minute,
   or cited values.
5. Team-frame conventions: every team attacks toward x = 105; "left" is the attacking
   team's left. A team attacking down its left meets the opponent's right side.

How to choose predictions: each manager reacts to what hurt their team and what the
opponent did best in the first half. Start from the "Key first-half contrasts" and the
not_working points, and predict the concrete change a manager would make to answer
them (block the flank the opponent is overloading, change shape to resist a press,
drop a midfielder deeper, press higher or lower, switch the point of attack), then
express its expected effect as a test. The analyst's candidate adjustments are grounded
starting points: keep the ones a manager would really make, rewrite their claims in
your own words, and replace any that are weak. Avoid generic predictions like "take
more shots"."""


@dataclass
class _Ctx:
    signals: SignalSet
    meta: MatchMetadata

    def sid(self, metric: str, team: str | None = None, qualifier: str | None = None) -> str:
        return make_id(metric, team, qualifier, 1)

    def v(self, metric: str, team: str | None = None, qualifier: str | None = None) -> float:
        value = self.signals.value(metric, team, qualifier, 1)
        return 0.0 if value is None else value

    def has(self, metric: str, team: str | None = None, qualifier: str | None = None) -> bool:
        return self.sid(metric, team, qualifier) in self.signals

    def name(self, code: str) -> str:
        return self.meta.team(code).short_name


def _fmt(value: float) -> str:
    return f"{value:g}"


# ----- template briefing ------------------------------------------------------


def _top_channel(c: _Ctx, team: str) -> tuple[str, float]:
    shares = {ch: c.v("channel_share", team, ch) for ch in CHANNELS}
    best = max(shares, key=shares.__getitem__)
    return best, shares[best]


def _working(c: _Ctx, t: str, o: str) -> list[Point]:
    tn, on = c.name(t), c.name(o)
    out: list[Point] = []
    hr_t, hr_o, ppda_t, ppda_o = (
        c.v("high_regains", t),
        c.v("high_regains", o),
        c.v("ppda", t),
        c.v("ppda", o),
    )
    if (
        c.has("ppda", t)
        and c.has("ppda", o)
        and ppda_t <= 0.6 * ppda_o
        and hr_t >= 2 * max(hr_o, 1)
    ):
        out.append(
            Point(
                text=f"{tn}'s press is forcing mistakes: {_fmt(hr_t)} ball wins in the final third "
                f"and a PPDA of {_fmt(ppda_t)}, against {_fmt(ppda_o)} for {on}.",
                evidence=[c.sid("high_regains", t), c.sid("ppda", t), c.sid("ppda", o)],
            )
        )
    channel, share = _top_channel(c, t)
    if share >= 50:
        entries = _fmt(c.v("channel_entries", t, channel))
        total = _fmt(c.v("final_third_entries", t))
        out.append(
            Point(
                text=f"{tn} are attacking down the {channel}: {entries} of their {total} "
                f"final-third entries ({_fmt(share)}%) come through that channel.",
                evidence=[
                    c.sid("channel_entries", t, channel),
                    c.sid("final_third_entries", t),
                    c.sid("channel_share", t, channel),
                ],
            )
        )
    if c.v("field_tilt", t) >= 65:
        out.append(
            Point(
                text=f"{tn} have pinned {on} back, playing {_fmt(c.v('field_tilt', t))}% of the "
                f"final-third passes.",
                evidence=[c.sid("field_tilt", t)],
            )
        )
    xg_t, xg_o = c.v("xg", t), c.v("xg", o)
    if xg_t >= 0.5 and xg_t >= 2 * xg_o:
        out.append(
            Point(
                text=f"{tn} have created {_fmt(xg_t)} xG to {on}'s {_fmt(xg_o)}.",
                evidence=[c.sid("xg", t), c.sid("xg", o)],
            )
        )
    if xg_o < 0.8 and xg_o < xg_t * 2:
        out.append(
            Point(
                text=f"{tn} have kept {on} quiet, allowing {_fmt(c.v('shots', o))} shots worth "
                f"{_fmt(xg_o)} xG.",
                evidence=[c.sid("shots", o), c.sid("xg", o)],
            )
        )
    if c.v("possession", t) >= 55:
        out.append(
            Point(
                text=f"{tn} have controlled the ball with {_fmt(c.v('possession', t))}% "
                f"possession.",
                evidence=[c.sid("possession", t)],
            )
        )
    if not out:
        out.append(
            Point(
                text=f"{tn} have completed {_fmt(c.v('pass_completion', t))}% of their passes.",
                evidence=[c.sid("pass_completion", t)],
            )
        )
    return out[:3]


def _not_working(c: _Ctx, t: str, o: str) -> list[Point]:
    tn, on = c.name(t), c.name(o)
    out: list[Point] = []
    hr_t, hr_o = c.v("high_regains", t), c.v("high_regains", o)
    if hr_o >= 8 and hr_o >= 2 * max(hr_t, 1):
        out.append(
            Point(
                text=f"{tn} keep losing the ball in their own third: {on} have won it there "
                f"{_fmt(hr_o)} times.",
                evidence=[c.sid("high_regains", o)],
            )
        )
    th_t, th_o = c.v("turnover_height", t), c.v("turnover_height", o)
    if th_t <= th_o - 15:
        out.append(
            Point(
                text=f"{tn} only win the ball back {_fmt(th_t)} m from their own goal on average, "
                f"against {_fmt(th_o)} m for {on}.",
                evidence=[c.sid("turnover_height", t), c.sid("turnover_height", o)],
            )
        )
    xg_t, goals_t = c.v("xg", t), c.v("goals", t)
    if xg_t < 0.5:
        out.append(
            Point(
                text=f"{tn} have created little: {_fmt(c.v('shots', t))} shots worth "
                f"{_fmt(xg_t)} xG.",
                evidence=[c.sid("shots", t), c.sid("xg", t)],
            )
        )
    elif xg_t - goals_t >= 0.7:
        out.append(
            Point(
                text=f"{tn} have scored {_fmt(goals_t)} from {_fmt(xg_t)} xG.",
                evidence=[c.sid("goals", t), c.sid("xg", t)],
            )
        )
    channel, share = _top_channel(c, t)
    if share >= 50:
        weakest = min(
            (ch for ch in CHANNELS if ch != channel), key=lambda ch: c.v("channel_share", t, ch)
        )
        out.append(
            Point(
                text=f"{tn} have barely used the {weakest}: "
                f"{_fmt(c.v('channel_share', t, weakest))}% of their final-third entries.",
                evidence=[c.sid("channel_share", t, weakest)],
            )
        )
    if c.v("turnovers_per_min", t) > c.v("turnovers_per_min", o):
        out.append(
            Point(
                text=f"{tn} are giving the ball away in open play "
                f"{_fmt(c.v('turnovers_per_min', t))} times a minute, more often than {on}.",
                evidence=[c.sid("turnovers_per_min", t), c.sid("turnovers_per_min", o)],
            )
        )
    if not out:
        out.append(
            Point(
                text=f"{tn}'s control index is {_fmt(c.v('control_index', t))}, against "
                f"{_fmt(c.v('control_index', o))} for {on}.",
                evidence=[c.sid("control_index", t), c.sid("control_index", o)],
            )
        )
    return out[:3]


def _predictions(c: _Ctx, t: str, o: str) -> list[Prediction]:
    tn, on = c.name(t), c.name(o)
    specs: list[dict] = []
    o_channel, o_share = _top_channel(c, o)
    if o_share >= 45:
        specs.append(
            dict(
                claim=(
                    f"{tn} crowd the middle to cut {on}'s central entries"
                    if o_channel == "centre"
                    else f"{tn} reinforce their {FLANK_FACING[o_channel]} side to cut {on}'s "
                    f"{o_channel}-channel entries"
                ),
                metric="channel_entries",
                subject_team=o,
                scope={"channel": o_channel},
                direction="decrease",
                threshold_pct=30,
                evidence=[
                    c.sid("channel_share", o, o_channel),
                    c.sid("channel_entries", o, o_channel),
                ],
            )
        )
    hr_t, hr_o = c.v("high_regains", t), c.v("high_regains", o)
    if hr_o >= 8 and hr_o >= 2 * max(hr_t, 1):
        specs.append(
            dict(
                claim=f"{tn} drop a midfielder deeper to beat the press, cutting how often "
                f"{on} win the ball in {tn}'s third",
                metric="high_regains",
                subject_team=o,
                scope={},
                direction="decrease",
                threshold_pct=30,
                evidence=[c.sid("high_regains", o)],
            )
        )
    t_channel, t_share = _top_channel(c, t)
    if t_share >= 50:
        alt = min(
            (ch for ch in CHANNELS if ch != t_channel), key=lambda ch: c.v("channel_share", t, ch)
        )
        if c.signals.window_values("channel_entries", t, alt, 1):
            specs.append(
                dict(
                    claim=f"{tn} switch play away from the {t_channel} as {on} adjust, "
                    f"sending more attacks down the {alt}",
                    metric="channel_entries",
                    subject_team=t,
                    scope={"channel": alt},
                    direction="increase",
                    threshold_pct=40,
                    evidence=[c.sid("channel_share", t, t_channel), c.sid("channel_share", t, alt)],
                )
            )
    if c.has("ppda", t) and c.v("ppda", t) <= 6:
        specs.append(
            dict(
                claim=f"{tn}'s press fades as legs tire, so their PPDA rises",
                metric="ppda",
                subject_team=t,
                scope={},
                direction="increase",
                threshold_pct=20,
                evidence=[c.sid("ppda", t)],
            )
        )
    if c.v("possession", t) < 45:
        specs.append(
            dict(
                claim=f"{tn} push more players forward and see more of the ball",
                metric="possession",
                subject_team=t,
                scope={},
                direction="increase",
                threshold_pct=10,
                evidence=[c.sid("possession", t)],
            )
        )
    specs.append(
        dict(
            claim=f"{tn} chase more of the game and get into the final third more often",
            metric="final_third_entries",
            subject_team=t,
            scope={},
            direction="increase",
            threshold_pct=20,
            evidence=[c.sid("final_third_entries", t)],
        )
    )
    specs.append(
        dict(
            claim=f"{tn} move the ball forward more directly after the break",
            metric="progressive_passes",
            subject_team=t,
            scope={},
            direction="increase",
            threshold_pct=15,
            evidence=[c.sid("progressive_passes", t)],
        )
    )
    from second_act.agents.verifier import baseline_problem

    specs = [
        spec
        for spec in specs
        if baseline_problem(
            spec["metric"], spec["subject_team"], spec["scope"].get("channel"), c.signals
        )
        is None
    ]
    return [
        Prediction(
            prediction_id=f"p-{t}-{i}",
            team=t,
            comparison="second_half_window_mean_vs_first_half_mean",
            evaluate_after_minute=EVALUATE_AFTER,
            **spec,
        )
        for i, spec in enumerate(specs[:3], start=1)
    ]


def template_briefing(signals: SignalSet, meta: MatchMetadata) -> BriefingDraft:
    """Build a briefing from first-half signals with fixed rules (offline mode and fallback)."""
    c = _Ctx(signals, meta)
    home, away = meta.home.code, meta.away.code
    g_home, g_away = c.v("goals", home), c.v("goals", away)
    goals_ev = [c.sid("goals", home), c.sid("goals", away)]
    if g_home != g_away:
        lead, trail = (home, away) if g_home > g_away else (away, home)
        tilt = c.v("field_tilt", lead)
        headline = (
            f"{c.name(lead)} lead {_fmt(max(g_home, g_away))}-{_fmt(min(g_home, g_away))} "
            f"at the break with {_fmt(tilt)}% of the final-third passing"
        )
        evidence = [*goals_ev, c.sid("field_tilt", lead)]
        if tilt < 50:
            headline = (
                f"{c.name(lead)} lead {_fmt(max(g_home, g_away))}-"
                f"{_fmt(min(g_home, g_away))} at the break against the run of territory"
            )
        del trail
    else:
        better = home if c.v("xg", home) >= c.v("xg", away) else away
        headline = (
            f"Level at {_fmt(g_home)}-{_fmt(g_away)}, with {c.name(better)} edging the "
            f"chances {_fmt(c.v('xg', better))} xG to "
            f"{_fmt(c.v('xg', meta.opponent(better)))}"
        )
        evidence = [*goals_ev, c.sid("xg", home), c.sid("xg", away)]
    teams = [
        TeamRead(
            team=t,
            working=_working(c, t, meta.opponent(t)),
            not_working=_not_working(c, t, meta.opponent(t)),
            predictions=_predictions(c, t, meta.opponent(t)),
        )
        for t in (home, away)
    ]
    return BriefingDraft(headline=headline, headline_evidence=evidence, teams=teams)


# ----- model drafting ---------------------------------------------------------


def _gloss(metric: str, team: str | None, meta: MatchMetadata) -> str:
    """Whose action a commonly misread signal measures."""
    if team is None:
        return ""
    me, opp = meta.team(team).short_name, meta.team(meta.opponent(team)).short_name
    return {
        "ppda": f"how hard {me} press; lower = harder",
        "high_regains": f"{me} winning the ball in {opp}'s third = {opp} losing it there",
        "turnover_height": f"where {me} win the ball back",
        "def_line_height": f"{me}'s back line out of possession",
    }.get(metric, "")


def signal_digest(
    signals: SignalSet, meta: MatchMetadata, player_name: Callable[[str], str]
) -> str:
    """A compact listing of first-half signals for the model prompt."""
    lines = ["# Team signals, whole first half (ID = value unit)"]
    team_level = sorted(
        (s for s in signals if s.period == 1 and s.window is None and s.player_id is None),
        key=lambda s: (s.metric, s.team or "", s.qualifier or ""),
    )
    for s in team_level:
        if s.qualifier and s.qualifier.startswith("n"):
            continue  # pass pairs are listed with players
        gloss = _gloss(s.metric, s.team, meta)
        lines.append(f"{s.signal_id} = {s.value:g} {s.unit}" + (f"  ({gloss})" if gloss else ""))
    lines.append("\n# Key first-half contrasts (from the analyst)")
    lines += key_contrasts(signals, meta)
    lines.append("\n# Candidate adjustments flagged by the analyst (use, adapt or replace)")
    lines += candidate_adjustments(signals, meta)
    lines.append(
        "\n# Trends by five-minute window, w0..w8 (context only: do not cite these "
        "lines; cite the whole-half IDs above)"
    )
    for metric in (
        "channel_entries",
        "final_third_entries",
        "possession",
        "ppda",
        "high_regains",
        "xg",
    ):
        for team in (meta.home.code, meta.away.code):
            for q in CHANNELS if metric == "channel_entries" else (None,):
                values = [signals.value(metric, team, q, 1, w) for w in range(WINDOWS_PER_PERIOD)]
                shown = ", ".join("-" if v is None else f"{v:g}" for v in values)
                label = " ".join(x for x in (metric, team, q) if x)
                lines.append(f"trend {label}: [{shown}]")
    lines.append("\n# Players, first half (name in brackets)")
    player_rows = sorted(
        (
            s
            for s in signals
            if s.period == 1
            and s.window is None
            and s.player_id is not None
            and s.metric in ("avg_position_x", "difficult_passes", "top_speed", "max_shot_speed")
        ),
        key=lambda s: (s.metric, s.player_id or ""),
    )
    for s in player_rows:
        lines.append(f"{s.signal_id} = {s.value:g} {s.unit} [{player_name(s.player_id or '')}]")
    for s in signals.find(metric="pass_pair", period=1, window=None):
        lines.append(f"{s.signal_id} = {s.value:g} passes")
    lines.append("\n# Metric meanings")
    lines += [f"- {k}: {v}" for k, v in METRIC_NOTES.items()]
    return "\n".join(lines)


def key_contrasts(signals: SignalSet, meta: MatchMetadata) -> list[str]:
    """The largest first-half differences between the teams, as cited facts."""
    c = _Ctx(signals, meta)
    out: list[str] = []
    for t in (meta.home.code, meta.away.code):
        o = meta.opponent(t)
        channel, share = _top_channel(c, t)
        if share >= 45:
            out.append(
                f"- {c.name(t)} put {_fmt(share)}% of final-third entries through the "
                f"{channel} ({c.sid('channel_share', t, channel)}), i.e. against "
                f"{c.name(o)}'s {FLANK_FACING[channel]} side"
            )
        hr_t, hr_o = c.v("high_regains", t), c.v("high_regains", o)
        if hr_t >= 2 * max(hr_o, 1) and hr_t >= 6:
            out.append(
                f"- {c.name(t)} won the ball in {c.name(o)}'s third {_fmt(hr_t)} times vs "
                f"{_fmt(hr_o)} ({c.sid('high_regains', t)}, {c.sid('high_regains', o)})"
            )
        if c.has("ppda", t) and c.has("ppda", o) and c.v("ppda", t) <= 0.6 * c.v("ppda", o):
            out.append(
                f"- {c.name(t)} pressed far harder: PPDA {_fmt(c.v('ppda', t))} vs "
                f"{_fmt(c.v('ppda', o))} ({c.sid('ppda', t)}, {c.sid('ppda', o)})"
            )
        if c.v("turnover_height", t) >= c.v("turnover_height", o) + 15:
            out.append(
                f"- {c.name(t)} won the ball much higher: {_fmt(c.v('turnover_height', t))} m"
                f" vs {_fmt(c.v('turnover_height', o))} m "
                f"({c.sid('turnover_height', t)}, {c.sid('turnover_height', o)})"
            )
        if c.v("xg", t) >= 2 * c.v("xg", o) and c.v("xg", t) >= 0.5:
            out.append(
                f"- {c.name(t)} created far more: {_fmt(c.v('xg', t))} xG vs "
                f"{_fmt(c.v('xg', o))} ({c.sid('xg', t)}, {c.sid('xg', o)})"
            )
    return out or ["- no large contrasts"]


def candidate_adjustments(signals: SignalSet, meta: MatchMetadata) -> list[str]:
    """The rule-based predictions, offered to the model as a starting menu."""
    c = _Ctx(signals, meta)
    out = []
    for t in (meta.home.code, meta.away.code):
        for p in _predictions(c, t, meta.opponent(t)):
            channel = p.scope.get("channel", "none")
            out.append(
                f"- {t}: {p.claim} -> metric={p.metric}, subject_team={p.subject_team}, "
                f"channel={channel}, {p.direction} >= {p.threshold_pct:g}% "
                f"(evidence: {', '.join(p.evidence)})"
            )
    return out


def tactician_instructions() -> str:
    """System instructions for the tactician agent."""
    from second_act.agents.verifier import EVALUABLE_METRICS

    return TACTICIAN_INSTRUCTIONS.format(
        metrics=", ".join(sorted(EVALUABLE_METRICS)), evaluate_after=EVALUATE_AFTER
    )


def briefing_prompt(
    signals: SignalSet,
    meta: MatchMetadata,
    score: dict[str, int],
    player_name: Callable[[str], str],
    feedback: list[str] | None = None,
    previous: BriefingDraft | None = None,
) -> str:
    """The user message for one drafting attempt, including verifier feedback on retries."""
    teams = "\n".join(
        f"- {t.code}: {t.name} (manager {t.manager}), started in a {t.formation}"
        for t in (meta.home, meta.away)
    )
    parts = [
        f"Match: {meta.title}. Half-time score {meta.home.code} {score[meta.home.code]} - "
        f"{score[meta.away.code]} {meta.away.code}.",
        f"Teams:\n{teams}",
        signal_digest(signals, meta, player_name),
        "Write the half-time briefing.",
    ]
    if feedback:
        parts.append(
            "Your previous draft was REJECTED by the verifier for these reasons. Fix every "
            "one and return a complete corrected briefing:\n- " + "\n- ".join(feedback)
        )
        if previous is not None:
            parts.append("Previous draft:\n" + json.dumps(previous.model_dump(mode="json")))
    return "\n\n".join(parts)


# ----- strict model output schema ---------------------------------------------------
# Structured outputs in strict mode need every field required and no free-form dicts,
# so the model fills these and ``to_draft`` converts them to the briefing contract.


class PredictionOut(BaseModel):
    """A prediction as the model writes it."""

    model_config = ConfigDict(extra="forbid")

    prediction_id: str
    claim: str
    metric: str
    subject_team: str
    channel: Literal["left", "centre", "right", "none"] = Field(
        description="Channel for channel_entries; 'none' for every other metric."
    )
    direction: Literal["increase", "decrease"]
    threshold_pct: float
    evaluate_after_minute: int
    evidence: list[str]


class TeamReadOut(BaseModel):
    """One team's read as the model writes it."""

    model_config = ConfigDict(extra="forbid")

    team: str
    working: list[Point]
    not_working: list[Point]
    predictions: list[PredictionOut]


class BriefingOut(BaseModel):
    """The briefing as the model writes it."""

    model_config = ConfigDict(extra="forbid")

    headline: str
    headline_evidence: list[str]
    teams: list[TeamReadOut]


class DraftConversionError(ValueError):
    """The model's output could not become a briefing; ``reasons`` go back as feedback."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def team_code(value: str, meta: MatchMetadata | None) -> str:
    """Map a team name, short name or code to its code (formatting, not grounding)."""
    if meta is None:
        return value
    wanted = value.strip().lower()
    for team in (meta.home, meta.away):
        if wanted in (team.code.lower(), team.name.lower(), team.short_name.lower()):
            return team.code
    return value


def to_draft(out: BriefingOut, meta: MatchMetadata | None = None) -> BriefingDraft:
    """Convert model output to the briefing contract, collecting every field error.

    Team names become codes and prediction IDs are assigned in order; those are
    formatting, so they are fixed here rather than sent back as rejections.
    """
    reasons: list[str] = []
    teams = []
    for read in out.teams:
        code = team_code(read.team, meta)
        predictions = []
        for i, p in enumerate(read.predictions):
            try:
                predictions.append(
                    Prediction(
                        prediction_id=f"p-{code}-{i + 1}",
                        team=code,
                        claim=p.claim,
                        metric=p.metric,
                        subject_team=team_code(p.subject_team, meta),
                        scope={} if p.channel == "none" else {"channel": p.channel},
                        comparison="second_half_window_mean_vs_first_half_mean",
                        direction=p.direction,
                        threshold_pct=p.threshold_pct,
                        evaluate_after_minute=p.evaluate_after_minute,
                        evidence=p.evidence,
                    )
                )
            except ValidationError as exc:
                for err in exc.errors():
                    field_name = ".".join(str(x) for x in err["loc"])
                    reasons.append(f"{read.team}.predictions[{i}].{field_name}: {err['msg']}")
        teams.append(
            TeamRead(
                team=code,
                working=read.working,
                not_working=read.not_working,
                predictions=predictions,
            )
        )
    if reasons:
        raise DraftConversionError(reasons)
    return BriefingDraft(
        headline=out.headline, headline_evidence=out.headline_evidence, teams=teams
    )


# ----- drafters -----------------------------------------------------------------


@dataclass
class DraftResult:
    """One drafting attempt."""

    draft: BriefingDraft
    source: str  # "model" or "template"
    latency_s: float = 0.0
    usage: dict | None = None


class TemplateDrafter:
    """Offline tactician: the rule-based template, no model calls."""

    source = "template"

    async def draft(self, state, feedback=None, previous=None) -> DraftResult:
        """Return the template briefing for the state's current signals."""
        return DraftResult(template_briefing(state.signals, state.meta), "template")


class ModelDrafter:
    """Online tactician: an Agent Framework agent on a Foundry model with structured output."""

    source = "model"

    def __init__(self, agent) -> None:
        self.agent = agent

    async def draft(self, state, feedback=None, previous=None) -> DraftResult:
        """Ask the model for a briefing; on retries include the verifier's reasons."""
        import time

        prompt = briefing_prompt(
            state.signals, state.meta, state.score, state.player_name, feedback, previous
        )
        start = time.perf_counter()
        response = await self.agent.run(prompt, options={"response_format": BriefingOut})
        latency = round(time.perf_counter() - start, 2)
        out = response.value
        if not isinstance(out, BriefingOut):
            out = BriefingOut.model_validate_json(response.text)
        usage = dict(response.usage_details or {})
        try:
            draft = to_draft(out, state.meta)
        except DraftConversionError as exc:
            exc.latency_s, exc.usage = latency, usage  # type: ignore[attr-defined]
            raise
        return DraftResult(draft, "model", latency, usage)
