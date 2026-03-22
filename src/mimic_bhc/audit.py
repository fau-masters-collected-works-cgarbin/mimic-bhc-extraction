"""BHC extraction audit entry generation.

Produces audit entries from extraction results for manual review.
Each entry contains extraction metadata, review flags, and fields
for human review status. No MIMIC text is included.
"""

from __future__ import annotations

import random
from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mimic_bhc.stats import MIN_BHC_LENGTH

if TYPE_CHECKING:
    from mimic_bhc.model import BHCResult

# Fraction of clean (unflagged) entries to randomly select for spot-checking.
SPOT_CHECK_FRACTION = 0.01

# Header/boundary variants appearing fewer than this many times are flagged.
RARE_THRESHOLD = 10


def build_audit_entries(
    results: list[BHCResult],
    *,
    spot_check_seed: int = 42,
) -> list[dict[str, Any]]:
    """Build audit entries from extraction results.

    Each entry contains extraction metadata, review flags, and empty
    fields for human review. No MIMIC text is included.
    """
    now = datetime.now(tz=UTC).isoformat()

    # Count header and boundary frequencies for rarity detection.
    successful = [r for r in results if r.extraction_success]
    header_counts = Counter(r.start_header for r in successful if r.start_header)
    boundary_counts = Counter(r.end_header for r in successful if r.end_header)

    entries = []
    for r in results:
        bhc_length = r.bhc_char_count
        structure_cat = r.bhc_structure.category if r.bhc_structure else None

        entry: dict[str, Any] = {
            "hadm_id": r.hadm_id,
            "note_row_id": r.row_id,
            "note_length": r.note_length or 0,
            "success": r.extraction_success,
            "failure_reason": r.failure_reason,
            "start_header": r.start_header,
            "end_header": r.end_header,
            "bhc_start": r.bhc_start,
            "bhc_end": r.bhc_end,
            "bhc_length": bhc_length,
            "structure_category": structure_cat,
            "note_row_count": r.note_row_count,
            "quality_flags": list(r.quality_flags),
            "review_flags": [],
            "review_reasons": {},
            "extracted_at": now,
            "review_status": None,
            "review_notes": None,
            "reviewed_at": None,
        }

        _assign_flags(entry, r, bhc_length, header_counts, boundary_counts)
        entries.append(entry)

    _add_spot_check_flags(entries, spot_check_seed)

    return entries


def _assign_flags(
    entry: dict[str, Any],
    r: BHCResult,
    bhc_length: int,
    header_counts: dict[str, int],
    boundary_counts: dict[str, int],
) -> None:
    """Assign review flags and reasons to an audit entry."""
    flags = entry["review_flags"]
    reasons = entry["review_reasons"]

    if not r.extraction_success:
        flags.append(r.failure_reason)
        reasons[r.failure_reason] = f"Extraction failed: {r.failure_reason}"

    if r.extraction_success and bhc_length < MIN_BHC_LENGTH:
        flags.append("short")
        reasons["short"] = f"BHC is {bhc_length} chars, below {MIN_BHC_LENGTH}-char threshold"

    if r.extraction_success and r.end_header is None:
        flags.append("unbounded")
        reasons["unbounded"] = "No end boundary found, extracted to end of note"

    header = r.start_header
    if r.extraction_success and header and header_counts.get(header, 0) < RARE_THRESHOLD:
        count = header_counts.get(header, 0)
        flags.append("rare_header")
        reasons["rare_header"] = (
            f"Header variant {header!r} appears only {count} time(s) across all notes"
        )

    boundary = r.end_header
    if r.extraction_success and boundary and boundary_counts.get(boundary, 0) < RARE_THRESHOLD:
        count = boundary_counts.get(boundary, 0)
        flags.append("rare_boundary")
        reasons["rare_boundary"] = (
            f"Boundary variant {boundary!r} appears only {count} time(s) across all notes"
        )


def _add_spot_check_flags(entries: list[dict[str, Any]], seed: int) -> None:
    """Flag a random sample of clean entries for spot-checking."""
    clean_indices = [i for i, e in enumerate(entries) if not e["review_flags"]]
    n_spot = max(1, int(len(clean_indices) * SPOT_CHECK_FRACTION))
    rng = random.Random(seed)  # noqa: S311
    for i in rng.sample(clean_indices, min(n_spot, len(clean_indices))):
        entries[i]["review_flags"].append("sampled_for_spot_check")
        entries[i]["review_reasons"]["sampled_for_spot_check"] = (
            "Randomly selected clean extraction for spot-checking"
        )
