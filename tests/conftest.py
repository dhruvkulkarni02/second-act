"""Shared fixtures: repository paths and the committed sample matches."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
MATCH_IDS = ["m-001", "m-002", "m-003"]


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never call a model from tests."""
    monkeypatch.setenv("SECOND_ACT_OFFLINE", "true")
