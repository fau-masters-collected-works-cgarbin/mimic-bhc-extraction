"""Regex patterns for BHC header detection and end-boundary matching.

Design rationale documented in docs/extraction-design.md. Update both when
changing patterns, thresholds, or algorithm logic. Concrete claims in the
doc (pattern counts, threshold values) are verified by tests/test_doc_sync.py.

If you change these patterns, the empirical results in docs/empirical-findings.md
may be stale. See docs/extraction-design.md#updating-documentation-metrics.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Header patterns (case-insensitive)
# ---------------------------------------------------------------------------
# Uses [ \t] instead of \s so a line ending with "hospital" and the next
# starting with "course" is not treated as one header.
# Common prefix: optional "concise/brief summary of" or "history of"
_SUMMARY_PREFIX = r"(?:(?:(?:concise|brief)[ \t]+)?summary[ \t]+of[ \t]+)?"
_HISTORY_PREFIX = r"(?:history[ \t]+of[ \t]+)?"
_HOSP = r"hospital(?:ization)?"

# Ordered most-specific-first for priority-based matching (see below).
_BHC_HEADER_PATTERNS: list[str] = [
    _SUMMARY_PREFIX + r"brief[ \t]+" + _HOSP + r"[ \t]+course",
    _SUMMARY_PREFIX + _HISTORY_PREFIX + _HOSP + r"[ \t]+course",
    _SUMMARY_PREFIX + r"nicu[ \t]+course",
    r"hosp\.?[ \t]+course",
]

# Combined regex used only for classification (is this line a BHC header?).
# The ^ anchor is what keeps these patterns from matching mid-sentence.
_BHC_HEADER_RE = re.compile(
    r"^[ \t]*(?:" + "|".join(_BHC_HEADER_PATTERNS) + r")[ \t]*:?[ \t]*",
    re.IGNORECASE | re.MULTILINE,
)

# Priority-based header matching
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# One compiled pattern per variant, to be tried in order. The first match
# from the most specific pattern that matches anywhere in the note wins.
#
# Example: a note contains "His\nhospital course has been complicated..."
# early on and "Brief Hospital Course:" later as a real section header.
# A single combined regex would return the first textual match ("hospital
# course"), which is a mid-sentence fragment. Trying the "brief hospital
# course" pattern first finds the real header instead.
_BHC_HEADER_PRIORITY = [
    re.compile(
        r"^[ \t]*(?:" + pat + r")[ \t]*:?[ \t]*",
        re.IGNORECASE | re.MULTILINE,
    )
    for pat in _BHC_HEADER_PATTERNS
]

# ---------------------------------------------------------------------------
# End-boundary patterns (ranked allowlist)
# ---------------------------------------------------------------------------
# Only top-level discharge-summary section headers. BHC-internal sub-headers
# like "Cardiovascular:" or "Renal:" stay out: as boundaries they would cut
# the BHC off at its own first subsection.
#
# "Facility" and "Attending" are legitimate discharge-summary sections but
# could in theory appear as standalone lines within a BHC. The start-of-line
# anchor mitigates most false positives.
#
# Uses [ \t] instead of \s for intra-pattern whitespace (same rationale as
# the header patterns: avoid matching across line breaks).
_END_BOUNDARIES: list[tuple[str, str]] = [
    ("Medications on Admission", r"medications?[ \t]+on[ \t]+admission"),
    ("Medications on Discharge", r"medications?[ \t]+on[ \t]+discharge"),
    ("Discharge Medications", r"discharge[ \t]+medications?"),
    ("Discharge Condition", r"discharge[ \t]+condition"),
    ("Condition on Discharge", r"condition[ \t]+on[ \t]+discharge"),
    ("Discharge Diagnosis", r"discharge[ \t]+diagnos[ei]s"),
    ("Discharge Instructions", r"discharge[ \t]+instructions?"),
    ("Discharge Disposition", r"discharge[ \t]+disposition"),
    ("Discharge Status", r"discharge[ \t]+status"),
    ("Discharge Diet", r"discharge[ \t]+diet"),
    ("Followup Instructions", r"followup[ \t]+instructions?"),
    ("Follow-up Instructions", r"follow[ \t-]up[ \t]+instructions?"),
    ("Facility", r"facility"),
    ("Completed by", r"completed[ \t]+by"),
    ("Attending", r"attending"),
]

_END_BOUNDARY_ALTERNATION = "|".join(pat for _, pat in _END_BOUNDARIES)

END_BOUNDARY_RE = re.compile(
    r"^[ \t]*(?:" + _END_BOUNDARY_ALTERNATION + r")[ \t]*:?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)

# The same sections without the end anchor, to match a header that leaked into
# the extracted text and carries its section content on the same line.
DISCHARGE_HEADER_RE = re.compile(
    r"^[ \t]*(?:" + _END_BOUNDARY_ALTERNATION + r")[ \t]*:?",
    re.IGNORECASE | re.MULTILINE,
)

# Individual patterns compiled for reporting which boundary matched.
_END_BOUNDARY_INDIVIDUAL = [
    (label, re.compile(r"^[ \t]*(?:" + pat + r")[ \t]*:?[ \t]*$", re.IGNORECASE | re.MULTILINE))
    for label, pat in _END_BOUNDARIES
]

# ---------------------------------------------------------------------------
# Section header discovery pattern (any line that looks like a header)
#
# Far more permissive than the BHC header and end-boundary patterns above:
# it matches any capitalized line, so exploratory scans find header
# variants those two do not cover yet.
# ---------------------------------------------------------------------------
_SECTION_HEADER_RE = re.compile(
    r"^[ \t]*([A-Z][A-Za-z /\-]+):?\s*$",
    re.MULTILINE,
)


def identify_end_boundary(line: str) -> str:
    """Return the label of the end-boundary pattern that matches *line*."""
    for label, pattern in _END_BOUNDARY_INDIVIDUAL:
        if pattern.match(line):
            return label
    return line
