"""Cache of verified briefings, so replays of the same match do not call the model again.

The key covers everything that determines the briefing: the events seen so far, the
model deployment and reasoning effort, and ``PROMPT_VERSION`` (bump it whenever the
tactician or verifier prompts or rules change). Offline briefings are deterministic
and cheap, so only online briefings are cached.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from pydantic import BaseModel

from second_act.schema.briefing import Briefing
from second_act.state.match_state import MatchState

PROMPT_VERSION = "v5"


def briefing_key(state: MatchState, model: str, effort: str | None, kind: str = "halftime") -> str:
    """Stable key for the briefing (or recap) of this match state and model configuration."""
    digest = hashlib.sha256()
    for part in (PROMPT_VERSION, kind, model, effort or "", state.meta.match_id):
        digest.update(part.encode() + b"\0")
    for event in state.events:
        digest.update(event.model_dump_json(exclude_none=True).encode() + b"\n")
    prefix = state.meta.match_id if kind == "halftime" else f"{state.meta.match_id}-{kind}"
    return f"{prefix}-{digest.hexdigest()[:16]}"


class BriefingCache:
    """Verified briefings stored as JSON files under one directory."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or Path(
            os.environ.get("SECOND_ACT_CACHE_DIR", ".cache/briefings")
        )

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str, model: type[BaseModel] = Briefing):
        """The cached output for ``key``, if any (an unreadable file counts as a miss)."""
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return model.model_validate(json.loads(path.read_text())["briefing"])
        except (ValueError, KeyError):
            return None

    def put(self, key: str, briefing: BaseModel, summary: dict | None = None) -> Path:
        """Store a verified briefing (and how it was produced) under ``key``."""
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(key)
        payload = {"key": key, "briefing": briefing.model_dump(mode="json"), "log": summary}
        path.write_text(json.dumps(payload, indent=1) + "\n")
        return path

    def get_json(self, key: str):
        """A cached JSON payload (used for presenter variants), or None."""
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())["payload"]
        except (ValueError, KeyError):
            return None

    def put_json(self, key: str, payload) -> Path:
        """Store a JSON payload under ``key``."""
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(key)
        path.write_text(json.dumps({"key": key, "payload": payload}) + "\n")
        return path
