"""DuckDB-based batch extraction and structure classification.

Loads a MIMIC notes file, runs extraction and structure classification over
it, and returns one enriched BHCResult per admission.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING

import duckdb

from mimic_bhc._util import is_mimic3 as _is_mimic3
from mimic_bhc._util import load_dataframe
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

    if not result.extraction_success:
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
    *,
    include_note_text: bool = False,
) -> BHCResult:
    """Extract BHC, classify structure, and enrich with note metadata."""
    result = extract_bhc(note_text)

    structure = None
    if result.extraction_success and result.bhc_text:
        structure = classify_bhc_structure(result.bhc_text)

    return dataclasses.replace(
        result,
        hadm_id=hadm_id,
        row_id=row_id,
        subject_id=subject_id,
        chartdate=chartdate,
        note_length=note_length,
        note_text=note_text if include_note_text else None,
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
    assert all(c == c.lower() for c in columns), (  # noqa: S101
        "columns must be lowercase; call load_dataframe() first"
    )
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


def _select_best_per_admission(
    group: pd.DataFrame,
    cols: _ColumnMap,
    *,
    include_note_text: bool = False,
) -> BHCResult:
    """Process all notes for one admission and return the best result.

    For admissions with multiple notes (addendums), keeps the note with the
    longest BHC text. If all notes fail extraction, returns the first result
    so that failed admissions still appear in output.
    """
    import pandas as pd

    best_result: BHCResult | None = None
    best_bhc_len = -1  # -1 so that even bhc_char_count=0 wins on the first note

    for row in group.itertuples(index=False):
        raw_chartdate = getattr(row, cols.chartdate) if cols.chartdate else None
        has_chartdate = raw_chartdate is not None and pd.notna(raw_chartdate)
        chartdate = str(raw_chartdate) if has_chartdate else None
        raw_text = getattr(row, cols.text)
        note_text = str(raw_text) if pd.notna(raw_text) else ""
        raw_iserror = getattr(row, cols.iserror) if cols.iserror else None
        iserror = int(raw_iserror) if raw_iserror is not None and pd.notna(raw_iserror) else None

        note_result = _process_note(
            hadm_id=int(getattr(row, cols.hadm_id)),
            subject_id=int(getattr(row, cols.subject_id)),
            row_id=str(getattr(row, cols.row_id)),
            chartdate=chartdate,
            note_text=note_text,
            note_length=len(note_text),
            iserror=iserror,
            include_note_text=include_note_text,
        )

        bhc_len = note_result.bhc_char_count
        if bhc_len > best_bhc_len:
            best_bhc_len = bhc_len
            best_result = note_result

    # groupby guarantees non-empty groups, so best_result is always set.
    assert best_result is not None, "empty group in _select_best_per_admission"  # noqa: S101
    return dataclasses.replace(
        best_result,
        note_row_count=len(group),
    )


def extract_from_file(
    input_path: str,
    format_hint: str = "auto",
    *,
    sample_size: int | None = None,
    include_note_text: bool = False,
) -> list[BHCResult]:
    """Extract BHC from discharge summaries in a CSV/parquet file.

    Loads the file, filters to discharge summaries (MIMIC-III), runs extraction
    and structure classification on each note, and handles addendum deduplication.
    Returns one BHCResult per admission.

    Args:
        input_path: Path to NOTEEVENTS CSV/parquet (MIMIC-III or MIMIC-IV format)
        format_hint: 'mimic3', 'mimic4', or 'auto' (default: auto-detect)
        sample_size: Extract from random sample of N admissions (optional)
        include_note_text: Include the full original note text in each result
            (default: False, to keep output size small)

    Returns:
        List of BHCResult, one per admission (including failed extractions).
    """
    input_path = str(input_path)

    is_mimic3 = _is_mimic3(input_path, format_hint)

    with duckdb.connect() as conn:
        notes = load_dataframe(conn, input_path)

    cols = _column_map(is_mimic3=is_mimic3, columns=list(notes.columns))

    # MIMIC-III: filter to discharge summaries, ~60k rows of the ~2M loaded.
    if is_mimic3:
        notes = notes[notes["category"] == "Discharge summary"].copy()

    if sample_size is not None:
        import pandas as pd

        unique_ids = pd.Series(notes[cols.hadm_id].unique())
        sampled_ids = unique_ids.sample(n=min(sample_size, len(unique_ids)), random_state=42)
        notes = notes[notes[cols.hadm_id].isin(sampled_ids)]

    return [
        _select_best_per_admission(group, cols, include_note_text=include_note_text)
        for _, group in notes.groupby(cols.hadm_id)
    ]
