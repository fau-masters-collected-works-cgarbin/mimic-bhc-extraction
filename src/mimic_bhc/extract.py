"""Single-note BHC extraction.

The extract_bhc() function is the sole extraction entry point. It uses
patterns from mimic_bhc.patterns and returns a BHCResult from mimic_bhc.model.
Design rationale documented in docs/extraction-design.md.
"""

from __future__ import annotations

from mimic_bhc.model import BHCResult
from mimic_bhc.patterns import (
    _BHC_HEADER_PRIORITY,
    END_BOUNDARY_RE,
    identify_end_boundary,
)

__all__ = ["BHCResult", "extract_bhc"]


def extract_bhc(note_text: str) -> BHCResult:
    """Extract the Brief Hospital Course section from a discharge note.

    Finds the BHC header, then captures text until the next top-level section
    boundary or end of note.  Returns a BHCResult with extraction metadata.

    This is a pure text-in/result-out function. It does not know about
    admission IDs, note metadata, or structure classification. Callers
    enrich the result with that context via dataclasses.replace().
    """
    # Try each header pattern in priority order (most specific first).
    header_match = None
    for pattern in _BHC_HEADER_PRIORITY:
        header_match = pattern.search(note_text)
        if header_match is not None:
            break
    if header_match is None:
        return BHCResult(
            success=False,
            bhc_text=None,
            start_header=None,
            end_header=None,
            failure_reason="no_header",
        )

    start_header = header_match.group(0).strip()
    body_start = header_match.end()
    remaining = note_text[body_start:]

    # Search for the first end-boundary in the remaining text.
    end_match = END_BOUNDARY_RE.search(remaining)
    if end_match is not None:
        raw_text = remaining[: end_match.start()]
        matched_line = end_match.group(0).strip()
        end_header = identify_end_boundary(matched_line)
    else:
        raw_text = remaining
        end_header = None

    bhc_text = raw_text.strip()

    if not bhc_text:
        return BHCResult(
            success=False,
            bhc_text=None,
            start_header=start_header,
            end_header=end_header,
            failure_reason="empty_section",
        )

    # Compute offsets of the stripped BHC text within the original note.
    lstrip_offset = len(raw_text) - len(raw_text.lstrip())
    bhc_start_offset = body_start + lstrip_offset
    bhc_end_offset = bhc_start_offset + len(bhc_text)

    return BHCResult(
        success=True,
        bhc_text=bhc_text,
        start_header=start_header,
        end_header=end_header,
        failure_reason=None,
        bhc_start=bhc_start_offset,
        bhc_end=bhc_end_offset,
    )
