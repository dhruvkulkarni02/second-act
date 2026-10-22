"""Presenter: turns everything the other agents produce into overlay cues.

Each source becomes one cue per variant: audience (analyst, casual) x language
(en, es) x club focus (none, home, away), generated when the source is created so
the frontend can switch instantly. Template content (live triggers, player tags,
template briefings and recaps, template explanations) is rendered from the phrase
catalogue. Model-written content is rewritten by the model for the other audience
and language variants; each rewritten line must pass the deterministic number
check (Spanish decimal commas included) or the English analyst line is used.
Club focus tells the same facts from that club's side: favourable or worrying
framing for live cues, the club's read first in briefings and recaps.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from second_act.agents.analyst import LiveItem
from second_act.agents.verifier import check_numbers
from second_act.agents.watcher import PredictionCheck, explanation_phrase
from second_act.analytics.signals import SignalSet
from second_act.i18n.catalog import AUDIENCES, LANGUAGES, Audience, Language, render
from second_act.schema.briefing import Briefing, BriefingDraft
from second_act.schema.cues import Cue, CueKind
from second_act.schema.metadata import MatchMetadata
from second_act.schema.predictions import PredictionStatus
from second_act.schema.recap import Recap, RecapDraft

VARIANTS: list[tuple[Audience, Language]] = [(a, lang) for a in AUDIENCES for lang in LANGUAGES]
DURATION_S = {
    CueKind.LIVE_TRIGGER: 8,
    CueKind.PLAYER_TAG: 6,
    CueKind.PREDICTION_CHECK: 10,
    CueKind.HALFTIME_BRIEFING: 20,
    CueKind.FULLTIME_RECAP: 30,
}


class BriefingVariant(BaseModel):
    """The half-time briefing for one audience, language and club focus."""

    model_config = ConfigDict(extra="forbid")

    audience: Audience
    language: Language
    club_focus: str | None
    briefing: BriefingDraft


class RecapVariant(BaseModel):
    """The full-time recap for one audience, language and club focus."""

    model_config = ConfigDict(extra="forbid")

    audience: Audience
    language: Language
    club_focus: str | None
    recap: RecapDraft


def match_time(minute: float) -> str:
    """``52.17`` -> ``52:10``."""
    total = max(0, round(minute * 60))
    return f"{total // 60}:{total % 60:02d}"


# ----- model localization -------------------------------------------------------------


class LocalizedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str
    text: str


class LocalizedItems(BaseModel):
    """Structured output for the localizer."""

    model_config = ConfigDict(extra="forbid")

    items: list[LocalizedItem]


LOCALIZER_INSTRUCTIONS = """You adapt football broadcast graphics text for one audience and
language. Rewrite every item you are given and return each one with its ref.
- analyst: precise and compact; keep every number.
- casual: plain words for fans, short; you may drop numbers and jargon (PPDA, xG) but
  never add a number that is not in the item.
- es: natural Spanish football language; decimal comma (70,6); keep team and player names.
- en: natural English.
Never change a fact, a direction (rise/fall) or a team, and never reinterpret a metric:
a LOWER PPDA means MORE intense pressing; a higher turnover height means winning the ball
further up the pitch; "high regains" are ball wins in the opponent's third."""


class Localizer(Protocol):
    """Rewrites items for one audience and language."""

    async def localize(
        self, items: list[dict], audience: Audience, language: Language
    ) -> tuple[dict[str, str], float, dict]: ...


class ModelLocalizer:
    """Online localizer: an Agent Framework agent with strict structured output."""

    def __init__(self, agent) -> None:
        self.agent = agent

    async def localize(self, items, audience, language):
        """Return ref -> text, latency and usage."""
        import json

        prompt = f"Audience: {audience}. Language: {language}.\nItems:\n" + json.dumps(
            [{"ref": i["ref"], "text": i["text"]} for i in items], indent=1
        )
        start = time.perf_counter()
        response = await self.agent.run(prompt, options={"response_format": LocalizedItems})
        latency = round(time.perf_counter() - start, 2)
        out = response.value
        if not isinstance(out, LocalizedItems):
            out = LocalizedItems.model_validate_json(response.text)
        return {i.ref: i.text for i in out.items}, latency, dict(response.usage_details or {})


@dataclass
class _Item:
    ref: str
    text: str
    values: dict[str, float]
    extra: list[float] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"ref": self.ref, "text": self.text}


# ----- presenter ----------------------------------------------------------------------


class Presenter:
    """Builds cues and audience/language/club variants."""

    def __init__(self, meta: MatchMetadata, localizer: Localizer | None = None, log=None) -> None:
        self.meta, self.localizer, self.log = meta, localizer, log
        self._next = 0
        self.focuses: list[str | None] = [None, meta.home.code, meta.away.code]

    def _id(self) -> str:
        self._next += 1
        return f"c-{self._next:04d}"

    def _frame(
        self,
        focus: str | None,
        favours: str | None,
        against: str | None,
        audience: Audience,
        language: Language,
    ) -> str:
        if focus is None:
            return ""
        club = self.meta.team(focus).short_name
        if favours == focus:
            return render("f_good", {"club": club}, audience, language)
        if against == focus or (favours is not None and favours != focus):
            return render("f_bad", {"club": club}, audience, language)
        return ""

    def _cues(
        self,
        kind: CueKind,
        minute: float,
        texts: dict[tuple[Audience, Language], tuple],
        evidence: list[str],
        favours: str | None = None,
        against: str | None = None,
        **extra: Any,
    ) -> list[Cue]:
        out = []
        for audience, language in VARIANTS:
            headline, body = texts[(audience, language)]
            for focus in self.focuses:
                prefix = self._frame(focus, favours, against, audience, language)
                out.append(
                    Cue(
                        cue_id=self._id(),
                        match_time=match_time(minute),
                        duration_s=DURATION_S[kind],
                        kind=kind,
                        audience=audience,
                        language=language,
                        club_focus=focus,
                        headline=prefix + headline,
                        body=body,
                        evidence=evidence,
                        **extra,
                    )
                )
        return out

    # ----- live triggers and player tags -----

    def live(self, item: LiveItem) -> list[Cue]:
        """Cues for a live trigger or player tag (template text, all variants)."""
        texts = {
            (a, lang): (item.headline.render(a, lang), item.body.render(a, lang))
            for a, lang in VARIANTS
        }
        return self._cues(
            CueKind(item.kind), item.minute, texts, item.evidence, favours=item.favours
        )

    # ----- prediction checks -----

    async def prediction(self, check: PredictionCheck, minute: float) -> list[Cue]:
        """Cues for a prediction status change."""
        pred, ev = check.prediction, check.evaluation
        status = pred.status
        head_key = f"x_head_{status.value}"
        # The catalogue states every verdict correctly in all variants; a model-written
        # (and verified) sentence replaces only the English analyst line.
        phrase = explanation_phrase(pred, ev, self.meta)
        bodies = {(a, lang): phrase.render(a, lang) for a, lang in VARIANTS}
        if check.source == "model":
            bodies[("analyst", "en")] = check.explanation
        texts = {
            (a, lang): (render(head_key, {}, a, lang), bodies[(a, lang)]) for a, lang in VARIANTS
        }
        favours = against = None
        if pred.subject_team != pred.team:  # the manager's change aimed at the opponent
            if status is PredictionStatus.CONFIRMED:
                favours = pred.team
            else:
                against = pred.team
        return self._cues(
            CueKind.PREDICTION_CHECK,
            minute,
            texts,
            ev.evidence,
            favours,
            against,
            prediction_id=pred.prediction_id,
            status=status,
        )

    # ----- briefing -----

    async def briefing_variants(
        self, briefing: Briefing, signals: SignalSet, template=None
    ) -> list[BriefingVariant]:
        """Every variant of the half-time briefing."""
        if briefing.source == "template" and template is not None:
            drafts = {v: template.render(*v) for v in VARIANTS}
        else:
            items = _briefing_items(briefing, signals)
            localized = await self._localize_all(items, "briefing")
            drafts = {v: _apply_briefing(briefing, localized[v]) for v in VARIANTS}
        out = []
        for (audience, language), draft in drafts.items():
            for focus in self.focuses:
                out.append(
                    BriefingVariant(
                        audience=audience,
                        language=language,
                        club_focus=focus,
                        briefing=self._focus_briefing(draft, focus, audience, language),
                    )
                )
        return out

    def source_variants(self, briefing: Briefing) -> list[BriefingVariant]:
        """The English analyst variants (one per club focus), available without any model call."""
        draft = BriefingDraft.model_validate(
            briefing.model_dump(include=set(BriefingDraft.model_fields))
        )
        return [
            BriefingVariant(
                audience="analyst",
                language="en",
                club_focus=focus,
                briefing=self._focus_briefing(draft, focus, "analyst", "en"),
            )
            for focus in self.focuses
        ]

    def _focus_briefing(self, draft: BriefingDraft, focus, audience, language) -> BriefingDraft:
        if focus is None:
            return draft
        club = self.meta.team(focus).short_name
        teams = sorted(draft.teams, key=lambda t: t.team != focus)
        return draft.model_copy(
            update={
                "headline": render("f_view", {"club": club}, audience, language) + draft.headline,
                "teams": teams,
            }
        )

    def briefing_cues(self, variants: list[BriefingVariant], minute: float) -> list[Cue]:
        """One half-time cue per variant: the headline, plus the focus (or first) team's
        leading point."""
        out = []
        for v in variants:
            lead = v.briefing.teams[0]
            point = lead.working[0] if lead.working else lead.not_working[0]
            out.append(
                Cue(
                    cue_id=self._id(),
                    match_time=match_time(minute),
                    duration_s=DURATION_S[CueKind.HALFTIME_BRIEFING],
                    kind=CueKind.HALFTIME_BRIEFING,
                    audience=v.audience,
                    language=v.language,
                    club_focus=v.club_focus,
                    headline=v.briefing.headline,
                    body=point.text,
                    evidence=v.briefing.headline_evidence + point.evidence,
                )
            )
        return out

    # ----- recap -----

    async def recap_variants(
        self, recap: Recap, signals: SignalSet, template=None, predictions=()
    ) -> list[RecapVariant]:
        """Every variant of the full-time recap."""
        if recap.source == "template" and template is not None:
            drafts = {v: template.render(*v) for v in VARIANTS}
        else:
            thresholds = {p.prediction_id: [p.threshold_pct] for p in predictions}
            items = _recap_items(recap, signals, thresholds)
            localized = await self._localize_all(items, "recap")
            drafts = {v: _apply_recap(recap, localized[v]) for v in VARIANTS}
        out = []
        for (audience, language), draft in drafts.items():
            for focus in self.focuses:
                d = draft
                if focus is not None:
                    club = self.meta.team(focus).short_name
                    d = draft.model_copy(
                        update={
                            "headline": render("f_view", {"club": club}, audience, language)
                            + draft.headline
                        }
                    )
                out.append(
                    RecapVariant(audience=audience, language=language, club_focus=focus, recap=d)
                )
        return out

    def recap_cues(self, variants: list[RecapVariant], minute: float) -> list[Cue]:
        """One full-time cue per variant."""
        out = []
        for v in variants:
            point = v.recap.result[0]
            out.append(
                Cue(
                    cue_id=self._id(),
                    match_time=match_time(minute),
                    duration_s=DURATION_S[CueKind.FULLTIME_RECAP],
                    kind=CueKind.FULLTIME_RECAP,
                    audience=v.audience,
                    language=v.language,
                    club_focus=v.club_focus,
                    headline=v.recap.headline,
                    body=point.text,
                    evidence=v.recap.headline_evidence + point.evidence,
                )
            )
        return out

    # ----- localization -----

    async def _localize_all(self, items: list[_Item], what: str) -> dict:
        """ref -> text for every variant; analyst/en is the source itself."""
        source = {i.ref: i.text for i in items}
        result: dict[tuple[Audience, Language], dict[str, str]] = {("analyst", "en"): source}
        others = [v for v in VARIANTS if v != ("analyst", "en")]
        if self.localizer is None:
            for v in others:
                result[v] = dict(source)
            return result

        async def one(variant):
            audience, language = variant
            try:
                texts, latency, usage = await self.localizer.localize(
                    [i.as_dict() for i in items], audience, language
                )
            except Exception as exc:
                self._record(what, variant, None, None, [f"model error: {exc}"[:300]], len(items))
                return variant, dict(source)
            out, rejected = {}, []
            for item in items:
                text = texts.get(item.ref)
                problems = [] if text else ["missing"]
                if text:
                    problems = [
                        str(x)
                        for x in check_numbers(
                            text, item.values, item.ref, item.extra, decimal_comma=language == "es"
                        )
                    ]
                if problems:
                    rejected.append(f"{item.ref}: {'; '.join(problems)}")
                    out[item.ref] = item.text
                else:
                    out[item.ref] = text
            self._record(what, variant, latency, usage, rejected, len(items))
            return variant, out

        for variant, texts in await asyncio.gather(*(one(v) for v in others)):
            result[variant] = texts
        return result

    def _record(self, what, variant, latency, usage, rejected, n):
        if self.log is None:
            return
        from second_act.orchestration.agent_log import usage_fields

        self.log.record(
            "presenter",
            "localize",
            task=what,
            audience=variant[0],
            language=variant[1],
            items=n,
            rejected=rejected,
            latency_s=latency,
            **usage_fields(usage),
        )


def _values(ids, signals: SignalSet) -> dict[str, float]:
    return {sid: signals.get(sid).value for sid in ids if sid in signals}  # type: ignore[union-attr]


def _briefing_items(b: BriefingDraft, signals: SignalSet) -> list[_Item]:
    items = [_Item("headline", b.headline, _values(b.headline_evidence, signals))]
    for read in b.teams:
        for kind in ("working", "not_working"):
            for i, p in enumerate(getattr(read, kind)):
                items.append(
                    _Item(f"{read.team}.{kind}[{i}]", p.text, _values(p.evidence, signals))
                )
        for i, p in enumerate(read.predictions):
            items.append(
                _Item(
                    f"{read.team}.predictions[{i}]",
                    p.claim,
                    _values(p.evidence, signals),
                    [p.threshold_pct, p.evaluate_after_minute],
                )
            )
    return items


def _apply_briefing(b: BriefingDraft, texts: dict[str, str]) -> BriefingDraft:
    draft = BriefingDraft(
        headline=texts["headline"],
        headline_evidence=b.headline_evidence,
        teams=[t.model_copy(deep=True) for t in b.teams],
    )
    for read in draft.teams:
        for kind in ("working", "not_working"):
            for i, p in enumerate(getattr(read, kind)):
                p.text = texts[f"{read.team}.{kind}[{i}]"]
        read.predictions = [
            p.model_copy(update={"claim": texts[f"{read.team}.predictions[{i}]"]})
            for i, p in enumerate(read.predictions)
        ]
    return draft


RECAP_SECTIONS = ("first_half", "changes", "result")


def _recap_items(
    r: RecapDraft, signals: SignalSet, thresholds: dict[str, list[float]] | None = None
) -> list[_Item]:
    items = [_Item("headline", r.headline, _values(r.headline_evidence, signals))]
    for section in RECAP_SECTIONS:
        for i, p in enumerate(getattr(r, section)):
            items.append(_Item(f"{section}[{i}]", p.text, _values(p.evidence, signals)))
    for i, p in enumerate(r.predictions):
        items.append(
            _Item(
                f"predictions[{i}]",
                p.text,
                _values(p.evidence, signals),
                (thresholds or {}).get(p.prediction_id, []),
            )
        )
    return items


def _apply_recap(r: RecapDraft, texts: dict[str, str]) -> RecapDraft:
    data = RecapDraft.model_validate(r.model_dump(include=set(RecapDraft.model_fields)))
    data.headline = texts["headline"]
    for section in RECAP_SECTIONS:
        for i, p in enumerate(getattr(data, section)):
            p.text = texts[f"{section}[{i}]"]
    for i, p in enumerate(data.predictions):
        p.text = texts[f"predictions[{i}]"]
    return data
