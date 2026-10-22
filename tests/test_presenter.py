"""Presenter: every cue in every variant, club framing, model-localized variants."""

from __future__ import annotations

import asyncio

import pytest

from second_act.agents.presenter import VARIANTS, Presenter, match_time
from second_act.orchestration.agent_log import AgentLog
from second_act.schema.briefing import Briefing
from second_act.schema.cues import Cue, CueKind
from second_act.schema.recap import Recap


class FakeLocalizer:
    """Pretends to translate: Spanish keeps numbers with decimal commas; one item lies."""

    def __init__(self, lie_ref: str | None = None) -> None:
        self.lie_ref, self.calls = lie_ref, 0

    async def localize(self, items, audience, language):
        self.calls += 1
        out = {}
        for item in items:
            text = f"[{audience}/{language}] " + item["text"]
            if language == "es":
                text = text.replace(".", ",") if any(c.isdigit() for c in text) else text
            if item["ref"] == self.lie_ref:
                text += " 99"
            out[item["ref"]] = text
        return out, 0.01, {}


def test_match_time() -> None:
    assert match_time(52.1667) == "52:10" and match_time(0) == "0:00"


def test_stream_has_every_kind_in_every_variant(finished) -> None:
    _, messages = finished
    cues = [Cue.model_validate(d) for k, d in messages if k == "cue"]
    kinds = {c.kind for c in cues}
    assert kinds == set(CueKind)
    for kind in CueKind:
        combos = {(c.audience, c.language, c.club_focus) for c in cues if c.kind is kind}
        assert len(combos) == 12, kind
    first_half_live = [
        c for c in cues if c.kind is CueKind.LIVE_TRIGGER and int(c.match_time.split(":")[0]) < 45
    ]
    assert first_half_live
    assert all(c.evidence for c in cues)


def test_variants_differ_by_audience_and_language(finished) -> None:
    _, messages = finished
    cues = [Cue.model_validate(d) for k, d in messages if k == "cue"]
    trigger = [c for c in cues if c.kind is CueKind.LIVE_TRIGGER and c.club_focus is None][:4]
    bodies = {(c.audience, c.language): c.body for c in trigger}
    assert len(set(bodies.values())) == 4


def test_club_framing(finished) -> None:
    _, messages = finished
    cues = [Cue.model_validate(d) for k, d in messages if k == "cue"]
    surge = [
        c
        for c in cues
        if c.kind is CueKind.LIVE_TRIGGER
        and "left" in c.body
        and c.audience == "casual"
        and c.language == "en"
    ]
    framing = {c.club_focus: c.headline for c in surge[:3]}
    assert framing["HAR"].startswith("Good news for Harbour fans: ")
    assert framing["KES"].startswith("Worry for Kestrel: ")
    assert not framing[None].startswith(("Good", "Worry"))


def test_model_briefing_is_localized_and_number_checked(finished) -> None:
    runner, _ = finished
    state = runner.state
    briefing = Briefing(**state.briefing.model_dump(exclude={"source"}), source="model")
    localizer = FakeLocalizer(lie_ref="HAR.working[0]")
    log = AgentLog(write=False)
    presenter = Presenter(state.meta, localizer, log)
    first_half = runner.state.signals  # full-time signals include the first half
    variants = asyncio.run(presenter.briefing_variants(briefing, first_half))
    assert len(variants) == 12 and localizer.calls == 3
    es = next(
        v for v in variants if (v.audience, v.language, v.club_focus) == ("analyst", "es", None)
    )
    assert es.briefing.headline.startswith("[analyst/es]")
    # The lying line fails the number check and falls back to the English analyst text.
    assert es.briefing.teams[0].working[0].text == briefing.teams[0].working[0].text
    rejected = [r for r in log.records if r["event"] == "localize" and r["rejected"]]
    assert len(rejected) == 3
    kes = next(v for v in variants if v.club_focus == "KES")
    assert kes.briefing.teams[0].team == "KES" and kes.briefing.headline.startswith("For Kestrel")


def test_model_recap_variants(finished) -> None:
    runner, _ = finished
    state = runner.state
    recap = Recap(**state.recap.model_dump(exclude={"source"}), source="model")
    variants = asyncio.run(
        Presenter(state.meta, FakeLocalizer()).recap_variants(
            recap, state.signals, predictions=state.predictions
        )
    )
    assert len(variants) == 12
    assert {(v.audience, v.language) for v in variants} == set(VARIANTS)


@pytest.mark.parametrize("status_source", ["template"])
def test_prediction_cues_from_template(finished, status_source) -> None:
    _, messages = finished
    checks = [
        Cue.model_validate(d) for k, d in messages if k == "cue" and d["kind"] == "prediction_check"
    ]
    confirmed = {c.prediction_id for c in checks if c.status and c.status.value == "confirmed"}
    contradicted = {
        c.prediction_id for c in checks if c.status and c.status.value == "contradicted"
    }
    assert "p-KES-1" in confirmed and "p-KES-2" in contradicted
