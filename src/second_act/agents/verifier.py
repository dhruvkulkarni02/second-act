"""Verifier: checks every briefing (and later, recap) before it reaches the presenter.

Layer 1 is deterministic: cited signal IDs exist, every number in the text is a
cited signal's value (or a difference, ratio or percentage change between two
cited values) within a small tolerance, and every prediction is a well-formed,
evaluable test. Layer 2 asks the model whether each claim is actually supported
by its evidence; it runs only online.
"""

from __future__ import annotations

import itertools
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from second_act.analytics.signals import WINDOWED_METRICS, SignalSet, make_id
from second_act.schema.briefing import BriefingDraft, Point
from second_act.schema.metadata import MatchMetadata
from second_act.schema.predictions import Prediction, PredictionStatus

# Metrics the watcher can evaluate per window, and the qualifier each scope key maps to.
EVALUABLE_METRICS = WINDOWED_METRICS
SCOPED_METRICS = {"channel_entries": "channel"}
CHANNELS = {"left", "centre", "right"}
MIN_EVALUATE_MINUTE, MAX_EVALUATE_MINUTE = 60, 85
MIN_THRESHOLD, MAX_THRESHOLD = 5.0, 80.0
PREDICTIONS_PER_TEAM = (2, 3)

# A prediction compares second-half window means with the first half; below these
# first-half totals one or two events would swing the result, so the test is noise.
MIN_BASELINE_TOTAL = {"count": 6.0, "xg": 0.5}
MIN_BASELINE_WINDOWS = 5

ABS_TOLERANCE = 0.05
REL_TOLERANCE = 0.01

_FORMATION = re.compile(r"\b\d(?:-\d){2,3}\b")
_IDENTIFIER = re.compile(r"\b[A-Za-z]+(?:-[A-Za-z0-9_]+)+")  # HAR-11, sig-xg-HAR-p1
_NUMBER = re.compile(r"(?<![\w#.])\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Issue:
    """One reason a draft was rejected."""

    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.where}: {self.message}"


@dataclass
class VerificationReport:
    """The outcome of verifying one draft."""

    issues: list[Issue] = field(default_factory=list)
    model_checked: bool = False

    @property
    def ok(self) -> bool:
        return not self.issues

    def reasons(self) -> list[str]:
        """Issue descriptions, for feeding back to the tactician."""
        return [str(i) for i in self.issues]


# ----- helpers ----------------------------------------------------------------


def normalize_evidence(ids: Iterable[str]) -> list[str]:
    """Reduce cited references to bare signal IDs (``sig-x-HAR-p1=3.1`` -> ``sig-x-HAR-p1``)."""
    out = []
    for raw in ids:
        token = re.split(r"[=:\s(]", raw.strip(), maxsplit=1)[0].strip("`'\",;")
        if token and token not in out:
            out.append(token)
    return out


_COMMA_DECIMAL = re.compile(r"(?<=\d),(?=\d)")
# Spelled-out quantities are numbers too ("three on target"). "one"/"uno"/"una" are left
# out: in prose they are mostly not quantities ("one of their", "una de las").
NUMBER_WORDS = {
    "zero": 0, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "cero": 0, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13,
    "catorce": 14, "quince": 15, "dieciséis": 16, "diecisiete": 17, "dieciocho": 18,
    "diecinueve": 19, "veinte": 20,
}  # fmt: skip
_NUMBER_WORD = re.compile(r"\b(" + "|".join(NUMBER_WORDS) + r")\b", re.IGNORECASE)
# "five minutes" is the window length, not a claim about the match.
_WINDOW_PHRASE = re.compile(r"\b(five|cinco)[ -](minutes?|minutos?)\b", re.IGNORECASE)


def numbers_in(text: str, decimal_comma: bool = False) -> list[float]:
    """Numeric literals in prose, including ordinals and unit suffixes (55th, 20m).

    Formations (4-3-3), identifiers (HAR-11, signal IDs) and #shirt numbers are ignored.
    """
    cleaned = _IDENTIFIER.sub(" ", _FORMATION.sub(" ", text))
    if decimal_comma:  # Spanish: 70,6 means 70.6
        cleaned = _COMMA_DECIMAL.sub(".", cleaned)
    numbers = [float(m) for m in _NUMBER.findall(cleaned)]
    worded = _WINDOW_PHRASE.sub(" ", cleaned)
    numbers += [float(NUMBER_WORDS[w.lower()]) for w in _NUMBER_WORD.findall(worded)]
    return numbers


def _round_half_up(value: float, digits: int) -> float:
    factor = 10**digits
    return math.floor(abs(value) * factor + 0.5) / factor * (1 if value >= 0 else -1)


def matches(number: float, value: float, whole_rounding: bool = True) -> bool:
    """True when ``number`` is ``value`` as a reader would quote it.

    Cited values may be quoted rounded to a whole number; derived values (differences,
    ratios, changes) only within tolerance or to one decimal, so coincidences such as
    a ratio of 3.6 excusing an invented "4" are not accepted.
    """
    if abs(number - value) <= max(ABS_TOLERANCE, REL_TOLERANCE * abs(value)):
        return True
    if number == _round_half_up(value, 1):
        return True
    return whole_rounding and number == _round_half_up(value, 0)


def derived_values(values: Sequence[float]) -> list[tuple[float, bool]]:
    """Sums, differences, ratios and percentage changes between pairs of cited values.

    Each comes with whether it may be quoted rounded to a whole number: percentage
    changes may ("a 33% rise" for 33.3%), differences and ratios may not.
    """
    out: list[tuple[float, bool]] = []
    for a, b in itertools.combinations(values, 2):
        out.append((a + b, False))  # totals, e.g. a full-time score from two halves
    for a, b in itertools.permutations(values, 2):
        out.append((abs(a - b), False))
        if b:
            out.append((a / b, False))
            out.append((abs(a - b) / abs(b) * 100, True))
    return out


def check_numbers(
    text: str,
    cited: dict[str, float],
    where: str,
    extra: Sequence[float] = (),
    decimal_comma: bool = False,
) -> list[Issue]:
    """Every number in ``text`` must be a cited value, a derived value, or in ``extra``."""
    allowed = [*cited.values(), *extra]
    derived = derived_values(list(cited.values()))
    issues = []
    for number in numbers_in(text, decimal_comma):
        if any(matches(number, v) for v in allowed) or any(
            matches(number, v, whole_rounding=whole) for v, whole in derived
        ):
            continue
        shown = ", ".join(f"{k}={v:g}" for k, v in cited.items()) or "none"
        issues.append(
            Issue(where, f"number {number:g} does not match any cited signal (cited: {shown})")
        )
    return issues


def _cited(evidence: Sequence[str], signals: SignalSet, where: str) -> tuple[dict, list[Issue]]:
    issues: list[Issue] = []
    values: dict[str, float] = {}
    if not evidence:
        issues.append(Issue(where, "no evidence cited"))
    for sid in evidence:
        sig = signals.get(sid)
        if sig is None:
            issues.append(Issue(where, f"unknown signal ID {sid}"))
        else:
            values[sid] = sig.value
    return values, issues


_COMPARATIVE = re.compile(
    r"\b(than|compared (?:with|to)|relative to|versus|vs\.?|outshot|outscored|outpassed)\b",
    re.IGNORECASE,
)


def check_comparison(
    point: Point,
    signals: SignalSet,
    where: str,
    team_names: dict[str, set[str]] | None = None,
) -> list[Issue]:
    """A comparison that names a team must cite that team's signals.

    ``team_names`` maps team codes to the names a claim may use for them. Comparisons
    within one team ("24 entries on the left versus 4 on the right") are fine.
    """
    if not team_names or not _COMPARATIVE.search(point.text):
        return []
    cited = {s.team for sid in point.evidence if (s := signals.get(sid)) is not None and s.team}
    text = point.text.lower()
    named = {
        code
        for code, names in team_names.items()
        if any(re.search(rf"\b{re.escape(n.lower())}\b", text) for n in names)
    }
    missing = sorted(named - cited)
    if not cited or not missing:
        return []
    return [
        Issue(
            where,
            f"the claim compares against {', '.join(missing)} but cites no "
            f"{', '.join(missing)} signal; cite the matching signal for that team",
        )
    ]


_PASSIVE_PRESSED = re.compile(
    r"\b(?:being|are|were|is|was|get|got|getting)\s+(?:\w+\s+)?pressed\b", re.IGNORECASE
)


def check_ppda_reading(point: Point, signals: SignalSet, where: str) -> list[Issue]:
    """PPDA measures a team's own pressing; reading it as how hard a team *is pressed* is
    the most common misreading seen in drafts."""
    cites_ppda = any(
        (s := signals.get(sid)) is not None and s.metric.startswith("ppda")
        for sid in point.evidence
    )
    if cites_ppda and _PASSIVE_PRESSED.search(point.text):
        return [
            Issue(
                where, "PPDA measures how hard the named team presses, not how hard it is pressed"
            )
        ]
    return []


_LOSING = re.compile(
    r"\b(?:los(?:e|es|ing|t)|giv(?:e|es|ing) (?:the ball )?away|"
    r"conced(?:e|es|ing|ed))\b",
    re.IGNORECASE,
)


def check_regains_reading(point: Point, signals: SignalSet, where: str) -> list[Issue]:
    """``high_regains-T`` counts team T *winning* the ball high. A point filed under T that
    cites T's own regains while describing T losing the ball has read it backwards."""
    team = where.split(".", 1)[0]
    if not re.fullmatch(r"[A-Z]{3}", team) or not _LOSING.search(point.text):
        return []
    own = [
        sid
        for sid in point.evidence
        if (s := signals.get(sid)) is not None
        and s.metric.startswith("high_regains")
        and s.team == team
    ]
    opponent = [
        sid
        for sid in point.evidence
        if (s := signals.get(sid)) is not None
        and s.metric.startswith("high_regains")
        and s.team not in (None, team)
    ]
    if own and not opponent:
        return [
            Issue(
                where,
                f"high_regains-{team} counts {team} winning the ball high; "
                f"{team} losing it in their own third is the opponent's "
                "high_regains",
            )
        ]
    return []


def check_point(
    point: Point,
    signals: SignalSet,
    where: str,
    team_names: dict[str, set[str]] | None = None,
) -> list[Issue]:
    """Evidence exists, every number is traceable to it, comparisons cite both teams, and
    PPDA is not read backwards."""
    values, issues = _cited(point.evidence, signals, where)
    return (
        issues
        + check_numbers(point.text, values, where)
        + check_comparison(point, signals, where, team_names)
        + check_ppda_reading(point, signals, where)
        + check_regains_reading(point, signals, where)
    )


def team_names(meta: MatchMetadata) -> dict[str, set[str]]:
    """The names a claim may use for each team (code, full name, short name)."""
    return {t.code: {t.code, t.name, t.short_name} for t in (meta.home, meta.away)}


def baseline_problem(
    metric: str, subject_team: str, qualifier: str | None, signals: SignalSet
) -> str | None:
    """Why a first-half baseline is too thin to test against, or None if it is usable."""
    baseline = signals.window_values(metric, subject_team, qualifier, 1)
    if not baseline:
        return "no first-half window values to compare against"
    half = signals.get(make_id(metric, subject_team, qualifier, 1))
    unit = half.unit if half is not None else ""
    if unit in MIN_BASELINE_TOTAL:
        minimum = MIN_BASELINE_TOTAL[unit]
        if sum(baseline) < minimum:
            return (
                f"first-half baseline is too thin to test ({sum(baseline):g} in total, "
                f"need at least {minimum:g}); pick a metric with more events"
            )
    elif len(baseline) < MIN_BASELINE_WINDOWS:
        return (
            f"only {len(baseline)} first-half windows have a value; need at least "
            f"{MIN_BASELINE_WINDOWS}"
        )
    return None


def check_prediction(
    pred: Prediction, team: str, meta: MatchMetadata, signals: SignalSet, where: str
) -> list[Issue]:
    """A prediction must be a test the watcher can evaluate against first-half baselines."""
    issues: list[Issue] = []
    teams = {meta.home.code, meta.away.code}
    if pred.team != team:
        issues.append(Issue(where, f"prediction team {pred.team} is not {team}"))
    if not re.fullmatch(rf"p-{team}-\d+", pred.prediction_id):
        issues.append(Issue(where, f"prediction_id must look like p-{team}-1"))
    if pred.subject_team not in teams:
        issues.append(Issue(where, f"subject_team must be one of {sorted(teams)}"))
    if pred.metric not in EVALUABLE_METRICS:
        issues.append(
            Issue(
                where,
                f"metric {pred.metric} is not evaluable per window; use one "
                f"of {sorted(EVALUABLE_METRICS)}",
            )
        )
    scope_key = SCOPED_METRICS.get(pred.metric)
    qualifier = None
    if scope_key:
        qualifier = pred.scope.get(scope_key)
        if set(pred.scope) != {scope_key} or qualifier not in CHANNELS:
            issues.append(
                Issue(
                    where, f"{pred.metric} needs scope {{'{scope_key}': one of {sorted(CHANNELS)}}}"
                )
            )
    elif pred.scope:
        issues.append(Issue(where, f"{pred.metric} takes an empty scope"))
    if not MIN_THRESHOLD <= pred.threshold_pct <= MAX_THRESHOLD:
        issues.append(Issue(where, f"threshold_pct must be {MIN_THRESHOLD:g}-{MAX_THRESHOLD:g}"))
    if not MIN_EVALUATE_MINUTE <= pred.evaluate_after_minute <= MAX_EVALUATE_MINUTE:
        issues.append(
            Issue(
                where,
                f"evaluate_after_minute must be {MIN_EVALUATE_MINUTE}-"
                f"{MAX_EVALUATE_MINUTE} so enough windows complete",
            )
        )
    if pred.status is not PredictionStatus.OPEN:
        issues.append(Issue(where, "new predictions must have status open"))
    if pred.metric in EVALUABLE_METRICS and pred.subject_team in teams:
        problem = baseline_problem(pred.metric, pred.subject_team, qualifier, signals)
        if problem:
            issues.append(Issue(where, problem))
    values, evidence_issues = _cited(pred.evidence, signals, where)
    if pred.evidence:
        issues.extend(evidence_issues)
    issues.extend(
        check_numbers(
            pred.claim, values, where, extra=[pred.threshold_pct, pred.evaluate_after_minute]
        )
    )
    return issues


def check_briefing(
    draft: BriefingDraft, signals: SignalSet, meta: MatchMetadata
) -> VerificationReport:
    """Run every deterministic check on a half-time briefing draft."""
    issues: list[Issue] = []
    names = team_names(meta)
    codes = [t.team for t in draft.teams]
    if sorted(codes) != sorted([meta.home.code, meta.away.code]):
        issues.append(
            Issue(
                "teams",
                f"need exactly one read each for {meta.home.code} and "
                f"{meta.away.code}, got {codes}",
            )
        )
    values, headline_issues = _cited(draft.headline_evidence, signals, "headline")
    issues += headline_issues + check_numbers(draft.headline, values, "headline")
    seen_ids: set[str] = set()
    for read in draft.teams:
        if not read.working:
            issues.append(Issue(read.team, "needs at least one point in working"))
        if not read.not_working:
            issues.append(Issue(read.team, "needs at least one point in not_working"))
        lo, hi = PREDICTIONS_PER_TEAM
        if not lo <= len(read.predictions) <= hi:
            issues.append(Issue(read.team, f"needs {lo}-{hi} predictions"))
        for kind in ("working", "not_working"):
            for i, point in enumerate(getattr(read, kind)):
                issues += check_point(point, signals, f"{read.team}.{kind}[{i}]", names)
        for i, pred in enumerate(read.predictions):
            where = f"{read.team}.predictions[{i}]"
            if pred.prediction_id in seen_ids:
                issues.append(Issue(where, f"duplicate prediction_id {pred.prediction_id}"))
            seen_ids.add(pred.prediction_id)
            issues += check_prediction(pred, read.team, meta, signals, where)
    return VerificationReport(issues)


_LOCATION = re.compile(r"^([A-Z]{3})\.(working|not_working|predictions)\[(\d+)\]$")
MIN_POINTS = 1


def prune(draft: BriefingDraft, issues: Sequence[Issue]) -> tuple[BriefingDraft, list[str]] | None:
    """Remove the claims that failed, if every issue is tied to one removable claim and
    each team still has a point in each section and at least two predictions.

    Returns the pruned draft and descriptions of what was removed, or None.
    """
    targets: dict[tuple[str, str], set[int]] = {}
    for issue in issues:
        match = _LOCATION.match(issue.where)
        if match is None:
            return None  # headline or structural problems cannot be pruned
        team, kind, index = match.group(1), match.group(2), int(match.group(3))
        targets.setdefault((team, kind), set()).add(index)
    pruned = draft.model_copy(deep=True)
    removed: list[str] = []
    for read in pruned.teams:
        for kind in ("working", "not_working", "predictions"):
            drop = targets.get((read.team, kind), set())
            items = getattr(read, kind)
            keep = [x for i, x in enumerate(items) if i not in drop]
            minimum = PREDICTIONS_PER_TEAM[0] if kind == "predictions" else MIN_POINTS
            if len(keep) < minimum:
                return None
            for i in sorted(drop):
                if i < len(items):
                    item = items[i]
                    text = item.claim if kind == "predictions" else item.text
                    reasons = "; ".join(
                        x.message for x in issues if x.where == f"{read.team}.{kind}[{i}]"
                    )
                    removed.append(f"{read.team}.{kind}: {text} -- {reasons}")
            setattr(read, kind, keep)
    return pruned, removed


_INLINE_IDS = re.compile(r"\s*\((?:\s*sig-[A-Za-z0-9_-]+\s*,?)+\)|\bsig-[A-Za-z0-9_-]+\b")


def strip_inline_ids(text: str) -> str:
    """Remove signal IDs a model wrote into prose ("... 70.6% (sig-x-HAR-p1).")."""
    cleaned = _INLINE_IDS.sub("", text)
    return re.sub(r"\s+([.,;:])", r"\1", re.sub(r"\s{2,}", " ", cleaned)).strip()


def normalize_draft(draft: BriefingDraft) -> BriefingDraft:
    """Fix formatting slips so they are not grounding failures: decorated evidence IDs, and
    signal IDs written into the text."""
    data = draft.model_copy(deep=True)
    data.headline = strip_inline_ids(data.headline)
    data.headline_evidence = normalize_evidence(data.headline_evidence)
    for read in data.teams:
        for point in [*read.working, *read.not_working]:
            point.text = strip_inline_ids(point.text)
            point.evidence = normalize_evidence(point.evidence)
        for pred in read.predictions:
            pred.evidence = normalize_evidence(pred.evidence)
    return data


# ----- layer 2: model support check -----------------------------------------


class ClaimCheck(BaseModel):
    """The model's judgement on one claim."""

    ref: str
    supported: bool
    reason: str = Field(description="One short sentence.")


class SupportChecks(BaseModel):
    """Structured output for the support check."""

    checks: list[ClaimCheck]


SUPPORT_INSTRUCTIONS = """You audit football analysis before it goes on air. For each claim you
get the exact values of the signals it cites, with what each metric means. A claim is
supported only if everything it asserts follows from those values. Numbers have already
been checked; judge meaning. Mark a claim unsupported when it:
- misreads a direction or a team (a lower PPDA means a MORE intense press; a higher
  turnover_height means winning the ball further up the pitch; a turnover_height below
  52.5 m is in the team's own half; each signal describes the team named in its ID);
- compares the two teams without citing values for both;
- asserts a cause, an effect or a forecast the values do not show ("which led to the
  goal", "a clear avenue to exploit", "risks tiring", "producing chances");
- contradicts itself;
- sits in the wrong category: a "working" point must describe something going well for
  the team it is filed under, a "not_working" point something going badly for it.
Plain descriptions of what the values show, with fair everyday wording, are supported.
Be concise."""


def claims_for_support_check(draft: BriefingDraft, signals: SignalSet) -> list[dict]:
    """The claims of a draft with the values of their cited signals."""

    from second_act.agents.tactician import METRIC_NOTES

    def describe(ids: Sequence[str]) -> list[str]:
        out = []
        for sid in ids:
            sig = signals.get(sid)
            if sig is not None:
                note = METRIC_NOTES.get(sig.metric, "")
                out.append(f"{sid} = {sig.value:g} {sig.unit} ({note})")
        return out

    claims = [
        {"ref": "headline", "text": draft.headline, "evidence": describe(draft.headline_evidence)}
    ]
    for read in draft.teams:
        for kind in ("working", "not_working"):
            for i, point in enumerate(getattr(read, kind)):
                claims.append(
                    {
                        "ref": f"{read.team}.{kind}[{i}]",
                        "category": kind,
                        "text": point.text,
                        "evidence": describe(point.evidence),
                    }
                )
    return claims


class ModelSupportChecker:
    """Layer 2: one batched model call judging whether each claim follows from its evidence."""

    def __init__(self, agent) -> None:
        self.agent = agent

    async def check(self, claims: list[dict]) -> tuple[list[Issue], dict]:
        """Return issues for unsupported claims, plus latency and usage for the log."""
        import json
        import time

        start = time.perf_counter()
        response = await self.agent.run(
            "Check each claim:\n" + json.dumps(claims, indent=1),
            options={"response_format": SupportChecks},
        )
        latency = round(time.perf_counter() - start, 2)
        result = response.value
        if not isinstance(result, SupportChecks):
            result = SupportChecks.model_validate_json(response.text)
        refs = {c["ref"] for c in claims}
        issues = [
            Issue(c.ref, f"claim not supported by its evidence: {c.reason}")
            for c in result.checks
            if not c.supported and c.ref in refs
        ]
        return issues, {"latency_s": latency, "usage": dict(response.usage_details or {})}
