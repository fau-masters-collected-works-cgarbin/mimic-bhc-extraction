"""BHC organizational structure classification (3-category taxonomy).

Classifies BHC sections into three categories based on observable
structural features:

- Category A (Narrative): no labeled sections
- Category B (Domain-organized): 2+ explicitly labeled sections
- Category C (Standardized template): NICU template with fixed domain set

Also detects modifiers: numbering convention, acute/chronic partition,
and transitional issues section.

The taxonomy and detection rules are defined in the codebook (see
BHC organizational structure analysis documentation).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Category and modifier constants
# ---------------------------------------------------------------------------
CATEGORY_NARRATIVE = "narrative"
CATEGORY_DOMAIN_ORGANIZED = "domain_organized"
CATEGORY_STANDARDIZED_TEMPLATE = "standardized_template"

MODIFIER_ACUTE_CHRONIC = "acute_chronic"
MODIFIER_TRANSITIONAL_ISSUES = "transitional_issues"

# ---------------------------------------------------------------------------
# Category C: NICU template detection
# ---------------------------------------------------------------------------
# Standard neonatal assessment domains. A BHC matching 4+ of these as
# standalone section headers is classified as Category C.
_NICU_DOMAINS: list[str] = [
    r"respiratory",
    r"cardiovascular",
    r"fluids[,]?\s*electrolytes[,]?\s*(?:and\s+)?nutrition",
    r"gastrointestinal",
    r"hematolog(?:y|ic(?:al)?)",
    r"infectious\s+disease",
    r"neurolog(?:y|ic(?:al)?)",
    r"sensory",
    r"psychosocial",
]

# NICU domain headers appear as standalone lines with " - " or ":" separator.
# They may have optional numeric or # prefixes.
_NICU_DOMAIN_RES = [
    re.compile(
        r"^\s*(?:\d+\.?\s*)?(?:#+ *)?" + pat + r"\s*(?:[-:])",
        re.IGNORECASE | re.MULTILINE,
    )
    for pat in _NICU_DOMAINS
]

# Threshold for NICU template detection. All three observed NICU templates
# in Phase 1 used 7-9 of these domains. A threshold of 4 provides margin
# while staying well above the 1-2 domain matches that appear incidentally
# in non-NICU organ-system reviews.
_MIN_NICU_DOMAINS = 4

# NICU-specific domains that almost never appear in non-NICU organ-system
# reviews. At least one of these must match to distinguish a NICU template
# from a non-NICU problem list that happens to use organ-system labels.
_NICU_SPECIFIC_DOMAINS = [
    re.compile(
        r"^\s*(?:\d+\.?\s*)?(?:#+ *)?sensory\s*(?:[-:])",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(
        r"^\s*(?:\d+\.?\s*)?(?:#+ *)?psychosocial\s*(?:[-:])",
        re.IGNORECASE | re.MULTILINE,
    ),
]


def _count_nicu_domains(bhc_text: str) -> int:
    """Count how many distinct NICU template domains appear as section headers."""
    return sum(1 for pat in _NICU_DOMAIN_RES if pat.search(bhc_text))


def _has_nicu_specific_domain(bhc_text: str) -> bool:
    """Check if at least one NICU-specific domain (Sensory, Psychosocial) is present."""
    return any(pat.search(bhc_text) for pat in _NICU_SPECIFIC_DOMAINS)


# ---------------------------------------------------------------------------
# Category B: Domain-organized detection
# ---------------------------------------------------------------------------
# Detect labeled sections using structural markers. Each pattern matches
# a line that starts a new clinical-domain section.

# Hash-prefixed entries: # Problem, ## Problem, ##. Problem
_HASH_ENTRY_RE = re.compile(
    r"^[ \t]*#{1,2}\.?[ \t]*[A-Za-z]",
    re.MULTILINE,
)

# Double-hash specifically (for numbering convention detection)
_DOUBLE_HASH_RE = re.compile(
    r"^[ \t]*##\.?[ \t]*[A-Za-z]",
    re.MULTILINE,
)

# Numbered entries: 1. Problem, 1) Problem, 1/ Problem, (1) Problem
_NUMBERED_ENTRY_RE = re.compile(
    r"^[ \t]*(?:\(?\d+[.)\/]|\(\d+\))[ \t]*[A-Za-z]",
    re.MULTILINE,
)

# Numbered with period: 1. Problem
_NUM_PERIOD_RE = re.compile(r"^[ \t]*\d+\.[ \t]*[A-Za-z]", re.MULTILINE)
# Numbered with paren: (1) Problem
_NUM_PAREN_RE = re.compile(r"^[ \t]*\(\d+\)[ \t]*[A-Za-z]", re.MULTILINE)
# Numbered with slash: 1/ Problem
_NUM_SLASH_RE = re.compile(r"^[ \t]*\d+/[ \t]*[A-Za-z]", re.MULTILINE)
# Numbered with closing paren only: 1) Problem
_NUM_CLOSEPAREN_RE = re.compile(r"^[ \t]*\d+\)[ \t]*[A-Za-z]", re.MULTILINE)

# Standalone clinical-domain headers at the start of a line:
# "Neuro:", "Cardiovascular:", "GI:", etc.
# The header is a short capitalized phrase (under 40 chars) followed by
# a colon. It may have content after the colon on the same line
# (e.g., "Neuro: Head CT negative") or stand alone.
_STANDALONE_HEADER_RE = re.compile(
    r"^[ \t]*[A-Z][A-Za-z /\-]{1,40}:[ \t]",
    re.MULTILINE,
)

# Asterisk/bullet entries: * HEENT, * CV
_ASTERISK_ENTRY_RE = re.compile(
    r"^[ \t]*\*[ \t]*[A-Za-z]",
    re.MULTILINE,
)

# Minimum labeled sections to classify as domain-organized (Category B).
# A single labeled section could be a false positive (e.g., one header-like
# line in an otherwise narrative BHC). Two or more is a strong signal of
# intentional domain-based organization. Same threshold rationale as the
# existing binary classifier's _MIN_PROBLEM_SUBHEADERS in extract.py.
_MIN_DOMAIN_SECTIONS = 2

# Discharge-summary section headers that may appear inside extracted BHC text
# due to extraction boundary issues. These should not be counted as
# clinical-domain sections for Category B detection.
_DISCHARGE_HEADER_RE = re.compile(
    r"^[ \t]*(?:"
    r"condition[ \t]+on[ \t]+discharge"
    r"|discharge[ \t]+(?:condition|medications?|diagnosis"
    r"|diagnoses|instructions?|disposition|status|diet)"
    r"|medications?[ \t]+on[ \t]+(?:admission|discharge)"
    r"|followup[ \t]+instructions?"
    r"|follow[ \t-]up[ \t]+instructions?"
    r"|facility"
    r"|completed[ \t]+by"
    r"|attending"
    r")[ \t]*:?",
    re.IGNORECASE | re.MULTILINE,
)


def _is_discharge_header(bhc_text: str, match_start: int) -> bool:
    """Check if a standalone header match is actually a discharge-summary section header."""
    # Find the line containing this match
    line_end = bhc_text.find("\n", match_start)
    if line_end == -1:
        line_end = len(bhc_text)
    line = bhc_text[match_start:line_end]
    return _DISCHARGE_HEADER_RE.match(line) is not None


def _count_labeled_sections(bhc_text: str) -> int:
    """Count distinct labeled sections using any structural marker.

    Counts unique line starts to avoid double-counting a line that
    matches multiple patterns. Excludes discharge-summary section
    headers that may appear inside the BHC due to extraction artifacts.
    """
    matched_lines: set[int] = set()

    for pattern in [
        _HASH_ENTRY_RE,
        _NUMBERED_ENTRY_RE,
        _ASTERISK_ENTRY_RE,
    ]:
        for m in pattern.finditer(bhc_text):
            matched_lines.add(m.start())

    # Standalone headers: exclude discharge-summary section headers
    for m in _STANDALONE_HEADER_RE.finditer(bhc_text):
        if not _is_discharge_header(bhc_text, m.start()):
            matched_lines.add(m.start())

    return len(matched_lines)


def _detect_numbering_convention(bhc_text: str) -> str | None:
    """Identify the dominant numbering convention used in a Category B BHC."""
    double_hash_count = len(_DOUBLE_HASH_RE.findall(bhc_text))
    counts: dict[str, int] = {
        "##": double_hash_count,
        "#": len(_HASH_ENTRY_RE.findall(bhc_text)) - double_hash_count,
        "N.": len(_NUM_PERIOD_RE.findall(bhc_text)),
        "(N)": len(_NUM_PAREN_RE.findall(bhc_text)),
        "N/": len(_NUM_SLASH_RE.findall(bhc_text)),
        "N)": len(_NUM_CLOSEPAREN_RE.findall(bhc_text)),
        "*": len(_ASTERISK_ENTRY_RE.findall(bhc_text)),
    }

    # Check for standalone headers (no prefix marker)
    standalone_count = len(_STANDALONE_HEADER_RE.findall(bhc_text))
    # Only count standalone if no other markers are dominant
    prefix_total = sum(counts.values())
    if standalone_count >= _MIN_DOMAIN_SECTIONS and prefix_total < _MIN_DOMAIN_SECTIONS:
        return "standalone"

    # Return the convention with the most matches, if any reach threshold
    best = max(counts, key=lambda k: counts[k])
    if counts[best] >= _MIN_DOMAIN_SECTIONS:
        return best
    if standalone_count >= _MIN_DOMAIN_SECTIONS:
        return "standalone"
    return None


# ---------------------------------------------------------------------------
# Modifier detection
# ---------------------------------------------------------------------------

# Acute/chronic partition headers on their own lines. Matches keywords
# acute, active, chronic, stable, inactive followed by "issues". Allows
# optional prefixes (>>, *, #) and optional trailing = underlines.
_ACUTE_CHRONIC_RE = re.compile(
    r"^[ \t]*(?:[>*#]+[ \t]*)?"
    r"(?:acute|active|chronic|stable|inactive)"
    r"[/ \t]*(?:active|stable|chronic)?"
    r"[ \t]*issues?[ \t]*:?"
    r"[ \t]*=*[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)

# Transitional issues: must appear as a header on its own line, not mid-sentence.
# Matches: "Transitional Issues:", "TRANSITIONAL CARE ISSUES:", "Transitions:",
# "#Transitions:", etc.
_TRANSITIONAL_RE = re.compile(
    r"^[ \t]*#?[ \t]*transition(?:al|s)?(?:[ \t]+(?:care[ \t]+)?issues?)?[ \t]*:?"
    r"[ \t]*=*[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)


def _has_acute_chronic(bhc_text: str) -> bool:
    return _ACUTE_CHRONIC_RE.search(bhc_text) is not None


def _has_transitional_issues(bhc_text: str) -> bool:
    return _TRANSITIONAL_RE.search(bhc_text) is not None


# ---------------------------------------------------------------------------
# Main classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StructureResult:
    """Result of classifying a BHC's organizational structure."""

    category: str  # "narrative", "domain_organized", or "standardized_template"
    numbering_convention: str | None = None  # Only for domain_organized
    modifiers: tuple[str, ...] = ()
    nicu_domain_count: int = 0  # For standardized_template diagnostics
    labeled_section_count: int = 0  # For domain_organized diagnostics


def classify_bhc_structure(bhc_text: str) -> StructureResult:
    """Classify a BHC's organizational structure with modifiers.

    Detection order: standardized_template (most specific) ->
    domain_organized -> narrative (default).
    """
    modifiers: list[str] = []

    # Detect modifiers regardless of category
    if _has_acute_chronic(bhc_text):
        modifiers.append(MODIFIER_ACUTE_CHRONIC)
    if _has_transitional_issues(bhc_text):
        modifiers.append(MODIFIER_TRANSITIONAL_ISSUES)

    # Standardized template (NICU)
    # Requires 4+ NICU domains AND at least one NICU-specific domain
    # (Sensory or Psychosocial) to avoid false positives from non-NICU
    # organ-system problem lists.
    nicu_count = _count_nicu_domains(bhc_text)
    if nicu_count >= _MIN_NICU_DOMAINS and _has_nicu_specific_domain(bhc_text):
        return StructureResult(
            category=CATEGORY_STANDARDIZED_TEMPLATE,
            nicu_domain_count=nicu_count,
            modifiers=tuple(modifiers),
        )

    # Domain-organized
    section_count = _count_labeled_sections(bhc_text)
    if section_count >= _MIN_DOMAIN_SECTIONS:
        convention = _detect_numbering_convention(bhc_text)
        return StructureResult(
            category=CATEGORY_DOMAIN_ORGANIZED,
            numbering_convention=convention,
            labeled_section_count=section_count,
            modifiers=tuple(modifiers),
        )

    # Default: no labeled sections detected
    return StructureResult(
        category=CATEGORY_NARRATIVE,
        modifiers=tuple(modifiers),
    )
