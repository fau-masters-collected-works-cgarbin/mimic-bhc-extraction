"""Section header discovery and classification tools.

Diagnostic utilities for exploring what section headers appear in discharge
notes and how they relate to the BHC extraction patterns. They support
pattern development and validation.

- ``discover_headers_from_file``: loads a MIMIC CSV/parquet file, samples
  notes, and returns a frequency table.
- ``discover_headers_in_texts``: scans an iterable of note strings. Use this
  when you already have note texts in memory.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

import duckdb

from mimic_bhc._util import is_mimic3 as _is_mimic3
from mimic_bhc._util import load_dataframe
from mimic_bhc.patterns import _BHC_HEADER_RE, _SECTION_HEADER_RE, END_BOUNDARY_RE

if TYPE_CHECKING:
    from collections.abc import Iterable


def classify_header_role(header: str) -> str:
    """Classify a discovered header as 'BHC header', 'End boundary', or 'Other'.

    A trailing colon is optional: the header is tried both as given and
    with a colon appended.
    """
    line = header.strip()
    if _BHC_HEADER_RE.match(line) or _BHC_HEADER_RE.match(line + ":"):
        return "BHC header"
    if END_BOUNDARY_RE.match(line) or END_BOUNDARY_RE.match(line + ":"):
        return "End boundary"
    return "Other"


def discover_headers_in_texts(
    texts: Iterable[str | None],
) -> list[tuple[str, int, str]]:
    """Scan note texts for lines that look like section headers.

    Args:
        texts: Discharge note strings to scan. Empty and None entries are
            skipped.

    Returns:
        ``(header_text, frequency, role)`` sorted by frequency descending,
        where role comes from :func:`classify_header_role`.
    """
    header_counts: Counter[str] = Counter()
    for text in texts:
        if not text:
            continue
        for match in _SECTION_HEADER_RE.finditer(text):
            header_counts[match.group(1).strip()] += 1

    if not header_counts:
        return []

    rows = header_counts.most_common()
    return [(header, count, classify_header_role(header)) for header, count in rows]


# ---------------------------------------------------------------------------
# File-level discovery
# ---------------------------------------------------------------------------


def discover_headers_from_file(
    input_path: str,
    *,
    format_hint: str = "auto",
    sample_size: int = 500,
) -> list[tuple[str, int, str]]:
    """Load a MIMIC notes file, sample discharge notes, and discover headers.

    Args:
        input_path: Path to MIMIC notes file (``NOTEEVENTS.csv.gz`` for
            MIMIC-III, ``discharge.csv.gz`` for MIMIC-IV-Note).
        format_hint: ``"mimic3"``, ``"mimic4"``, or ``"auto"`` (default).
        sample_size: Number of notes to sample. Pass 0 to scan all notes.

    Returns:
        ``(header_text, frequency, role)`` sorted by frequency descending.
    """
    is_mimic3 = _is_mimic3(input_path, format_hint)

    with duckdb.connect() as conn:
        notes = load_dataframe(conn, input_path)

    # MIMIC-III: filter to discharge summaries and drop ISERROR rows, whose
    # headers would skew the frequency counts.
    if is_mimic3:
        notes = notes[notes["category"] == "Discharge summary"]
        if "iserror" in notes.columns:
            notes = notes[(notes["iserror"].isna()) | (notes["iserror"] != 1)]

    if sample_size > 0 and len(notes) > sample_size:
        notes = notes.sample(n=sample_size, random_state=42)

    return discover_headers_in_texts(notes["text"])
