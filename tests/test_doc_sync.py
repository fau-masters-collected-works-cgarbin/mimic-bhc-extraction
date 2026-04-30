"""Cross-check concrete claims in docs/extraction-design.md against code.

These tests verify that documented pattern counts and threshold values stay
in sync with the code.  If a test fails, update the doc to match the code
(or vice versa).

For data-derived metrics (extraction success rates, header distributions,
length statistics in docs/empirical-findings.md), see the "Updating
documentation metrics" section in docs/extraction-design.md.
"""

import re
from pathlib import Path

from mimic_bhc.patterns import _BHC_HEADER_PATTERNS, _END_BOUNDARIES
from mimic_bhc.stats import MIN_BHC_LENGTH

_REPO_ROOT = Path(__file__).parent.parent
_DESIGN_DOC = _REPO_ROOT / "docs" / "extraction-design.md"


def _design_doc_text() -> str:
    return _DESIGN_DOC.read_text()


# ---------------------------------------------------------------------------
# extraction-design.md
# ---------------------------------------------------------------------------
class TestDesignDocMatchesCode:
    def test_header_pattern_count(self):
        """Doc says 'Four case-insensitive regex patterns match the BHC header'."""
        text = _design_doc_text()
        match = re.search(r"(\w+) case-insensitive regex patterns match the BHC header", text)
        assert match is not None, "Could not find header pattern count claim in doc"
        documented = _word_to_int(match.group(1))
        assert documented == len(_BHC_HEADER_PATTERNS), (
            f"Doc claims {documented} header patterns, code has {len(_BHC_HEADER_PATTERNS)}"
        )

    def test_end_boundary_count(self):
        """Doc says '15 patterns identify top-level discharge summary sections'."""
        text = _design_doc_text()
        match = re.search(r"(\d+) patterns identify top-level discharge summary sections", text)
        assert match is not None, "Could not find end-boundary count claim in doc"
        documented = int(match.group(1))
        assert documented == len(_END_BOUNDARIES), (
            f"Doc claims {documented} end boundaries, code has {len(_END_BOUNDARIES)}"
        )

    def test_min_bhc_length_threshold(self):
        """Doc says 'shorter than 100 characters'."""
        text = _design_doc_text()
        match = re.search(r"shorter than (\d+) characters", text)
        assert match is not None, "Could not find min BHC length claim in doc"
        documented = int(match.group(1))
        assert documented == MIN_BHC_LENGTH, (
            f"Doc claims {documented}, code has MIN_BHC_LENGTH={MIN_BHC_LENGTH}"
        )


# Map written-out numbers to ints for parsing prose claims.
_WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
}


def _word_to_int(word: str) -> int:
    """Convert a word like 'four' or '4' to an int."""
    lower = word.lower()
    if lower in _WORD_NUMBERS:
        return _WORD_NUMBERS[lower]
    return int(lower)
