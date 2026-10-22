"""Watcher: checks each half-time prediction against the second half as it unfolds.

A prediction compares the mean of its metric over complete second-half windows
with the first-half window mean (``*_wmean`` signals). After its
``evaluate_after_minute`` and once at least three second-half windows are
complete, it is:

- **confirmed** when the change reaches the threshold in the predicted direction;
- **contradicted** when the change is less than half the threshold, or goes the
  other way: clearly not happening;
- **open** in between. At full time every open prediction gets a final verdict.

A decided prediction can be reversed only when the opposite rule clearly holds.
On every status change the watcher writes a one-sentence explanation grounded in
the before and after values; online the model writes it, and it must pass the
deterministic number check or the template sentence is used instead.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from second_act.agents.verifier import check_numbers
from second_act.analytics.signals import WMEAN_SUFFIX, SignalSet, make_id
from second_act.i18n.catalog import Metric, Num, Unit, Word
from second_act.i18n.phrases import Phrase
from second_act.schema.metadata import MatchMetadata
from second_act.schema.predictions import Prediction, PredictionStatus

MIN_SECOND_HALF_WINDOWS = 3

METRIC_LABELS = {
    "channel_entries": "{q}-channel entries",
    "final_third_entries": "final-third entries",
    "possession": "possession",
    "field_tilt": "field tilt",
    "ppda": "PPDA",
    "turnover_height": "turnover height",
    "high_regains": "ball wins in the final third",
    "progressive_passes": "progressive passes",
    "shots": "shots",
    "xg": "xG",
    "pass_completion": "pass completion",
    "pass_difficulty": "pass difficulty",
    "sequence_length": "possession length",
    "turnovers_per_min": "turnover rate",
    "control_index": "control index",
    "def_line_height": "defensive line height",
    "team_width": "team width",
}
UNIT_SUFFIX = {"per_window": " per five minutes", "pct": "%", "m": " m", "xg": " per five minutes"}


@dataclass(frozen=True)
class Evaluation:
    """The state of one prediction test at a moment of the match."""

    prediction_id: str
    status: PredictionStatus
    before: float
    after: float
    change_pct: float
    windows: int
    evidence: list[str]
    final: bool = False

    @property
    def signed_change(self) -> float:
        """Change in the predicted direction (positive = towards the prediction)."""
        return self.change_pct


def _change_pct(before: float, after: float) -> float:
    if before == 0:
        return 100.0 if after > 0 else 0.0
    return (after - before) / abs(before) * 100


def evaluate(
    pred: Prediction, signals: SignalSet, minute: float, final: bool = False
) -> Evaluation | None:
    """Evaluate a prediction now; None while it cannot be judged yet."""
    if not final and minute < pred.evaluate_after_minute:
        return None
    qualifier = pred.scope.get("channel")
    windows = signals.window_values(pred.metric, pred.subject_team, qualifier, 2)
    if not windows or (not final and len(windows) < MIN_SECOND_HALF_WINDOWS):
        return None
    metric = pred.metric + WMEAN_SUFFIX
    before_id = make_id(metric, pred.subject_team, qualifier, 1)
    after_id = make_id(metric, pred.subject_team, qualifier, 2)
    before, after = signals.get(before_id), signals.get(after_id)
    if before is None or after is None:
        return None
    change = _change_pct(before.value, after.value)
    toward = change if pred.direction == "increase" else -change
    if toward >= pred.threshold_pct:
        status = PredictionStatus.CONFIRMED
    elif final or toward < pred.threshold_pct / 2:
        status = PredictionStatus.CONTRADICTED
    else:
        status = PredictionStatus.OPEN
    return Evaluation(
        pred.prediction_id,
        status,
        before.value,
        after.value,
        round(toward, 1),
        len(windows),
        [before_id, after_id],
        final,
    )


def next_status(current: PredictionStatus, evaluation: Evaluation) -> PredictionStatus:
    """Apply the stability rule: decided predictions only flip on a clear opposite result."""
    if current is PredictionStatus.OPEN or evaluation.final:
        return evaluation.status
    if evaluation.status is PredictionStatus.OPEN:
        return current
    return evaluation.status


def _label(pred: Prediction) -> str:
    return METRIC_LABELS.get(pred.metric, pred.metric).format(q=pred.scope.get("channel", ""))


def _fmt(value: float) -> str:
    return f"{value:g}"


def unit_of(metric: str) -> str:
    """The unit a metric's window mean is quoted in."""
    return {
        "possession": "pct",
        "field_tilt": "pct",
        "pass_completion": "pct",
        "turnover_height": "m",
        "def_line_height": "m",
        "team_width": "m",
        "xg": "xg",
        "ppda": "ratio",
    }.get(metric, "per_window")


def explanation_phrase(pred: Prediction, ev: Evaluation, meta: MatchMetadata) -> Phrase:
    """The verdict explanation as a catalogue phrase (renders in every variant)."""
    went = "rose" if ev.after > ev.before else "fell" if ev.after < ev.before else "stayed level"
    params = {
        "team": meta.team(pred.subject_team).short_name,
        "label": Metric(pred.metric, pred.scope.get("channel")),
        "went": Word(went),
        "wanted": Word("rise" if pred.direction == "increase" else "drop"),
        "b": Num(ev.before),
        "a": Num(ev.after),
        "unit": Unit(unit_of(pred.metric)),
        "size": Num(abs(round(ev.change_pct))),
        "thr": Num(pred.threshold_pct),
    }
    if ev.status is PredictionStatus.CONFIRMED:
        return Phrase("x_confirmed", params)
    if ev.signed_change < 0:
        return Phrase("x_opposite", params)
    return Phrase("x_short", params)


def template_explanation(pred: Prediction, ev: Evaluation, meta: MatchMetadata) -> str:
    """One grounded English sentence explaining the verdict."""
    return explanation_phrase(pred, ev, meta).render()


def check_explanation(text: str, pred: Prediction, ev: Evaluation) -> list[str]:
    """Deterministic check: every number is a cited value, a derived value or the threshold."""
    cited = {ev.evidence[0]: ev.before, ev.evidence[1]: ev.after}
    issues = check_numbers(text, cited, pred.prediction_id, extra=[pred.threshold_pct])
    return [str(i) for i in issues]


class ExplanationOut(BaseModel):
    """Structured output for the watcher's explanation."""

    model_config = ConfigDict(extra="forbid")

    sentence: str


WATCHER_INSTRUCTIONS = """You write one sentence for a football broadcast saying whether a
half-time prediction is holding. You get the verdict and a plain template sentence that
states the facts correctly. Rewrite it as one natural, lively sentence that keeps the
same verdict, the same direction (rose/fell) and the same numbers. Do not add numbers.
Use the short team names given."""

# Words that contradict a verdict; a model sentence containing them is not used.
_VERDICT_CLASH = {
    PredictionStatus.CONFIRMED: (
        "not happening",
        "contradict",
        "short of",
        "fails",
        "failed",
        "isn't",
        "is not",
        "hasn't",
        "opposite",
    ),
    PredictionStatus.CONTRADICTED: (
        "holds",
        "holding",
        "is working",
        "confirmed",
        "on track",
        "succeed",
        "paying off",
    ),
}


def verdict_clash(text: str, status: PredictionStatus) -> str | None:
    """A phrase in ``text`` that contradicts the verdict, if any."""
    lowered = text.lower()
    return next((w for w in _VERDICT_CLASH.get(status, ()) if w in lowered), None)


class Explainer(Protocol):
    """Writes the explanation for a status change."""

    async def explain(
        self, pred: Prediction, ev: Evaluation, meta: MatchMetadata
    ) -> tuple[str, float, dict]: ...


class ModelExplainer:
    """Online explanation via an Agent Framework agent."""

    def __init__(self, agent) -> None:
        self.agent = agent

    async def explain(self, pred: Prediction, ev: Evaluation, meta: MatchMetadata):
        """Ask the model for the sentence; returns (text, latency, usage)."""
        prompt = (
            f"Prediction by {meta.team(pred.team).short_name}: {pred.claim}\n"
            f"Verdict: {ev.status.value}.\n"
            f"Template sentence: {template_explanation(pred, ev, meta)}"
        )
        start = time.perf_counter()
        response = await self.agent.run(prompt, options={"response_format": ExplanationOut})
        latency = round(time.perf_counter() - start, 2)
        out = response.value
        if not isinstance(out, ExplanationOut):
            out = ExplanationOut.model_validate_json(response.text)
        return out.sentence.strip(), latency, dict(response.usage_details or {})


def final_notes(
    predictions: list[Prediction], signals: SignalSet, meta: MatchMetadata
) -> dict[str, tuple[str, list[str]]]:
    """Grounded full-time explanation and evidence for every prediction (for the recap).

    Uses the full-time evaluation, so the numbers match the final signals the recap is
    verified against, even if a verdict was first reached earlier in the half.
    """
    notes: dict[str, tuple[str, list[str]]] = {}
    for pred in predictions:
        ev = evaluate(pred, signals, 90, final=True)
        if ev is None:
            continue
        ev = Evaluation(**{**ev.__dict__, "status": pred.status})
        notes[pred.prediction_id] = (template_explanation(pred, ev, meta), ev.evidence)
    return notes


@dataclass
class PredictionCheck:
    """A status change, ready for the presenter."""

    prediction: Prediction
    evaluation: Evaluation
    explanation: str
    source: str
    previous: PredictionStatus


class Watcher:
    """Evaluates every open or decided prediction at each check and reports changes."""

    def __init__(self, explainer: Explainer | None = None, log=None) -> None:
        self.explainer, self.log = explainer, log

    async def check(
        self,
        predictions: list[Prediction],
        signals: SignalSet,
        meta: MatchMetadata,
        minute: float,
        final: bool = False,
    ) -> tuple[list[Prediction], list[PredictionCheck]]:
        """Return the updated predictions and the status changes to announce."""
        updated: list[Prediction] = []
        changes: list[PredictionCheck] = []
        for pred in predictions:
            ev = evaluate(pred, signals, minute, final)
            if ev is None:
                updated.append(pred)
                continue
            status = next_status(pred.status, ev)
            new = pred.model_copy(update={"status": status})
            updated.append(new)
            if status is not pred.status and status is not PredictionStatus.OPEN:
                ev = Evaluation(**{**ev.__dict__, "status": status})
                text, source = await self._explain(new, ev, meta)
                changes.append(PredictionCheck(new, ev, text, source, pred.status))
        return updated, changes

    async def _explain(self, pred: Prediction, ev: Evaluation, meta: MatchMetadata):
        fallback = template_explanation(pred, ev, meta)
        if self.explainer is None:
            self._record(pred, ev, "template", fallback, [])
            return fallback, "template"
        try:
            text, latency, usage = await self.explainer.explain(pred, ev, meta)
        except Exception as exc:
            self._record(pred, ev, "template", fallback, [f"model error: {exc}"[:300]])
            return fallback, "template"
        issues = check_explanation(text, pred, ev)
        clash = verdict_clash(text, ev.status)
        if clash:
            issues.append(f"contradicts the verdict ({clash!r})")
        if issues:
            self._record(pred, ev, "template", fallback, issues, latency, usage, rejected=text)
            return fallback, "template"
        self._record(pred, ev, "model", text, [], latency, usage)
        return text, "model"

    def _record(self, pred, ev, source, text, issues, latency=None, usage=None, rejected=None):
        if self.log is None:
            return
        from second_act.orchestration.agent_log import usage_fields

        self.log.record(
            "watcher",
            "status_change",
            prediction_id=pred.prediction_id,
            status=ev.status.value,
            before=ev.before,
            after=ev.after,
            change_pct=ev.change_pct,
            windows=ev.windows,
            final=ev.final,
            source=source,
            text=text,
            issues=issues,
            rejected=rejected,
            latency_s=latency,
            **usage_fields(usage),
        )
