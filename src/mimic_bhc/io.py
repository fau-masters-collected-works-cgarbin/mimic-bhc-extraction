"""I/O helpers for reading and writing BHC extraction results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mimic_bhc.model import BHCResult

if TYPE_CHECKING:
    from collections.abc import Sequence


def load_results(path: str | Path) -> list[BHCResult]:
    """Load BHCResult objects from a JSON or JSONL file.

    Supports both pretty-printed JSON arrays (first char is ``[``)
    and line-delimited JSONL (one JSON object per line).
    """
    with open(path) as f:  # noqa: PTH123
        first_char = f.read(1)
        if not first_char:
            return []
        f.seek(0)
        if first_char == "[":
            records = json.load(f)
        else:
            records = [json.loads(line) for line in f if line.strip()]
    return [BHCResult.from_dict(rec) for rec in records]


def write_results(
    results: Sequence[BHCResult],
    path: str | Path,
    *,
    pretty: bool = False,
) -> None:
    """Write BHCResult objects to a JSON or JSONL file.

    Default format is JSONL (one JSON object per line). Set
    ``pretty=True`` for a single pretty-printed JSON array.
    Creates parent directories if needed.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    dicts = [r.to_dict() for r in results]
    with out.open("w") as f:
        if pretty:
            json.dump(dicts, f, indent=2, default=str)
            f.write("\n")
        else:
            for d in dicts:
                json.dump(d, f, default=str)
                f.write("\n")


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Load dicts from a JSONL file (one JSON object per line).

    Skips blank lines. Returns an empty list for an empty file.
    """
    with open(path) as f:  # noqa: PTH123
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(records: Sequence[dict[str, Any]], path: str | Path) -> None:
    """Write dicts as JSONL (one JSON object per line).

    Useful for audit entries and other non-BHCResult data.
    Creates parent directories if needed.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for record in records:
            json.dump(record, f, default=str)
            f.write("\n")
