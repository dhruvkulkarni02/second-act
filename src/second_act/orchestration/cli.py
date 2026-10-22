"""Run the half-time workflow on a generated match and print the verified briefing.

    second-act-halftime [m-001] [--offline] [--data data] [--json briefing.json]

Online mode needs FOUNDRY_PROJECT_ENDPOINT, FOUNDRY_MODEL and ``az login``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from second_act.orchestration.agent_log import AgentLog
from second_act.orchestration.cache import BriefingCache
from second_act.orchestration.foundry import FoundryNotConfigured, offline_mode
from second_act.orchestration.halftime import run_halftime
from second_act.schema.briefing import Briefing
from second_act.schema.io import read_events, read_metadata, read_snapshots
from second_act.state import MatchState


def format_briefing(briefing: Briefing, state: MatchState) -> str:
    """Human-readable briefing with evidence IDs."""
    lines = [f"HEADLINE  {briefing.headline}", f"          {', '.join(briefing.headline_evidence)}"]
    for read in briefing.teams:
        lines.append(f"\n{state.meta.team(read.team).name} ({read.team})")
        for label, points in (("working", read.working), ("not working", read.not_working)):
            lines.append(f"  {label}:")
            for p in points:
                lines.append(f"    - {p.text}\n      [{', '.join(p.evidence)}]")
        lines.append("  predictions:")
        for p in read.predictions:
            scope = f" {p.scope['channel']}" if p.scope else ""
            lines.append(
                f"    {p.prediction_id}: {p.claim}\n      test: {p.subject_team} {p.metric}{scope} "
                f"{p.direction} >= {p.threshold_pct:g}% vs first half, "
                f"after {p.evaluate_after_minute}'"
            )
    lines.append(
        f"\nsource={briefing.source} attempts={briefing.attempts}"
        + (f" fallback={briefing.fallback_reason}" if briefing.fallback_reason else "")
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``second-act-halftime``."""
    parser = argparse.ArgumentParser(
        prog="second-act-halftime",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("match_id", nargs="?", default="m-001")
    parser.add_argument("--offline", action="store_true", help="template tactician, no model calls")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--json", type=Path, help="also write the briefing to this file")
    parser.add_argument("--no-cache", action="store_true", help="always call the model")
    args = parser.parse_args(argv)
    folder = args.data / args.match_id
    snaps = folder / "snapshots.jsonl.gz"
    state = MatchState.at_first_half_end(
        read_events(folder / "events.jsonl"),
        read_metadata(folder / "metadata.json"),
        read_snapshots(snaps) if snaps.exists() else None,
    )
    log = AgentLog()
    offline = args.offline or offline_mode()
    try:
        cache = None if args.no_cache else BriefingCache()
        briefing = asyncio.run(run_halftime(state, offline=offline, log=log, cache=cache))
    except FoundryNotConfigured as exc:
        print(
            f"online mode is not configured: {exc}\nUse --offline to run without a model.",
            file=sys.stderr,
        )
        return 2
    print(format_briefing(briefing, state))
    print(f"\nlog: {log.path}\nsummary: {json.dumps(log.summary())}")
    if args.json:
        args.json.write_text(json.dumps(briefing.model_dump(mode="json"), indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
