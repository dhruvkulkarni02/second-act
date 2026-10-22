"""Foundry settings: offline flag and configuration errors (no network calls)."""

from __future__ import annotations

import pytest

from second_act.orchestration.foundry import FoundryNotConfigured, FoundrySettings, offline_mode


def test_offline_flag_is_on_in_tests() -> None:
    assert offline_mode()


def test_missing_settings_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "")
    monkeypatch.setenv("FOUNDRY_MODEL", "")
    with pytest.raises(FoundryNotConfigured):
        FoundrySettings.from_env()


def test_placeholder_settings_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://<your-account>.services.ai.azure.com")
    monkeypatch.setenv("FOUNDRY_MODEL", "gpt-x")
    with pytest.raises(FoundryNotConfigured):
        FoundrySettings.from_env()


def test_settings_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "FOUNDRY_PROJECT_ENDPOINT", "https://acct.services.ai.azure.com/api/projects/p"
    )
    monkeypatch.setenv("FOUNDRY_MODEL", "my-deployment")
    settings = FoundrySettings.from_env()
    assert settings.model == "my-deployment"
