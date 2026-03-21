"""Section header discovery and classification tools.

Diagnostic utilities for exploring what section headers appear in discharge
notes and how they relate to the BHC extraction patterns.  These are not
part of the extraction pipeline itself -- they exist to support pattern
development and validation.

Two entry points:

- ``discover_headers_from_file``: loads a MIMIC CSV/parquet file, samples
  notes, and returns a frequency table.  This is the file-level function
  that mirrors ``extract_from_file`` in the pipeline module.
- ``discover_headers_in_texts``: scans an iterable of note strings.
  Use this when you already have note texts in memory.
"""

from __future__ import annotations

import gzip
from typing import TYPE_CHECKING

import duckdb

from mimic_bhc.extract import _BHC_HEADER_RE, _SECTION_HEADER_RE, END_BOUNDARY_RE

if TYPE_CHECKING:
    from collections.abc import Iterable


def classify_header_role(header: str) -> str:
    """Classify a discovered header as 'BHC header', 'End boundary', or 'Other'.

    Checks the header text against the extraction module's compiled patterns
    for BHC start headers and end boundaries.  Headers that match neither
    are labeled 'Other'.
    """
    line = header.strip()
    if _BHC_HEADER_RE.match(line) or _BHC_HEADER_RE.match(line + ":"):
        return "BHC header"
    if END_BOUNDARY_RE.match(line) or END_BOUNDARY_RE.match(line + ":"):
        return "End boundary"
    return "Other"


def discover_headers_in_texts(
    texts: Iterable[str],
) -> list[tuple[str, int, str]]:
    """Scan note texts for lines that look like section headers.

    Returns a list of ``(header, count, role)`` tuples sorted by frequency
    (descending).  Each ``role`` is one of "BHC header", "End boundary",
    or "Other", as determined by :func:`classify_header_role`.

    This function operates on text strings only and does not read files
    or connect to a database.  Use ``discover_headers_from_file`` to load
    notes from a MIMIC file directly.

    Parameters
    ----------
    texts:
        Iterable of discharge note strings to scan.

    Returns
    -------
    list[tuple[str, int, str]]
        ``(header_text, frequency, role)`` sorted by frequency descending.
    """
    header_counts: dict[str, int] = {}
    for text in texts:
        if not text:
            continue
        for match in _SECTION_HEADER_RE.finditer(text):
            header = match.group(1).strip()
            header_counts[header] = header_counts.get(header, 0) + 1

    if not header_counts:
        return []

    rows = sorted(header_counts.items(), key=lambda x: x[1], reverse=True)
    return [(header, count, classify_header_role(header)) for header, count in rows]


# ---------------------------------------------------------------------------
# File-level discovery (mirrors pipeline.extract_from_file)
# ---------------------------------------------------------------------------

def _detect_format(csv_path: str) -> str:
    """Auto-detect MIMIC version by checking for CATEGORY column in first row."""
    if csv_path.endswith(".gz"):
        with gzip.open(csv_path, "rt") as f:
            header = f.readline().strip()
    else:
        with open(csv_path) as f:  # noqa: PTH123
            header = f.readline().strip()

    return "mimic3" if "CATEGORY" in header else "mimic4"


def discover_headers_from_file(
    input_path: str,
    *,
    format_hint: str = "auto",
    sample_size: int = 500,
) -> list[tuple[str, int, str]]:
    """Load a MIMIC notes file, sample discharge notes, and discover headers.

    This is the file-level entry point for header discovery.  It handles
    format detection, column mapping, MIMIC-III discharge-summary filtering,
    and sampling -- the user only provides the file path.

    Parameters
    ----------
    input_path:
        Path to MIMIC notes file (``NOTEEVENTS.csv.gz`` for MIMIC-III,
        ``discharge.csv.gz`` for MIMIC-IV-Note).
    format_hint:
        ``"mimic3"``, ``"mimic4"``, or ``"auto"`` (default).
    sample_size:
        Number of notes to sample.  Pass 0 to scan all notes.

    Returns
    -------
    list[tuple[str, int, str]]
        ``(header_text, frequency, role)`` sorted by frequency descending.
    """
    is_mimic3 = (
        format_hint == "mimic3"
        if format_hint != "auto"
        else _detect_format(input_path) == "mimic3"
    )

    conn = duckdb.connect()

    # Load and normalize columns
    if input_path.endswith(".parquet"):
        df = conn.execute("SELECT * FROM parquet_scan(?)", [input_path]).fetchdf()
    else:
        df = conn.execute("SELECT * FROM read_csv_auto(?)", [input_path]).fetchdf()

    df.columns = df.columns.str.lower()
    conn.close()

    # MIMIC-III: filter to discharge summaries, exclude ISERROR
    if is_mimic3:
        df = df[df["category"] == "Discharge summary"]
        if "iserror" in df.columns:
            df = df[(df["iserror"].isna()) | (df["iserror"] != 1)]

    # Sample
    if sample_size > 0 and len(df) > sample_size:
        df = df.sample(n=sample_size, random_state=42)

    return discover_headers_in_texts(df["text"])
