"""Half-time workflow (offline): verified briefing, retry on rejection, fallback, logging."""

from __future__ import annotations

import asyncio
import json

import pytest

from second_act.agents.tactician import DraftResult, template_briefing
from second_act.agents.verifier import Issue
from second_act.orchestration.agent_log import AgentLog
from second_act.orchestration.halftime import MAX_RETRIES, run_halftime
from second_act.schema.io import read_events, read_metadata, read_snapshots
from second_act.state import MatchState
from tests.conftest import DATA


def _state() -> MatchState:
    folder = DATA / "m-001"
    return MatchState.at_first_half_end(
        read_events(folder / "events.jsonl"),
        read_metadata(folder / "metadata.json"),
        read_snapshots(folder / "snapshots.jsonl.gz"),
    )


@pytest.fixture
def state() -> MatchState:
    return _state()


class ScriptedDrafter:
    """Returns the template, corrupted on the attempts listed in ``bad``."""

    source = "model"

    def __init__(self, bad: set[int], raise_on: set[int] = frozenset()) -> None:
        self.bad, self.raise_on, self.calls = bad, raise_on, 0
        self.feedback: list[list[str] | None] = []

    async def draft(self, state, feedback=None, previous=None) -> DraftResult:
        self.calls += 1
        self.feedback.append(feedback)
        if self.calls in self.raise_on:
            raise RuntimeError("model timed out")
        draft = template_briefing(state.signals, state.meta)
        if self.calls in self.bad:
            draft.teams[0].working[0].text += " They hit the woodwork 4 times."
        return DraftResult(
            draft,
            "model",
            latency_s=0.01,
            usage={"input_token_count": 100, "output_token_count": 50},
        )


class RejectAll:
    async def check(self, draft, signals):
        return [Issue("headline", "claim not supported by its evidence: test")], {
            "latency_s": 0.0,
            "usage": {},
        }


def run(state, **kwargs):
    kwargs.setdefault("parallel_drafts", 1)
    return asyncio.run(run_halftime(state, **kwargs))


def test_offline_briefing_is_verified_and_stored(state: MatchState, tmp_path) -> None:
    log = AgentLog(session_id="t1", directory=tmp_path)
    briefing = run(state, offline=True, log=log)
    assert briefing.source == "template" and briefing.attempts == 1
    assert briefing.fallback_reason is None
    assert state.briefing is briefing
    assert {p.prediction_id for p in state.predictions} >= {"p-KES-1", "p-KES-2"}
    assert all(p.evidence for p in state.predictions)
    lines = [json.loads(x) for x in (tmp_path / "session-t1.jsonl").read_text().splitlines()]
    assert [(r["agent"], r["event"]) for r in lines] == [
        ("tactician", "start"),
        ("tactician", "draft"),
        ("verifier", "verdict"),
        ("workflow", "halftime_done"),
    ]


def test_invented_number_is_rejected_then_fixed_on_retry(state: MatchState) -> None:
    drafter = ScriptedDrafter(bad={1})
    log = AgentLog(write=False)
    briefing = run(state, offline=False, log=log, drafter=drafter, supporter=None)
    assert briefing.source == "model" and briefing.attempts == 2
    assert drafter.feedback[0] is None
    assert any("number 4 does not match" in r for r in drafter.feedback[1])
    verdicts = [r for r in log.records if r["event"] == "verdict"]
    assert [v["ok"] for v in verdicts] == [False, True]
    summary = log.summary()
    assert summary["rejections"] == 1 and summary["fallbacks"] == 0
    assert summary["tokens_in"] == 200


def test_fallback_after_repeated_rejections(state: MatchState) -> None:
    drafter = ScriptedDrafter(bad={1, 2, 3, 4})
    log = AgentLog(write=False)
    briefing = run(state, offline=False, log=log, drafter=drafter, supporter=None)
    assert drafter.calls == MAX_RETRIES + 1
    assert briefing.source == "template"
    assert briefing.fallback_reason == f"all drafts rejected after {MAX_RETRIES + 1} rounds"
    assert "woodwork" not in json.dumps(briefing.model_dump())
    assert log.summary()["fallbacks"] == 1


def test_model_error_counts_as_failed_attempt(state: MatchState) -> None:
    drafter = ScriptedDrafter(bad=set(), raise_on={1})
    log = AgentLog(write=False)
    briefing = run(state, offline=False, log=log, drafter=drafter, supporter=None)
    assert briefing.source == "model" and briefing.attempts == 2
    assert any("model timed out" in r for r in drafter.feedback[1])


def test_support_check_rejection_triggers_retry(state: MatchState) -> None:
    drafter = ScriptedDrafter(bad=set())
    log = AgentLog(write=False)
    briefing = run(state, offline=False, log=log, drafter=drafter, supporter=RejectAll())
    assert briefing.source == "template"  # every draft rejected by the model layer
    assert drafter.calls == MAX_RETRIES + 1
    layers = [r["layer"] for r in log.records if r["event"] == "verdict"]
    assert layers == ["model"] * (MAX_RETRIES + 1)


def test_time_budget_forces_fallback(state: MatchState) -> None:
    drafter = ScriptedDrafter(bad={1, 2, 3})
    log = AgentLog(write=False)
    briefing = run(state, offline=False, log=log, drafter=drafter, supporter=None, budget_s=0)
    assert drafter.calls == 1
    assert briefing.fallback_reason == "time budget exceeded"


def test_offline_mode_never_calls_the_support_checker(state: MatchState) -> None:
    log = AgentLog(write=False)
    briefing = run(state, offline=True, log=log, supporter=RejectAll())
    assert briefing.source == "template" and briefing.attempts == 1


def test_parallel_drafts_keep_the_first_good_candidate(state: MatchState) -> None:
    drafter = ScriptedDrafter(bad={1})  # the first of two concurrent drafts is bad
    log = AgentLog(write=False)
    briefing = run(
        state, offline=False, log=log, drafter=drafter, supporter=None, parallel_drafts=2
    )
    assert drafter.calls == 2  # one round, no retry
    assert briefing.source == "model" and briefing.attempts == 2
    verdicts = [(r["candidate"], r["ok"]) for r in log.records if r["event"] == "verdict"]
    assert verdicts == [(0, False), (1, True)]
