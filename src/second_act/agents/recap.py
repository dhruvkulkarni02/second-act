"""Full-time recap: the tactician's account of how the tactical battle played out.

The recap covers the first-half story, what changed at the break, what became of
every half-time prediction and how the result came about. Its verifier adds one
accountability rule on top of the grounding checks: each prediction's review must
carry the watcher's final status. Offline (and as the fallback) the recap is built
from the signals and the watcher's verified explanations by fixed rules.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from second_act.agents.tactician import (
    METRIC_NOTES,
    TemplatePoint,
    team_code,
    template_phrases,
)
from second_act.agents.verifier import (
    Issue,
    baseline_problem,
    check_numbers,
    check_point,
    normalize_evidence,
)
from second_act.agents.watcher import Evaluation, evaluate, explanation_phrase, unit_of
from second_act.analytics.signals import WMEAN_SUFFIX, SignalSet, make_id
from second_act.i18n.catalog import Audience, Language, Metric, Num, Unit, Word
from second_act.i18n.phrases import Phrase
from second_act.schema.briefing import Point
from second_act.schema.predictions import PredictionStatus
from second_act.schema.recap import PredictionReview, RecapDraft
from second_act.state.match_state import MatchState

SECTIONS = ("first_half", "changes", "result")
MAX_POINTS = 3
CHANGE_METRICS = (
    ("channel_entries", ("left", "centre", "right")),
    ("high_regains", (None,)),
    ("final_third_entries", (None,)),
    ("possession", (None,)),
    ("xg", (None,)),
    ("ppda", (None,)),
    ("field_tilt", (None,)),
)
MIN_CHANGE_PCT = 25.0


# ----- deterministic checks -----------------------------------------------------


def check_recap(draft: RecapDraft, state: MatchState) -> list[Issue]:
    """Grounding for every claim, plus: every prediction reviewed once with its final status."""
    signals = state.signals
    issues: list[Issue] = []
    cited = {sid: signals.get(sid).value for sid in draft.headline_evidence if sid in signals}  # type: ignore[union-attr]
    for sid in draft.headline_evidence:
        if sid not in signals:
            issues.append(Issue("headline", f"unknown signal ID {sid}"))
    if not draft.headline_evidence:
        issues.append(Issue("headline", "no evidence cited"))
    issues += check_numbers(draft.headline, cited, "headline")
    for section in SECTIONS:
        points = getattr(draft, section)
        if not 1 <= len(points) <= MAX_POINTS:
            issues.append(Issue(section, f"needs 1-{MAX_POINTS} points"))
        for i, point in enumerate(points):
            issues += check_point(point, signals, f"{section}[{i}]")
    expected = {p.prediction_id: p.status for p in state.predictions}
    seen: set[str] = set()
    for i, review in enumerate(draft.predictions):
        where = f"predictions[{i}]"
        if review.prediction_id not in expected:
            issues.append(Issue(where, f"unknown prediction {review.prediction_id}"))
            continue
        if review.prediction_id in seen:
            issues.append(Issue(where, f"{review.prediction_id} reviewed twice"))
        seen.add(review.prediction_id)
        if review.status != expected[review.prediction_id].value:
            issues.append(
                Issue(
                    where,
                    f"{review.prediction_id} was "
                    f"{expected[review.prediction_id].value}, not {review.status}",
                )
            )
        pred = next(p for p in state.predictions if p.prediction_id == review.prediction_id)
        values: dict[str, float] = {}
        for sid in review.evidence:
            sig = signals.get(sid)
            if sig is None:
                issues.append(Issue(where, f"unknown signal ID {sid}"))
            else:
                values[sid] = sig.value
        if not review.evidence:
            issues.append(Issue(where, "no evidence cited"))
        issues += check_numbers(review.text, values, where, extra=[pred.threshold_pct])
    missing = sorted(set(expected) - seen)
    if missing:
        issues.append(Issue("predictions", f"missing reviews for {', '.join(missing)}"))
    return issues


def normalize_recap(draft: RecapDraft) -> RecapDraft:
    """Strip decoration from cited IDs."""
    data = draft.model_copy(deep=True)
    data.headline_evidence = normalize_evidence(data.headline_evidence)
    for section in SECTIONS:
        for point in getattr(data, section):
            point.evidence = normalize_evidence(point.evidence)
    for review in data.predictions:
        review.evidence = normalize_evidence(review.evidence)
    return data


_LOCATION = re.compile(r"^(first_half|changes|result|predictions)\[(\d+)\]$")


def prune_recap(
    draft: RecapDraft, issues: Sequence[Issue], state: MatchState
) -> tuple[RecapDraft, list[str]] | None:
    """Remove failing points; replace a failing prediction review with the watcher's own
    verified explanation. Headline and structural problems cannot be repaired."""
    targets: dict[str, set[int]] = {}
    for issue in issues:
        match = _LOCATION.match(issue.where)
        if match is None:
            return None
        targets.setdefault(match.group(1), set()).add(int(match.group(2)))
    out = draft.model_copy(deep=True)
    removed: list[str] = []
    for section in SECTIONS:
        drop = targets.get(section, set())
        points = getattr(out, section)
        keep = [p for i, p in enumerate(points) if i not in drop]
        if not keep:
            return None
        removed += [f"{section}: {points[i].text}" for i in sorted(drop) if i < len(points)]
        setattr(out, section, keep)
    final = {p.prediction_id: p.status.value for p in state.predictions}
    for i in sorted(targets.get("predictions", set())):
        if i >= len(out.predictions):
            return None
        pid = out.predictions[i].prediction_id
        if pid not in state.prediction_notes or pid not in final:
            return None
        text, evidence = state.prediction_notes[pid]
        removed.append(f"predictions: {out.predictions[i].text} (replaced by the watcher's note)")
        out.predictions[i] = PredictionReview(
            prediction_id=pid, status=final[pid], text=text, evidence=evidence
        )
    return out, removed


def recap_claims(draft: RecapDraft, signals: SignalSet) -> list[dict]:
    """Claims with their evidence values, for the model support check."""

    def describe(ids: Sequence[str]) -> list[str]:
        return [
            f"{sid} = {signals.get(sid).value:g} {signals.get(sid).unit} "  # type: ignore[union-attr]
            f"({METRIC_NOTES.get(signals.get(sid).metric.removesuffix(WMEAN_SUFFIX), '')})"  # type: ignore[union-attr]
            for sid in ids
            if sid in signals
        ]

    claims = [
        {"ref": "headline", "text": draft.headline, "evidence": describe(draft.headline_evidence)}
    ]
    for section in SECTIONS:
        for i, p in enumerate(getattr(draft, section)):
            claims.append(
                {"ref": f"{section}[{i}]", "text": p.text, "evidence": describe(p.evidence)}
            )
    # Prediction reviews are checked against the watcher's verdict deterministically.
    return claims


# ----- template recap -------------------------------------------------------------


@dataclass
class TemplateRecap:
    """The template recap as phrases; ``render`` gives one variant."""

    headline: Phrase
    headline_evidence: list[str]
    first_half: list[TemplatePoint]
    changes: list[TemplatePoint]
    predictions: list[
        tuple[str, str, Phrase | None, str, list[str]]
    ]  # id, status, phrase, text, ev
    result: list[TemplatePoint]

    def render(self, audience: Audience = "analyst", language: Language = "en") -> RecapDraft:
        return RecapDraft(
            headline=self.headline.render(audience, language),
            headline_evidence=self.headline_evidence,
            first_half=[p.point(audience, language) for p in self.first_half],
            changes=[p.point(audience, language) for p in self.changes],
            predictions=[
                PredictionReview(
                    prediction_id=pid,
                    status=status,  # type: ignore[arg-type]
                    text=phrase.render(audience, language) if phrase else text,
                    evidence=ev,
                )
                for pid, status, phrase, text, ev in self.predictions
            ],
            result=[p.point(audience, language) for p in self.result],
        )


def _wm(signals: SignalSet, metric: str, team: str, q: str | None, period: int):
    return signals.get(make_id(metric + WMEAN_SUFFIX, team, q, period))


def _biggest_changes(state: MatchState) -> list[TemplatePoint]:
    signals, meta = state.signals, state.meta
    found: list[tuple[float, TemplatePoint]] = []
    for team in (meta.home.code, meta.away.code):
        for metric, qualifiers in CHANGE_METRICS:
            for q in qualifiers:
                before, after = _wm(signals, metric, team, q, 1), _wm(signals, metric, team, q, 2)
                if before is None or after is None or before.value == 0:
                    continue
                if baseline_problem(metric, team, q, signals):
                    continue
                change = (after.value - before.value) / abs(before.value) * 100
                if abs(change) < MIN_CHANGE_PCT:
                    continue
                phrase = Phrase(
                    "r_change",
                    {
                        "team": meta.team(team).short_name,
                        "label": Metric(metric, q),
                        "went": Word("rose" if change > 0 else "fell"),
                        "b": Num(before.value),
                        "a": Num(after.value),
                        "unit": Unit(unit_of(metric)),
                    },
                )
                found.append(
                    (abs(change), TemplatePoint(phrase, [before.signal_id, after.signal_id]))
                )
    found.sort(key=lambda x: -x[0])
    return [p for _, p in found[:MAX_POINTS]]


def template_recap_phrases(state: MatchState) -> TemplateRecap:
    """The template recap as phrases, renderable in every variant."""
    signals, meta = state.signals, state.meta
    home, away = meta.home.code, meta.away.code
    goals = {t: [signals.value("goals", t, None, p) or 0.0 for p in (1, 2)] for t in (home, away)}
    goal_ids = [make_id("goals", t, None, p) for t in (home, away) for p in (1, 2)]
    total = {t: sum(goals[t]) for t in (home, away)}
    hn, an = meta.home.short_name, meta.away.short_name
    if total[home] == total[away]:
        headline = Phrase(
            "r_head_draw", {"hn": hn, "an": an, "g1": Num(total[home]), "g2": Num(total[away])}
        )
    else:
        win, lose = (home, away) if total[home] > total[away] else (away, home)
        headline = Phrase(
            "r_head_win",
            {
                "w": meta.team(win).short_name,
                "l": meta.team(lose).short_name,
                "gw": Num(total[win]),
                "gl": Num(total[lose]),
            },
        )
    first = template_phrases(SignalSet(s for s in signals if s.period == 1), meta)
    leader = max(first.teams, key=lambda r: signals.value("goals", r.team, None, 1) or 0)
    trailer = next(r for r in first.teams if r.team != leader.team)
    first_half = [*leader.working[:1], *trailer.not_working[:1]]
    changes = _biggest_changes(state) or [
        TemplatePoint(
            Phrase(
                "r_possession",
                {"tn": hn, "poss": Num(signals.value("possession", home, None, 2) or 0)},
            ),
            [make_id("possession", home, None, 2)],
        )
    ]
    final_eval = {pid: phrase for pid, phrase in _final_phrases(state).items()}
    reviews = []
    for p in state.predictions:
        if p.status is PredictionStatus.OPEN or p.prediction_id not in state.prediction_notes:
            continue
        text, evidence = state.prediction_notes[p.prediction_id]
        reviews.append(
            (p.prediction_id, p.status.value, final_eval.get(p.prediction_id), text, evidence)
        )
    result = []
    for t in (home, away):
        g2, xg2 = goals[t][1], signals.value("xg", t, None, 2) or 0.0
        if g2 > 0:
            result.append(
                TemplatePoint(
                    Phrase(
                        "r_scored", {"team": meta.team(t).short_name, "g": Num(g2), "xg": Num(xg2)}
                    ),
                    [make_id("goals", t, None, 2), make_id("xg", t, None, 2)],
                )
            )
    result.append(
        TemplatePoint(
            Phrase("r_final", {"hn": hn, "an": an, "g1": Num(total[home]), "g2": Num(total[away])}),
            goal_ids,
        )
    )
    return TemplateRecap(headline, goal_ids, first_half, changes, reviews, result[:MAX_POINTS])


def _final_phrases(state: MatchState) -> dict[str, Phrase]:
    """Watcher phrases for each prediction's full-time verdict (when the note matches)."""
    out: dict[str, Phrase] = {}
    for pred in state.predictions:
        ev = evaluate(pred, state.signals, 90, final=True)
        if ev is None:
            continue
        ev = Evaluation(**{**ev.__dict__, "status": pred.status})
        phrase = explanation_phrase(pred, ev, state.meta)
        note = state.prediction_notes.get(pred.prediction_id)
        if note and note[0] == phrase.render():
            out[pred.prediction_id] = phrase
    return out


def template_recap(state: MatchState) -> RecapDraft:
    """Build the recap from the signals and the watcher's notes with fixed rules."""
    return template_recap_phrases(state).render()


# ----- model drafting ---------------------------------------------------------------


RECAP_INSTRUCTIONS = """You are the tactical analyst writing the full-time recap for a football
broadcast. Tell how the tactical battle played out: the first-half story, what changed at
the break, what became of every half-time prediction, and how the result came about.

Hard rules (a verifier rejects drafts that break them):
1. Every claim cites the signal IDs it relies on, copied exactly; bare IDs only.
2. Every number must be the value of a cited signal, or a sum, difference, ratio or
   percentage change between two cited values. No other numbers, no match minutes.
3. 1-3 points each in first_half, changes and result.
4. Review EVERY prediction listed, once, with exactly the status given (confirmed or
   contradicted). Cite the before/after IDs given for it.
5. "_wmean" signals are averages per five-minute window; p1 = first half, p2 = second half.
Every team-level signal describes that team's own actions (sig-ppda-KES-p2 is how hard
Kestrel pressed in the second half)."""


class RecapOut(BaseModel):
    """Strict structured output for the recap."""

    model_config = ConfigDict(extra="forbid")

    headline: str
    headline_evidence: list[str]
    first_half: list[Point]
    changes: list[Point]
    predictions: list[PredictionReview]
    result: list[Point]


def recap_prompt(
    state: MatchState, feedback: list[str] | None = None, previous: RecapDraft | None = None
) -> str:
    """User message for one recap drafting attempt."""
    signals, meta = state.signals, state.meta
    lines = [
        f"Match: {meta.title}. Final score {meta.home.code} {state.score[meta.home.code]} - "
        f"{state.score[meta.away.code]} {meta.away.code}."
    ]
    if state.briefing is not None:
        lines.append(f"Half-time headline was: {state.briefing.headline}")
    lines.append("\n# Team signals by half (ID = value unit)")
    keep = {
        "goals",
        "shots",
        "xg",
        "possession",
        "field_tilt",
        "ppda",
        "turnover_height",
        "high_regains",
        "final_third_entries",
        "channel_share",
        "def_line_height",
    }
    for s in sorted(
        (
            s
            for s in signals
            if s.window is None
            and s.player_id is None
            and (s.metric in keep or s.metric.endswith(WMEAN_SUFFIX))
        ),
        key=lambda s: s.signal_id,
    ):
        if s.metric.endswith(WMEAN_SUFFIX) and s.metric.removesuffix(WMEAN_SUFFIX) not in (
            "channel_entries",
            "high_regains",
            "final_third_entries",
            "xg",
            "ppda",
            "possession",
        ):
            continue
        lines.append(f"{s.signal_id} = {s.value:g} {s.unit}")
    lines.append("\n# Predictions made at half-time and the watcher's final verdicts")
    for p in state.predictions:
        note = state.prediction_notes.get(p.prediction_id)
        ids = ", ".join(note[1]) if note else ""
        lines.append(
            f"- {p.prediction_id} ({p.team}): {p.claim} -> status {p.status.value}; "
            f"before/after: {ids}; watcher: {note[0] if note else ''}"
        )
    lines.append("\nWrite the full-time recap.")
    if feedback:
        lines.append(
            "Your previous draft was REJECTED for these reasons; fix every one:\n- "
            + "\n- ".join(feedback)
        )
        if previous is not None:
            lines.append("Previous draft:\n" + json.dumps(previous.model_dump(mode="json")))
    return "\n".join(lines)


class RecapDraftResult:
    """One recap drafting attempt."""

    def __init__(self, draft: RecapDraft, source: str, latency_s: float = 0.0, usage=None):
        self.draft, self.source, self.latency_s, self.usage = draft, source, latency_s, usage


class TemplateRecapDrafter:
    """Offline recap writer."""

    source = "template"

    async def draft(self, state, feedback=None, previous=None) -> RecapDraftResult:
        """The template recap."""
        return RecapDraftResult(template_recap(state), "template")


class ModelRecapDrafter:
    """Online recap writer: an Agent Framework agent with strict structured output."""

    source = "model"

    def __init__(self, agent) -> None:
        self.agent = agent

    async def draft(self, state, feedback=None, previous=None) -> RecapDraftResult:
        """Ask the model for the recap."""
        start = time.perf_counter()
        response = await self.agent.run(
            recap_prompt(state, feedback, previous), options={"response_format": RecapOut}
        )
        latency = round(time.perf_counter() - start, 2)
        out = response.value
        if not isinstance(out, RecapOut):
            out = RecapOut.model_validate_json(response.text)
        try:
            draft = RecapDraft.model_validate(out.model_dump())
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
        return RecapDraftResult(draft, "model", latency, dict(response.usage_details or {}))


Status = Literal["confirmed", "contradicted"]
__all__ = [
    "ModelRecapDrafter",
    "TemplateRecapDrafter",
    "check_recap",
    "prune_recap",
    "recap_claims",
    "team_code",
    "template_recap",
]
