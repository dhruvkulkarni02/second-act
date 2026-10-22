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

    def __init__(self, bad: set[int], raise_on: set[int] = frozenset(), where: str = "headline"):
        self.bad, self.raise_on, self.calls, self.where = bad, raise_on, 0, where
        self.feedback: list[list[str] | None] = []

    async def draft(self, state, feedback=None, previous=None) -> DraftResult:
        self.calls += 1
        self.feedback.append(feedback)
        if self.calls in self.raise_on:
            raise RuntimeError("model timed out")
        draft = template_briefing(state.signals, state.meta)
        if self.calls in self.bad:
            # The headline cannot be pruned, so a bad headline forces a retry; a bad point
            # can be removed instead.
            if self.where == "headline":
                draft.headline += ", hitting the woodwork 4 times"
            else:
                draft.teams[0].working[0].text += " They hit the woodwork 4 times."
        return DraftResult(
            draft,
            "model",
            latency_s=0.01,
            usage={"input_token_count": 100, "output_token_count": 50},
        )


class RejectPoint:
    """Model check that flags Harbour's first working point only."""

    async def check(self, draft, signals):
        return [Issue("HAR.working[0]", "claim not supported by its evidence: test")], {
            "latency_s": 0.0,
            "usage": {},
        }


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


def test_invented_number_in_one_point_is_removed_not_rejected(state: MatchState) -> None:
    drafter = ScriptedDrafter(bad={1}, where="point")
    log = AgentLog(write=False)
    briefing = run(state, offline=False, log=log, drafter=drafter, supporter=None)
    assert drafter.calls == 1 and briefing.source == "model"
    assert "woodwork" not in briefing.model_dump_json(exclude={"removed_claims"})
    [removed] = briefing.removed_claims
    assert removed.startswith("HAR.working: ") and "number 4 does not match" in removed
    assert log.summary()["claims_removed"] == 1


def test_unsupported_point_is_removed(state: MatchState) -> None:
    drafter = ScriptedDrafter(bad=set())
    template = template_briefing(state.signals, state.meta)
    log = AgentLog(write=False)
    briefing = run(state, offline=False, log=log, drafter=drafter, supporter=RejectPoint())
    assert drafter.calls == 1 and briefing.source == "model"
    assert len(briefing.teams[0].working) == len(template.teams[0].working) - 1
    assert briefing.teams[0].working[0].text == template.teams[0].working[1].text
    assert [r["event"] for r in log.records].count("pruned") == 1


def test_pruning_never_empties_a_section(state: MatchState) -> None:
    from second_act.agents.verifier import prune

    draft = template_briefing(state.signals, state.meta)
    only = [Issue(f"HAR.not_working[{i}]", "x") for i in range(len(draft.teams[0].not_working))]
    assert prune(draft, only) is None
    assert prune(draft, [Issue("headline", "x")]) is None


def test_cache_round_trip_and_key(state: MatchState, tmp_path, monkeypatch) -> None:
    from second_act.orchestration.cache import BriefingCache, briefing_key

    monkeypatch.setenv(
        "FOUNDRY_PROJECT_ENDPOINT", "https://acct.services.ai.azure.com/api/projects/p"
    )
    monkeypatch.setenv("FOUNDRY_MODEL", "test-model")
    cache = BriefingCache(tmp_path)
    key = briefing_key(state, "test-model", "minimal")
    assert key.startswith("m-001-")
    assert key != briefing_key(state, "other-model", "minimal")
    assert cache.get(key) is None

    first = run(state, offline=True, log=AgentLog(write=False))
    cache.put(key, first)
    log = AgentLog(write=False)
    fresh = _state()
    hit = run(fresh, offline=False, log=log, cache=cache)  # no drafter: must not call a model
    assert hit == first and fresh.briefing == first and fresh.predictions
    assert [r["event"] for r in log.records] == ["cache_hit"]

    (tmp_path / f"{key}.json").write_text("not json")
    assert cache.get(key) is None
