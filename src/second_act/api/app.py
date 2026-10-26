"""FastAPI app: replays stored synthetic matches as a live server-sent-event stream.

    GET  /api/health                      health check
    GET  /api/matches                     available synthetic matches
    POST /api/sessions                    start a replay: {match_id, speed, offline}
    GET  /api/sessions/{id}/stream        SSE: match, event, score, status, cue,
                                          predictions, briefing, recap, end
    GET  /api/sessions/{id}/briefing      half-time briefing, all variants
    GET  /api/sessions/{id}/recap         full-time recap, all variants
    GET  /api/sessions/{id}/predictions   prediction statuses

Each session runs one ``MatchRunner`` in the background and keeps its message
history, so a client that connects (or reconnects) late replays from the start.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from second_act.orchestration.agent_log import AgentLog
from second_act.orchestration.cache import BriefingCache
from second_act.orchestration.foundry import FoundryNotConfigured, FoundrySettings, offline_mode
from second_act.orchestration.match_runner import MatchOutputs, MatchRunner
from second_act.schema.io import read_events, read_metadata, read_snapshots

DATA_DIR = Path(os.environ.get("SECOND_ACT_DATA_DIR", "data"))
WEB_DIR = Path(os.environ.get("SECOND_ACT_WEB_DIR", "web"))
MAX_SESSIONS = int(os.environ.get("SECOND_ACT_MAX_SESSIONS", 20))
SESSION_TTL_S = float(os.environ.get("SECOND_ACT_SESSION_TTL_S", 3600))
HALFTIME_HOLD_S = float(os.environ.get("SECOND_ACT_HALFTIME_HOLD_S", 15))


class SessionRequest(BaseModel):
    """Start a replay."""

    match_id: str = "m-001"
    speed: float = Field(default=30.0, gt=0, le=600, description="Match seconds per real second.")
    offline: bool = False


class SpeedRequest(BaseModel):
    """Change replay speed mid-session."""

    speed: float = Field(gt=0, le=600)


@dataclass
class Session:
    """One replay: its runner, outputs and message history."""

    session_id: str
    match_id: str
    speed: float
    offline: bool
    created: float = field(default_factory=time.time)
    history: list[tuple[str, dict]] = field(default_factory=list)
    done: bool = False
    error: str | None = None
    outputs: MatchOutputs | None = None
    runner: MatchRunner | None = None
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    resume: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None

    async def hold(self, phase: str) -> None:
        """Pause at half-time so viewers can read the briefing (until resumed or timeout)."""
        if HALFTIME_HOLD_S <= 0:
            return
        await self.emit("status", {"phase": phase, "hold_s": HALFTIME_HOLD_S})
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.resume.wait(), timeout=HALFTIME_HOLD_S)

    async def emit(self, kind: str, data: dict) -> None:
        async with self.changed:
            self.history.append((kind, data))
            self.changed.notify_all()


def _pacer(session: Session):
    """Sleep between events by match time / the session's (changeable) speed."""
    last: dict[str, tuple[int, float]] = {}

    async def pace(period: int, t: float) -> None:
        prev = last.get("t")
        last["t"] = (period, t)
        if prev is None or prev[0] != period:
            return
        delay = (t - prev[1]) / session.speed
        if delay > 0:
            await asyncio.sleep(min(delay, 5.0))

    return pace


def _fingerprinted_index(web_dir: Path) -> str:
    """index.html with ``?v=<hash>`` appended to each /static asset it references."""
    html = (web_dir / "index.html").read_text(encoding="utf-8")
    for asset in sorted(web_dir.iterdir()):
        if asset.suffix in (".css", ".js"):
            digest = hashlib.sha256(asset.read_bytes()).hexdigest()[:10]
            html = html.replace(f'"/static/{asset.name}"', f'"/static/{asset.name}?v={digest}"')
    return html


def _load_match(match_id: str):
    folder = DATA_DIR / match_id
    if not (folder / "metadata.json").exists():
        raise HTTPException(404, f"unknown match {match_id}")
    snaps = folder / "snapshots.jsonl.gz"
    return (
        read_events(folder / "events.jsonl"),
        read_metadata(folder / "metadata.json"),
        read_snapshots(snaps) if snaps.exists() else None,
    )


def _online_available() -> bool:
    try:
        FoundrySettings.from_env()
    except FoundryNotConfigured:
        return False
    return True


def create_app() -> FastAPI:
    """Build the app (one in-memory session store per process)."""
    app = FastAPI(title="Second Act", version="0.1.0")
    sessions: dict[str, Session] = {}
    cache = BriefingCache()
    match_cache: dict[str, tuple] = {}

    def prune_sessions() -> None:
        now = time.time()
        for sid in [
            s for s, sess in sessions.items() if sess.done and now - sess.created > SESSION_TTL_S
        ]:
            sessions.pop(sid, None)

    async def run_session(session: Session) -> None:
        if session.match_id not in match_cache:
            match_cache[session.match_id] = _load_match(session.match_id)
        events, meta, snapshots = match_cache[session.match_id]
        log = AgentLog(session_id=session.session_id)
        try:
            runner = MatchRunner(
                events,
                meta,
                snapshots,
                offline=session.offline,
                log=log,
                emit=session.emit,
                pace=_pacer(session),
                hold=session.hold,
                cache=cache,
            )
            session.runner = runner
            session.outputs = runner.outputs
            await runner.run()
        except Exception as exc:  # report, never crash the server
            session.error = f"{type(exc).__name__}: {exc}"
            await session.emit("error", {"message": session.error})
        finally:
            session.done = True
            async with session.changed:
                session.changed.notify_all()

    def get_session(session_id: str) -> Session:
        session = sessions.get(session_id)
        if session is None:
            raise HTTPException(404, "unknown session")
        return session

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok", "online_available": _online_available(), "sessions": len(sessions)}

    @app.get("/api/matches")
    async def matches() -> list[dict]:
        index = DATA_DIR / "index.json"
        return json.loads(index.read_text()) if index.exists() else []

    @app.post("/api/sessions")
    async def start(request: SessionRequest) -> dict:
        prune_sessions()
        if sum(not s.done for s in sessions.values()) >= MAX_SESSIONS:
            raise HTTPException(429, "too many live sessions; try again shortly")
        _ = _load_match(request.match_id) if request.match_id not in match_cache else None
        offline = request.offline or offline_mode() or not _online_available()
        session = Session(uuid.uuid4().hex[:12], request.match_id, request.speed, offline)
        sessions[session.session_id] = session
        session.task = asyncio.create_task(run_session(session))
        return {
            "session_id": session.session_id,
            "match_id": session.match_id,
            "speed": session.speed,
            "offline": offline,
        }

    @app.get("/api/sessions/{session_id}/stream")
    async def stream(session_id: str, request: Request) -> StreamingResponse:
        session = get_session(session_id)

        async def events() -> AsyncIterator[str]:
            index = 0
            while True:
                async with session.changed:
                    while index >= len(session.history) and not session.done:
                        try:
                            await asyncio.wait_for(session.changed.wait(), timeout=15)
                        except TimeoutError:
                            break
                    batch = session.history[index:]
                    finished = session.done and index + len(batch) >= len(session.history)
                index += len(batch)
                if not batch and not finished:
                    yield ": keep-alive\n\n"
                for kind, data in batch:
                    yield f"event: {kind}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
                if finished:
                    return
                if await request.is_disconnected():
                    return

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/sessions/{session_id}/briefing")
    async def briefing(session_id: str) -> dict:
        out = get_session(session_id).outputs or MatchOutputs()
        return {
            "status": out.briefing_status,
            "variants": [v.model_dump(mode="json") for v in out.briefing],
        }

    @app.get("/api/sessions/{session_id}/recap")
    async def recap(session_id: str) -> dict:
        out = get_session(session_id).outputs or MatchOutputs()
        return {
            "status": out.recap_status,
            "variants": [v.model_dump(mode="json") for v in out.recap],
        }

    @app.post("/api/sessions/{session_id}/speed")
    async def set_speed(session_id: str, request: SpeedRequest) -> dict:
        session = get_session(session_id)
        session.speed = request.speed
        return {"session_id": session_id, "speed": session.speed}

    @app.post("/api/sessions/{session_id}/resume")
    async def resume(session_id: str) -> dict:
        """End the half-time hold early."""
        get_session(session_id).resume.set()
        return {"session_id": session_id, "resumed": True}

    @app.get("/api/sessions/{session_id}/signals")
    async def signal_values(session_id: str, ids: str = "") -> dict:
        """Current values of the given signal IDs (comma-separated), for evidence chips."""
        session = get_session(session_id)
        signals = session.runner.state.signals if session.runner else None
        wanted = [i for i in ids.split(",") if i][:200]
        out = {}
        for sid in wanted:
            sig = signals.get(sid) if signals is not None else None
            if sig is not None:
                out[sid] = sig.model_dump(mode="json", exclude_none=True)
        return {"signals": out}

    @app.get("/api/sessions/{session_id}/predictions")
    async def predictions(session_id: str) -> dict:
        session = get_session(session_id)
        state = session.runner.state if session.runner else None
        return {
            "predictions": [p.model_dump(mode="json") for p in (state.predictions if state else [])]
        }

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        page = _fingerprinted_index(WEB_DIR)

        @app.get("/")
        async def index() -> HTMLResponse:
            # The page is never cached; its asset links carry content hashes, so a new
            # release always loads fresh CSS and JavaScript.
            return HTMLResponse(page, headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
