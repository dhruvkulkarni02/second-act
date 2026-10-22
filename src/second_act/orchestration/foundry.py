"""Microsoft Foundry model access through Microsoft Agent Framework.

Configuration comes from the environment (see ``.env.example``):
``FOUNDRY_PROJECT_ENDPOINT`` and ``FOUNDRY_MODEL``. Authentication uses
Microsoft Entra ID through ``DefaultAzureCredential``: ``az login`` locally,
managed identity on Azure Container Apps. No keys are read or stored.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv


class FoundryNotConfigured(RuntimeError):
    """Raised when the Foundry endpoint or model deployment is missing."""


@dataclass(frozen=True)
class FoundrySettings:
    """Where the model lives."""

    project_endpoint: str
    model: str
    reasoning_effort: str | None = None

    @classmethod
    def from_env(cls) -> FoundrySettings:
        """Read settings from the environment, loading ``.env`` if present."""
        load_dotenv()
        endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "").strip()
        model = os.environ.get("FOUNDRY_MODEL", "").strip()
        effort = os.environ.get("FOUNDRY_REASONING_EFFORT", "low").strip() or None
        if not endpoint or not model or "<" in endpoint or "<" in model:
            raise FoundryNotConfigured(
                "Set FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL (see .env.example)."
            )
        return cls(project_endpoint=endpoint, model=model, reasoning_effort=effort)

    def default_options(self) -> dict[str, object]:
        """Agent ``default_options`` for this deployment (reasoning effort, if set)."""
        return {"reasoning": {"effort": self.reasoning_effort}} if self.reasoning_effort else {}


def offline_mode() -> bool:
    """True when agents must not call the model (tests, CI, fallback)."""
    load_dotenv()
    return os.environ.get("SECOND_ACT_OFFLINE", "false").strip().lower() in {"1", "true", "yes"}


def build_chat_client(settings: FoundrySettings | None = None) -> FoundryChatClient:
    """Create a Foundry chat client for the configured project and deployment."""
    settings = settings or FoundrySettings.from_env()
    return FoundryChatClient(
        project_endpoint=settings.project_endpoint,
        model=settings.model,
        credential=DefaultAzureCredential(exclude_interactive_browser_credential=True),
    )


@dataclass(frozen=True)
class SmokeResult:
    """Outcome of a single round-trip to the model."""

    text: str
    latency_s: float
    input_tokens: int | None
    output_tokens: int | None


async def smoke_call(prompt: str = "Reply with exactly: Second Act is live.") -> SmokeResult:
    """Make one model call through an Agent Framework agent and report latency and tokens."""
    settings = FoundrySettings.from_env()
    agent = Agent(
        client=build_chat_client(settings),
        name="SmokeTest",
        instructions="You are a terse connectivity check. Follow the user's instruction exactly.",
        default_options=settings.default_options(),
    )
    start = time.perf_counter()
    response = await agent.run(prompt)
    latency = time.perf_counter() - start
    usage = response.usage_details or {}
    return SmokeResult(
        text=response.text,
        latency_s=round(latency, 2),
        input_tokens=usage.get("input_token_count"),
        output_tokens=usage.get("output_token_count"),
    )
