"""Per-session JSONL log of every agent step: inputs, outputs, retries, latency, tokens.

Logs go to ``$SECOND_ACT_LOG_DIR`` (default ``logs/``, gitignored). They never
contain credentials: only prompts' sizes, outputs and verifier findings.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class AgentLog:
    """Collects records in memory and appends each one to a session JSONL file."""

    def __init__(
        self, session_id: str | None = None, directory: Path | None = None, write: bool = True
    ) -> None:
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.records: list[dict[str, Any]] = []
        self.path: Path | None = None
        if write:
            folder = directory or Path(os.environ.get("SECOND_ACT_LOG_DIR", "logs"))
            folder.mkdir(parents=True, exist_ok=True)
            self.path = folder / f"session-{self.session_id}.jsonl"

    def record(self, agent: str, event: str, **fields: Any) -> dict[str, Any]:
        """Append one record."""
        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "session": self.session_id,
            "agent": agent,
            "event": event,
            **fields,
        }
        self.records.append(entry)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
        return entry

    def summary(self) -> dict[str, Any]:
        """Grounding, retry, latency and token totals for this session."""
        drafts = [r for r in self.records if r["event"] == "draft"]
        verdicts = [r for r in self.records if r["event"] == "verdict"]
        tokens_in = sum(r.get("tokens_in") or 0 for r in self.records)
        tokens_out = sum(r.get("tokens_out") or 0 for r in self.records)
        return {
            "drafts": len(drafts),
            "rejections": sum(not r["ok"] for r in verdicts),
            "issues_caught": sum(len(r.get("issues", [])) for r in verdicts),
            "fallbacks": sum(r["event"] == "fallback" for r in self.records),
            "claims_removed": sum(
                len(r.get("removed", [])) for r in self.records if r["event"] == "pruned"
            ),
            "model_calls": sum(1 for r in self.records if r.get("tokens_in") is not None),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "model_latency_s": round(
                sum(r.get("latency_s") or 0 for r in self.records if r["agent"] != "workflow"), 2
            ),
            "workflow_latency_s": round(
                sum(r.get("latency_s") or 0 for r in self.records if r["agent"] == "workflow"), 2
            ),
        }


class Timer:
    """Context manager measuring wall-clock seconds."""

    def __enter__(self) -> Timer:
        self.start = time.perf_counter()
        self.elapsed = 0.0
        return self

    def __exit__(self, *_: object) -> None:
        self.elapsed = round(time.perf_counter() - self.start, 2)


def usage_fields(usage: Mapping[str, Any] | None) -> dict[str, int | None]:
    """Token counts from an Agent Framework usage mapping."""
    usage = usage or {}
    return {
        "tokens_in": usage.get("input_token_count"),
        "tokens_out": usage.get("output_token_count"),
    }
