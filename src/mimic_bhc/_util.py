"""Shared internal utilities."""

from __future__ import annotations

import gzip


def detect_mimic_format(csv_path: str) -> str:
    """Auto-detect MIMIC version by checking for CATEGORY column in first row."""
    if csv_path.endswith(".gz"):
        with gzip.open(csv_path, "rt") as f:
            header = f.readline().strip()
    else:
        with open(csv_path) as f:  # noqa: PTH123
            header = f.readline().strip()

    return "mimic3" if "CATEGORY" in header else "mimic4"
