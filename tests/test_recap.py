"""Recap: accountability and grounding checks, repair, template on the demo match."""

from __future__ import annotations

from second_act.agents.recap import check_recap, prune_recap, template_recap
from second_act.agents.verifier import Issue


def test_template_recap_passes(finished) -> None:
    runner, _ = finished
    state = runner.state
    assert state.recap is not None and state.recap.source == "template"
    assert check_recap(template_recap(state), state) == []
    assert {r.prediction_id for r in state.recap.predictions} == {
        p.prediction_id for p in state.predictions
    }


def test_recap_must_carry_the_watchers_status(finished) -> None:
    state = finished[0].state
    draft = template_recap(state)
    review = next(r for r in draft.predictions if r.prediction_id == "p-KES-2")
    review.status = "confirmed"  # it was contradicted
    issues = [str(i) for i in check_recap(draft, state)]
    assert any("p-KES-2 was contradicted, not confirmed" in i for i in issues)


def test_recap_must_review_every_prediction(finished) -> None:
    state = finished[0].state
    draft = template_recap(state)
    draft.predictions = draft.predictions[1:]
    assert any("missing reviews" in str(i) for i in check_recap(draft, state))


def test_recap_rejects_invented_number_and_repairs_it(finished) -> None:
    state = finished[0].state
    draft = template_recap(state)
    draft.predictions[0].text += " They had 9 chances."
    draft.changes[0].text += " Over 12 minutes."
    issues = check_recap(draft, state)
    assert {i.where for i in issues} == {"predictions[0]", "changes[0]"}
    repaired, removed = prune_recap(draft, issues, state)
    assert check_recap(repaired, state) == []
    assert "replaced by the watcher's note" in removed[-1]
    assert prune_recap(draft, [Issue("headline", "x")], state) is None
