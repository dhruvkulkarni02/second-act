"""Run each match once online and store the verified outputs in precomputed/.

The deployed app reads this directory as its cache (SECOND_ACT_CACHE_DIR), so repeat
viewers get the same verified briefing, recap and variants without new model calls.

    PYTHONPATH=src python scripts/precompute.py [m-001 m-002 m-003]
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from second_act.orchestration.agent_log import AgentLog
from second_act.orchestration.cache import BriefingCache
from second_act.orchestration.match_runner import MatchRunner
from second_act.schema.io import read_events, read_metadata, read_snapshots

ROOT = Path(__file__).resolve().parents[1]


async def precompute(match_id: str, cache: BriefingCache) -> dict:
    folder = ROOT / "data" / match_id

    async def emit(kind: str, data: dict) -> None:
        return None

    log = AgentLog(session_id=f"precompute-{match_id}")
    start = time.perf_counter()
    runner = MatchRunner(
        read_events(folder / "events.jsonl"),
        read_metadata(folder / "metadata.json"),
        read_snapshots(folder / "snapshots.jsonl.gz"),
        offline=False,
        log=log,
        emit=emit,
        cache=cache,
    )
    await runner.run()
    state = runner.state
    return {
        "match_id": match_id,
        "seconds": round(time.perf_counter() - start, 1),
        "briefing": state.briefing.source if state.briefing else None,
        "recap": state.recap.source if state.recap else None,
        "predictions": {p.prediction_id: p.status.value for p in state.predictions},
        **log.summary(),
    }


async def run_all(match_ids: list[str]) -> None:
    cache = BriefingCache(ROOT / "precomputed")
    for match_id in match_ids:
        print(json.dumps(await precompute(match_id, cache)), flush=True)


def main(argv: list[str]) -> int:
    asyncio.run(run_all(argv or ["m-001", "m-002", "m-003"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
