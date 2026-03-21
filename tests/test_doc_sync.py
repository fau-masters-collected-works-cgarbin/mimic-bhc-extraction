"""Cross-check concrete claims in docs/extraction-design.md against code.

These tests verify that documented pattern counts, threshold values, and
algorithm descriptions stay in sync with the code.  If a test fails, update
the doc to match the code (or vice versa).
"""

import re
from pathlib import Path

from mimic_bhc.patterns import _BHC_HEADER_PATTERNS, _END_BOUNDARIES
from mimic_bhc.stats import MIN_BHC_LENGTH

_DOC_PATH = Path(__file__).parent.parent / "docs" / "extraction-design.md"


def _doc_text() -> str:
    return _DOC_PATH.read_text()


class TestDocMatchesCode:
    def test_header_pattern_count(self):
        """Doc says 'Four case-insensitive regex patterns match the BHC header'."""
        text = _doc_text()
        match = re.search(r"(\w+) case-insensitive regex patterns match the BHC header", text)
        assert match is not None, "Could not find header pattern count claim in doc"
        documented = _word_to_int(match.group(1))
        assert documented == len(_BHC_HEADER_PATTERNS), (
            f"Doc claims {documented} header patterns, code has {len(_BHC_HEADER_PATTERNS)}"
        )

    def test_end_boundary_count(self):
        """Doc says '15 patterns identify top-level discharge summary sections'."""
        text = _doc_text()
        match = re.search(r"(\d+) patterns identify top-level discharge summary sections", text)
        assert match is not None, "Could not find end-boundary count claim in doc"
        documented = int(match.group(1))
        assert documented == len(_END_BOUNDARIES), (
            f"Doc claims {documented} end boundaries, code has {len(_END_BOUNDARIES)}"
        )

    def test_min_bhc_length_threshold(self):
        """Doc says 'shorter than 100 characters'."""
        text = _doc_text()
        match = re.search(r"shorter than (\d+) characters", text)
        assert match is not None, "Could not find min BHC length claim in doc"
        documented = int(match.group(1))
        assert documented == MIN_BHC_LENGTH, (
            f"Doc claims {documented}, code has MIN_BHC_LENGTH={MIN_BHC_LENGTH}"
        )

    def test_failure_reasons_documented(self):
        """Doc should mention both failure reasons: no_header and empty_section."""
        text = _doc_text()
        assert "no_header" in text, "Doc missing failure reason 'no_header'"
        assert "empty_section" in text, "Doc missing failure reason 'empty_section'"


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
