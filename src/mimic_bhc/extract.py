"""BHC (Brief Hospital Course) extraction from MIMIC discharge summaries.

Design rationale documented in docs/bhc_extraction_findings.md (section
"Extraction Algorithm"). Update both when changing patterns, thresholds,
or algorithm logic. Concrete claims in the doc (pattern counts, threshold
values) are verified by tests/test_extract.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    import pandas as pd

# ---------------------------------------------------------------------------
# Header patterns (case-insensitive)
# ---------------------------------------------------------------------------
# Ordered from most specific to least specific so the match reports the best
# variant.  Optional colon, optional trailing whitespace.
#
# Uses [ \t] instead of \s to avoid matching across line breaks -- a line
# ending with "hospital" and the next starting with "course" should not be
# treated as a header.  The end-of-line anchor ($) is intentionally omitted
# so that headers with inline text ("HOSPITAL COURSE:  The patient was...")
# still match; the ^ anchor prevents mid-sentence false positives.
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
_BHC_HEADER_RE = re.compile(
    r"^[ \t]*(?:" + "|".join(_BHC_HEADER_PATTERNS) + r")[ \t]*:?[ \t]*",
    re.IGNORECASE | re.MULTILINE,
)

# Priority-based header matching
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# extract_bhc() tries each pattern in order and uses the first match from
# the highest-priority pattern that matches *anywhere* in the note.  This
# avoids false positives from less specific patterns.
#
# Example: a note contains "His\nhospital course has been complicated..."
# early on and "Brief Hospital Course:" later as a real section header.
# A single combined regex would return the first textual match ("hospital
# course"), which is a mid-sentence fragment.  Priority matching tries the
# "brief hospital course" pattern first, finds the real header, and never
# falls through to the bare "hospital course" pattern.
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
# Only top-level discharge-summary section headers.  BHC-internal sub-headers
# like "Cardiovascular:" or "Renal:" are intentionally excluded.
#
# "Facility" and "Attending" are legitimate discharge-summary sections but
# could in theory appear as standalone lines within a BHC.  The start-of-line
# anchor mitigates most false positives; empirical verification found no
# premature truncation from these patterns.
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

END_BOUNDARY_RE = re.compile(
    r"^[ \t]*(?:" + "|".join(pat for _, pat in _END_BOUNDARIES) + r")[ \t]*:?[ \t]*$",
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
# This is a more permissive pattern used for discovery and classification of
# section headers. The regex intentionally allows for a wide range of potential
# headers, including those that do not match the BHC header patterns or end
# boundary patterns, to support exploratory analysis of what headers appear in
# the notes. It looks for lines that start with optional whitespace, followed
# by a capitalized word (indicating the start of a header), and may optionally
# end with a colon. This is used in the discovery tools to find candidate headers
# and classify them based on whether they match the known BHC header or end
# boundary patterns.
# ---------------------------------------------------------------------------
_SECTION_HEADER_RE = re.compile(
    r"^[ \t]*([A-Z][A-Za-z /\-]+):?\s*$",
    re.MULTILINE,
)

# ---------------------------------------------------------------------------
# Problem-oriented sub-header detection
# ---------------------------------------------------------------------------
# Organ-system and problem sub-headers that indicate a problem-oriented BHC
# format.  See _MIN_PROBLEM_SUBHEADERS for the classification threshold.
_PROBLEM_SUBHEADERS: list[str] = [
    r"cardiovascular",
    r"cardiac",
    r"pulmonary",
    r"respiratory",
    r"renal",
    r"nephrology",
    r"infectious\s+disease",
    r"id",
    r"neurolog(?:y|ic(?:al)?)",
    r"neuro",
    r"hematolog(?:y|ic(?:al)?)",
    r"heme",
    r"endocrine",
    r"gastrointestinal",
    r"gi",
    r"fluids[,]?\s*electrolytes[,]?\s*(?:and\s+)?nutrition",
    r"fen",
    r"musculoskeletal",
    r"psychiatry",
    r"oncology",
    r"pain",
    r"prophylaxis",
    r"social",
    r"dispo(?:sition)?",
]

_PROBLEM_SUBHEADER_RE = re.compile(
    r"^\s*(?:#+ *)?(?:" + "|".join(_PROBLEM_SUBHEADERS) + r")\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Minimum number of distinct sub-header matches to classify as problem-oriented.
# A single match could be a false positive; two or more is a strong signal.
_MIN_PROBLEM_SUBHEADERS = 2


def find_subheader_matches(bhc_text: str) -> list[str]:
    """Return distinct organ-system sub-headers found in the BHC text."""
    matches = set()
    for match in _PROBLEM_SUBHEADER_RE.finditer(bhc_text):
        matches.add(match.group(0).strip().lower().rstrip(":"))
    return sorted(matches)


def _classify_bhc_format(bhc_text: str) -> str:
    """Classify a BHC as 'problem_oriented' or 'chronological'.

    This is a quick binary classification used during extraction. For the
    full 3-category taxonomy (narrative / domain_organized /
    standardized_template), see ``mimic_bhc.structure.classify_bhc_structure``.

    Looks for organ-system sub-headers on their own lines.  Returns
    'problem_oriented' if at least two distinct patterns match,
    'chronological' otherwise.
    """
    if len(find_subheader_matches(bhc_text)) >= _MIN_PROBLEM_SUBHEADERS:
        return "problem_oriented"
    return "chronological"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BHCResult:
    """Result of extracting a BHC section from a single note."""

    hadm_id: int
    success: bool
    bhc_text: str | None
    start_header: str | None
    end_header: str | None
    failure_reason: str | None  # "no_header" | "empty_section" | None
    bhc_format: str | None = None  # "chronological" | "problem_oriented" | None
    note_row_id: str | None = None
    # Character offsets of the BHC text within the original note.
    # Set only on successful extraction; None otherwise.
    bhc_start: int | None = None
    bhc_end: int | None = None


# ---------------------------------------------------------------------------
# Single-note extraction
# ---------------------------------------------------------------------------
def extract_bhc(note_text: str, hadm_id: int = 0, note_row_id: str | None = None) -> BHCResult:
    """Extract the Brief Hospital Course section from a discharge note.

    Finds the BHC header, then captures text until the next top-level section
    boundary or end of note.  Returns a BHCResult with extraction metadata.
    """
    # Try each header pattern in priority order (most specific first).
    # Use the first match from the highest-priority pattern that matches
    # anywhere in the note, avoiding false positives from less specific
    # patterns (e.g., bare "hospital course" when "Brief Hospital Course"
    # exists later in the note).
    header_match = None
    for pattern in _BHC_HEADER_PRIORITY:
        header_match = pattern.search(note_text)
        if header_match is not None:
            break
    if header_match is None:
        return BHCResult(
            hadm_id=hadm_id,
            success=False,
            bhc_text=None,
            start_header=None,
            end_header=None,
            failure_reason="no_header",
            note_row_id=note_row_id,
        )

    start_header = header_match.group(0).strip()
    body_start = header_match.end()
    remaining = note_text[body_start:]

    # Search for the first end-boundary in the remaining text.
    end_match = END_BOUNDARY_RE.search(remaining)
    if end_match is not None:
        raw_text = remaining[: end_match.start()]
        matched_line = end_match.group(0).strip()
        end_header = _identify_end_boundary(matched_line)
    else:
        raw_text = remaining
        end_header = None

    bhc_text = raw_text.strip()

    if not bhc_text:
        return BHCResult(
            hadm_id=hadm_id,
            success=False,
            bhc_text=None,
            start_header=start_header,
            end_header=end_header,
            failure_reason="empty_section",
            note_row_id=note_row_id,
        )

    # Compute offsets of the stripped BHC text within the original note.
    # raw_text starts at body_start; find where the stripped content begins.
    lstrip_offset = len(raw_text) - len(raw_text.lstrip())
    bhc_start_offset = body_start + lstrip_offset
    bhc_end_offset = bhc_start_offset + len(bhc_text)

    return BHCResult(
        hadm_id=hadm_id,
        success=True,
        bhc_text=bhc_text,
        start_header=start_header,
        end_header=end_header,
        failure_reason=None,
        bhc_format=_classify_bhc_format(bhc_text),
        note_row_id=note_row_id,
        bhc_start=bhc_start_offset,
        bhc_end=bhc_end_offset,
    )


def _identify_end_boundary(line: str) -> str:
    """Return the label of the end-boundary pattern that matches *line*."""
    for label, pattern in _END_BOUNDARY_INDIVIDUAL:
        if pattern.match(line):
            return label
    return line


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
# Minimum BHC length (characters) below which an extraction is flagged as
# suspiciously short.  Matches the threshold used in the Stanford
# MIMIC-IV-Ext-BHC dataset (Aali et al., JAMIA 2024).
MIN_BHC_LENGTH = 100


class BHCStats(TypedDict):
    """Return type for ``compute_bhc_stats``."""

    total: int
    successes: int
    failures: int
    success_rate: float
    failure_reasons: dict[str, int]
    header_distribution: dict[str, int]
    end_boundary_distribution: dict[str, int]
    unbounded_extractions: int
    bhc_length_stats: dict[str, float | int]
    short_extractions: int
    format_distribution: dict[str, int]


def compute_bhc_stats(results_df: pd.DataFrame) -> BHCStats:
    """Compute summary statistics from BHC extraction results.

    Expects a DataFrame with columns: hadm_id, success, bhc_text, start_header,
    end_header, failure_reason, bhc_format.
    """
    total = len(results_df)
    if total == 0:
        return {
            "total": 0,
            "successes": 0,
            "failures": 0,
            "success_rate": 0.0,
            "failure_reasons": {},
            "header_distribution": {},
            "end_boundary_distribution": {},
            "unbounded_extractions": 0,
            "bhc_length_stats": {},
            "short_extractions": 0,
            "format_distribution": {},
        }

    successes = results_df["success"].sum()
    failures = total - successes

    # Failure breakdown
    failure_df = results_df[~results_df["success"]]
    failure_reasons = (
        failure_df["failure_reason"].value_counts().to_dict() if len(failure_df) > 0 else {}
    )

    # Header distribution (successful extractions)
    success_df = results_df[results_df["success"]]
    header_dist = success_df["start_header"].value_counts().to_dict() if len(success_df) > 0 else {}

    # End boundary distribution
    end_dist = success_df["end_header"].value_counts().to_dict() if len(success_df) > 0 else {}
    unbounded = int(success_df["end_header"].isna().sum()) if len(success_df) > 0 else 0

    # BHC format distribution
    format_dist = success_df["bhc_format"].value_counts().to_dict() if len(success_df) > 0 else {}

    # BHC length stats (character count)
    length_stats = {}
    short_extractions = 0
    if len(success_df) > 0:
        bhc_lengths = success_df["bhc_text"].str.len()
        short_extractions = int((bhc_lengths < MIN_BHC_LENGTH).sum())
        length_stats = {
            "mean": float(bhc_lengths.mean()),
            "median": float(bhc_lengths.median()),
            "q25": float(bhc_lengths.quantile(0.25)),
            "q75": float(bhc_lengths.quantile(0.75)),
            "min": int(bhc_lengths.min()),
            "max": int(bhc_lengths.max()),
        }

    return {
        "total": total,
        "successes": int(successes),
        "failures": int(failures),
        "success_rate": float(successes / total),
        "failure_reasons": failure_reasons,
        "header_distribution": header_dist,
        "end_boundary_distribution": end_dist,
        "unbounded_extractions": unbounded,
        "bhc_length_stats": length_stats,
        "short_extractions": short_extractions,
        "format_distribution": format_dist,
    }
