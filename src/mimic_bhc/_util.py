"""Shared internal utilities."""

from __future__ import annotations

import gzip
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb
    import pandas as pd


def load_dataframe(conn: duckdb.DuckDBPyConnection, input_path: str) -> pd.DataFrame:
    """Load a CSV/parquet file into a DataFrame via DuckDB.

    Uses parameterized queries to avoid SQL injection from file paths.
    Column names are normalized to lowercase.
    """
    if input_path.endswith(".parquet"):
        notes = conn.execute("SELECT * FROM parquet_scan(?)", [input_path]).fetchdf()
    else:
        notes = conn.execute("SELECT * FROM read_csv_auto(?)", [input_path]).fetchdf()

    notes.columns = notes.columns.str.lower()
    return notes


def is_mimic3(input_path: str, format_hint: str = "auto") -> bool:
    """Resolve a format hint to a concrete MIMIC-III boolean.

    When *format_hint* is ``"auto"``, auto-detects from the file header.
    """
    if format_hint == "auto":
        return detect_mimic_format(input_path) == "mimic3"
    return format_hint == "mimic3"


def detect_mimic_format(csv_path: str) -> str:
    """Auto-detect MIMIC version by checking for CATEGORY column in first row.

    Only works with CSV files (plain or gzipped). Parquet files do not have a
    text header row, so auto-detection is not supported; pass --format explicitly.
    """
    if csv_path.endswith(".parquet"):
        msg = (
            f"Cannot auto-detect MIMIC format from a Parquet file: {csv_path}. "
            "Use --format mimic3 or --format mimic4 to specify the format explicitly."
        )
        raise ValueError(msg)

    if csv_path.endswith(".gz"):
        with gzip.open(csv_path, "rt") as f:
            header = f.readline().strip()
    else:
        with open(csv_path, encoding="utf-8") as f:  # noqa: PTH123
            header = f.readline().strip()

    return "mimic3" if "CATEGORY" in header else "mimic4"
