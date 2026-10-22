"""Print a signal summary for a generated match.

second-act-signals [m-001] [--data data] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from second_act.analytics.signals import SignalSet, compute_signals
from second_act.schema.io import read_events, read_metadata, read_snapshots

HALF_METRICS = [
    ("possession", None, "%"),
    ("field_tilt", None, "%"),
    ("ppda", None, ""),
    ("turnover_height", None, " m"),
    ("high_regains", None, ""),
    ("final_third_entries", None, ""),
    ("channel_share", "left", "%"),
    ("channel_share", "centre", "%"),
    ("channel_share", "right", "%"),
    ("progressive_passes", None, ""),
    ("pass_completion", None, "%"),
    ("pass_difficulty", None, ""),
    ("shots", None, ""),
    ("xg", None, ""),
    ("goals", None, ""),
    ("sequence_length", None, ""),
    ("turnovers_per_min", None, ""),
    ("control_index", None, ""),
    ("def_line_height", None, " m"),
    ("team_width", None, " m"),
    ("sprints", None, ""),
    ("fast_shots", None, ""),
]


def _fmt(value: float | None, suffix: str) -> str:
    if value is None:
        return "-"
    return f"{value:g}{suffix}"


def summary(signals: SignalSet, home: str, away: str) -> str:
    """A per-half table plus the windowed channel entries for both teams."""
    lines = [
        f"{'signal':28} {home + ' H1':>9} {away + ' H1':>9} {home + ' H2':>9} {away + ' H2':>9}"
    ]
    for metric, qualifier, suffix in HALF_METRICS:
        name = metric + (f"[{qualifier}]" if qualifier else "")
        cells = [
            _fmt(signals.value(metric, team, qualifier, period), suffix)
            for period in (1, 2)
            for team in (home, away)
        ]
        lines.append(f"{name:28} " + " ".join(f"{c:>9}" for c in cells))
    lines.append("")
    lines.append("final-third entries per 5-minute window (w0..w8)")
    for team in (home, away):
        for channel in ("left", "centre", "right"):
            for period in (1, 2):
                values = signals.window_values("channel_entries", team, channel, period)
                lines.append(
                    f"  {team} {channel:6} p{period}: "
                    + " ".join(f"{v:2.0f}" for v in values)
                    + f"   (mean {sum(values) / max(len(values), 1):.2f})"
                )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``second-act-signals``."""
    parser = argparse.ArgumentParser(prog="second-act-signals", description=__doc__)
    parser.add_argument("match_id", nargs="?", default="m-001")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--json", type=Path, help="also write all signals to this file")
    args = parser.parse_args(argv)
    folder = args.data / args.match_id
    meta = read_metadata(folder / "metadata.json")
    snaps_path = folder / "snapshots.jsonl.gz"
    snapshots = read_snapshots(snaps_path) if snaps_path.exists() else None
    signals = compute_signals(read_events(folder / "events.jsonl"), meta, snapshots)
    print(f"{meta.title} ({meta.match_id}): {len(signals)} signals\n")
    print(summary(signals, meta.home.code, meta.away.code))
    if args.json:
        args.json.write_text(
            json.dumps([s.model_dump(exclude_none=True) for s in signals.to_list()], indent=1)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
