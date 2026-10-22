"""Shared fixtures: repository paths and the committed sample matches."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

# No half-time hold in tests (read when the API module is imported).
os.environ.setdefault("SECOND_ACT_HALFTIME_HOLD_S", "0")

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
MATCH_IDS = ["m-001", "m-002", "m-003"]


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never call a model from tests."""
    monkeypatch.setenv("SECOND_ACT_OFFLINE", "true")


@pytest.fixture(scope="session")
def finished():
    """Run the demo match offline once and return the runner (state at full time)."""
    from second_act.orchestration.agent_log import AgentLog
    from second_act.orchestration.match_runner import MatchRunner
    from second_act.schema.io import read_events, read_metadata, read_snapshots

    folder = DATA / "m-001"
    messages: list = []

    async def emit(kind, data):
        messages.append((kind, data))

    runner = MatchRunner(
        read_events(folder / "events.jsonl"),
        read_metadata(folder / "metadata.json"),
        read_snapshots(folder / "snapshots.jsonl.gz"),
        offline=True,
        log=AgentLog(write=False),
        emit=emit,
    )
    asyncio.run(runner.run())
    return runner, messages
