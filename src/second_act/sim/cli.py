"""Command line for the generator.

second-act-generate generate [--match m-001] [--out data] [--no-snapshots]
second-act-generate find-seed m-001 [--start 1] [--tries 2000]
second-act-generate story [--match m-001] [--data data]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from second_act.schema.io import (
    read_events,
    read_metadata,
    write_events,
    write_metadata,
    write_snapshots,
)
from second_act.sim.config import load_match_config, load_teams, match_config_paths
from second_act.sim.engine import simulate
from second_act.sim.positions import generate_snapshots
from second_act.sim.story import check_demo_story, format_summary

DEMO_MATCH = "m-001"


def _config_path(match_id: str) -> Path:
    for path in match_config_paths():
        if path.stem == match_id:
            return path
    raise SystemExit(f"no config for {match_id}")


def generate(match_ids: list[str], out: Path, snapshots: bool = True) -> None:
    """Generate the given matches into ``out/<match_id>/`` and refresh ``out/index.json``."""
    teams = load_teams()
    out.mkdir(parents=True, exist_ok=True)
    for match_id in match_ids:
        config = load_match_config(_config_path(match_id))
        result = simulate(config, teams)
        folder = out / match_id
        folder.mkdir(exist_ok=True)
        write_events(folder / "events.jsonl", result.events)
        write_metadata(folder / "metadata.json", result.metadata)
        if snapshots and config.snapshots:
            write_snapshots(folder / "snapshots.jsonl.gz", generate_snapshots(result))
        print(f"{match_id}: {len(result.events)} events -> {folder}")
        print(format_summary(result.events, result.metadata))
    index = []
    for folder in sorted(p for p in out.iterdir() if (p / "metadata.json").exists()):
        meta = read_metadata(folder / "metadata.json")
        index.append(
            {
                "match_id": meta.match_id,
                "title": meta.title,
                "home": meta.home.code,
                "away": meta.away.code,
                "venue": meta.venue,
                "kickoff": meta.kickoff,
            }
        )
    (out / "index.json").write_text(json.dumps(index, indent=2) + "\n")


def find_seed(match_id: str, start: int, tries: int) -> int | None:
    """Return the first seed from ``start`` whose events pass the demo story checks."""
    teams = load_teams()
    config = load_match_config(_config_path(match_id))
    for seed in range(start, start + tries):
        result = simulate(config.model_copy(update={"seed": seed}), teams)
        report = check_demo_story(result.events, result.metadata)
        if report.passed:
            print(f"seed {seed} passes:\n{report}")
            return seed
    print(f"no seed in [{start}, {start + tries}) passes")
    return None


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``second-act-generate``."""
    parser = argparse.ArgumentParser(
        prog="second-act-generate",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help="generate match files")
    gen.add_argument("--match", action="append", help="match id (repeatable; default: all)")
    gen.add_argument("--out", type=Path, default=Path("data"))
    gen.add_argument("--no-snapshots", action="store_true")
    fs = sub.add_parser("find-seed", help="search for a seed that tells the demo story")
    fs.add_argument("match_id", nargs="?", default=DEMO_MATCH)
    fs.add_argument("--start", type=int, default=1)
    fs.add_argument("--tries", type=int, default=2000)
    st = sub.add_parser("story", help="check the demo story in generated files")
    st.add_argument("--match", default=DEMO_MATCH)
    st.add_argument("--data", type=Path, default=Path("data"))
    args = parser.parse_args(argv)

    if args.command == "generate":
        ids = args.match or [p.stem for p in match_config_paths()]
        generate(ids, args.out, snapshots=not args.no_snapshots)
        return 0
    if args.command == "find-seed":
        return 0 if find_seed(args.match_id, args.start, args.tries) is not None else 1
    folder = args.data / args.match
    report = check_demo_story(
        read_events(folder / "events.jsonl"), read_metadata(folder / "metadata.json")
    )
    print(report)
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
