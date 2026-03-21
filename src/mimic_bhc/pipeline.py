"""DuckDB-based batch pipeline for BHC extraction and structure classification.

This module handles input/output and orchestrates extraction across note collections.
DuckDB is used internally for efficient data loading but is not exposed in the public API.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import duckdb

from mimic_bhc._util import detect_mimic_format
from mimic_bhc.extract import extract_bhc
from mimic_bhc.stats import MIN_BHC_LENGTH
from mimic_bhc.structure import classify_bhc_structure

if TYPE_CHECKING:
    import pandas as pd

    from mimic_bhc.model import BHCResult


def _build_quality_flags(result: BHCResult, iserror: int | None = None) -> tuple[str, ...]:
    """Build quality_flags tuple based on extraction result metadata."""
    flags: list[str] = []

    if iserror == 1:
        flags.append("iserror")

    if not result.success:
        return tuple(flags)

    if result.bhc_text and len(result.bhc_text) < MIN_BHC_LENGTH:
        flags.append("below_min_length")

    if result.end_header is None:
        flags.append("unbounded")

    return tuple(flags)


def _process_note(
    hadm_id: int,
    subject_id: int,
    row_id: str,
    chartdate: str | None,
    note_text: str,
    note_length: int,
    iserror: int | None = None,
) -> BHCResult:
    """Extract BHC, classify structure, and enrich with note metadata."""
    result = extract_bhc(note_text, hadm_id=hadm_id, note_row_id=row_id)

    structure = None
    if result.success and result.bhc_text:
        structure = classify_bhc_structure(result.bhc_text)

    return dataclasses.replace(
        result,
        subject_id=subject_id,
        chartdate=chartdate,
        note_length=note_length,
        quality_flags=_build_quality_flags(result, iserror),
        bhc_structure=structure,
    )


@dataclass
class _ColumnMap:
    """Column name mapping for a MIMIC version."""

    hadm_id: str
    subject_id: str
    text: str
    row_id: str
    chartdate: str | None
    iserror: str | None


def _column_map(*, is_mimic3: bool, columns: list[str]) -> _ColumnMap:
    """Return the column mapping for the detected MIMIC version."""
    if is_mimic3:
        return _ColumnMap(
            hadm_id="hadm_id",
            subject_id="subject_id",
            text="text",
            row_id="row_id",
            chartdate="chartdate",
            iserror="iserror",
        )
    return _ColumnMap(
        hadm_id="hadm_id",
        subject_id="subject_id",
        text="text",
        row_id="note_id",
        chartdate="charttime" if "charttime" in columns else None,
        iserror=None,
    )


def _load_dataframe(conn: duckdb.DuckDBPyConnection, input_path: str) -> pd.DataFrame:
    """Load a CSV/parquet file into a DataFrame via DuckDB.

    Uses parameterized queries to avoid SQL injection from file paths.
    """
    if input_path.endswith(".parquet"):
        notes = conn.execute("SELECT * FROM parquet_scan(?)", [input_path]).fetchdf()
    else:
        notes = conn.execute("SELECT * FROM read_csv_auto(?)", [input_path]).fetchdf()

    # Normalize column names to lowercase for easier lookup
    notes.columns = notes.columns.str.lower()
    return notes


def _select_best_per_admission(
    group: pd.DataFrame,
    cols: _ColumnMap,
) -> BHCResult:
    """Process all notes for one admission and return the best result.

    For admissions with multiple notes (addendums), keeps the note with the
    longest BHC text. If all notes fail extraction, returns the first result
    so that failed admissions still appear in output.
    """
    import pandas as pd

    best_result: BHCResult | None = None
    best_bhc_len = -1  # -1 so that even bhc_char_count=0 wins on the first note

    for _, row in group.iterrows():
        chartdate = (
            str(row[cols.chartdate]) if cols.chartdate and pd.notna(row[cols.chartdate]) else None
        )
        note_text = str(row[cols.text]) if pd.notna(row[cols.text]) else ""
        iserror = int(row[cols.iserror]) if cols.iserror and pd.notna(row[cols.iserror]) else None

        note_result = _process_note(
            hadm_id=int(row[cols.hadm_id]),
            subject_id=int(row[cols.subject_id]),
            row_id=str(row[cols.row_id]),
            chartdate=chartdate,
            note_text=note_text,
            note_length=len(note_text),
            iserror=iserror,
        )

        bhc_len = note_result.bhc_char_count
        if bhc_len > best_bhc_len:
            best_bhc_len = bhc_len
            best_result = note_result

    # best_result is always set because group is never empty (groupby guarantees it)
    return best_result  # type: ignore[return-value]


def _write_results(results: list[BHCResult], output_path: str, *, pretty: bool) -> None:
    """Write extraction results to a JSON/JSONL file.

    Serialization happens here via BHCResult.to_dict(). This is the only
    place in the pipeline that converts BHCResult to dicts.
    """
    dicts = [r.to_dict() for r in results]
    with open(output_path, "w") as f:  # noqa: PTH123
        if pretty:
            json.dump(dicts, f, indent=2)
            f.write("\n")
        else:
            for d in dicts:
                json.dump(d, f)
                f.write("\n")


def extract_from_file(
    input_path: str,
    output_path: str,
    format_hint: str = "auto",
    *,
    pretty_print: bool = False,
    sample_size: int | None = None,
) -> None:
    """Extract BHC from discharge summaries in a CSV/parquet file.

    Args:
        input_path: Path to NOTEEVENTS CSV/parquet (MIMIC-III or MIMIC-IV format)
        output_path: Path to write JSONL output (or JSON array with pretty_print)
        format_hint: 'mimic3', 'mimic4', or 'auto' (default: auto-detect)
        pretty_print: Write a single pretty-printed JSON array instead of JSONL
        sample_size: Extract from random sample of N admissions (optional)
    """
    input_path = str(input_path)
    output_path = str(output_path)

    if format_hint == "auto":
        is_mimic3 = detect_mimic_format(input_path) == "mimic3"
    else:
        is_mimic3 = format_hint == "mimic3"

    with duckdb.connect() as conn:
        notes = _load_dataframe(conn, input_path)

    cols = _column_map(is_mimic3=is_mimic3, columns=list(notes.columns))

    # MIMIC-III: filter to discharge summaries
    if is_mimic3:
        notes = notes[notes["category"] == "Discharge summary"].copy()

    if sample_size is not None:
        notes = notes.sample(n=min(sample_size, len(notes)), random_state=42)

    results = [_select_best_per_admission(group, cols) for _, group in notes.groupby(cols.hadm_id)]

    _write_results(results, output_path, pretty=pretty_print)
