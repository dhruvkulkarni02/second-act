"""API: health, matches, an offline session streamed to the end over SSE."""

from __future__ import annotations

import json
import re

from fastapi.testclient import TestClient

from second_act.api.app import create_app


def test_offline_session_streams_to_the_end() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        listing = client.get("/api/matches").json()
        assert listing[1]["home_name"] == "Kestrel" and listing[1]["home_colour"].startswith("#")
        assert [m["match_id"] for m in listing] == [
            "m-001",
            "m-002",
            "m-003",
        ]
        session = client.post(
            "/api/sessions", json={"match_id": "m-001", "speed": 600, "offline": True}
        ).json()
        assert session["offline"] is True
        sid = session["session_id"]
        kinds: list[str] = []
        with client.stream("GET", f"/api/sessions/{sid}/stream") as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            for line in response.iter_lines():
                if line.startswith("event: "):
                    kinds.append(line.removeprefix("event: "))
                if line.startswith("data: ") and kinds and kinds[-1] == "end":
                    json.loads(line.removeprefix("data: "))
        assert kinds[0] == "match" and kinds[-1] == "end" and kinds.count("cue") >= 100
        briefing = client.get(f"/api/sessions/{sid}/briefing").json()
        assert briefing["status"] == "ready" and len(briefing["variants"]) == 12
        assert client.get(f"/api/sessions/{sid}/recap").json()["status"] == "ready"
        preds = client.get(f"/api/sessions/{sid}/predictions").json()["predictions"]
        assert {p["status"] for p in preds} >= {"confirmed", "contradicted"}


def test_unknown_match_and_session() -> None:
    with TestClient(create_app()) as client:
        assert client.post("/api/sessions", json={"match_id": "m-999"}).status_code == 404
        assert client.get("/api/sessions/nope/briefing").status_code == 404


def test_index_fingerprints_assets_and_is_not_cached() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"
        assert re.search(r'/static/styles\.css\?v=[0-9a-f]{10}"', response.text)
        assert re.search(r'/static/app\.js\?v=[0-9a-f]{10}"', response.text)
