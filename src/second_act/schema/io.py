"""Read and write match files: events JSONL, snapshots gzipped JSONL, metadata JSON.

Every reader validates each record against its schema.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterable, Iterator
from pathlib import Path

from second_act.schema.events import Event
from second_act.schema.metadata import MatchMetadata
from second_act.schema.snapshots import Snapshot


def write_events(path: Path, events: Iterable[Event]) -> None:
    """Write events as JSONL, omitting null fields."""
    with path.open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(event.model_dump_json(exclude_none=True) + "\n")


def read_events(path: Path) -> list[Event]:
    """Read and validate an events JSONL file."""
    with path.open(encoding="utf-8") as fh:
        return [Event.model_validate_json(line) for line in fh if line.strip()]


def write_snapshots(path: Path, snapshots: Iterable[Snapshot]) -> None:
    """Write snapshots as gzipped JSONL (deterministic: no gzip timestamp)."""
    with path.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as fh:
        for snap in snapshots:
            fh.write((snap.model_dump_json() + "\n").encode("utf-8"))


def iter_snapshots(path: Path) -> Iterator[Snapshot]:
    """Stream and validate snapshots from a gzipped JSONL file."""
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield Snapshot.model_validate_json(line)


def read_snapshots(path: Path) -> list[Snapshot]:
    """Read and validate all snapshots from a gzipped JSONL file."""
    return list(iter_snapshots(path))


def write_metadata(path: Path, metadata: MatchMetadata) -> None:
    """Write match metadata as pretty JSON."""
    path.write_text(json.dumps(metadata.model_dump(mode="json"), indent=2) + "\n")


def read_metadata(path: Path) -> MatchMetadata:
    """Read and validate a metadata JSON file."""
    return MatchMetadata.model_validate_json(path.read_text(encoding="utf-8"))
