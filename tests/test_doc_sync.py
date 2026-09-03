"""Cross-check concrete claims in the docs against code.

Covers docs/extraction-design.md and docs/bhc-format-coding-rules.md. On a
failure, update the doc to match the code or the code to match the doc.

For data-derived metrics (extraction success rates, header distributions,
length statistics in docs/empirical-findings.md), see the "Updating
documentation metrics" section in docs/extraction-design.md.
"""

import re
from pathlib import Path

from mimic_bhc.patterns import _BHC_HEADER_PATTERNS, _END_BOUNDARIES
from mimic_bhc.stats import MIN_BHC_LENGTH
from mimic_bhc.structure import (
    _MIN_DOMAIN_SECTIONS,
    CATEGORY_DOMAIN_ORGANIZED,
    CATEGORY_NARRATIVE,
    MODIFIER_ACUTE_CHRONIC,
    classify_bhc_structure,
)

_REPO_ROOT = Path(__file__).parent.parent
_DESIGN_DOC = _REPO_ROOT / "docs" / "extraction-design.md"


def _design_doc_text() -> str:
    return _DESIGN_DOC.read_text()


# ---------------------------------------------------------------------------
# extraction-design.md
# ---------------------------------------------------------------------------
class TestDesignDocMatchesCode:
    def test_header_pattern_count(self):
        """Doc says 'N case-insensitive regex patterns match the BHC header'."""
        text = _design_doc_text()
        match = re.search(r"(\w+) case-insensitive regex patterns match the BHC header", text)
        assert match is not None, "Could not find header pattern count claim in doc"
        documented = _word_to_int(match.group(1))
        assert documented == len(_BHC_HEADER_PATTERNS), (
            f"Doc claims {documented} header patterns, code has {len(_BHC_HEADER_PATTERNS)}"
        )

    def test_end_boundary_count(self):
        """Doc says 'N patterns identify top-level discharge summary sections'."""
        text = _design_doc_text()
        match = re.search(r"(\d+) patterns identify top-level discharge summary sections", text)
        assert match is not None, "Could not find end-boundary count claim in doc"
        documented = int(match.group(1))
        assert documented == len(_END_BOUNDARIES), (
            f"Doc claims {documented} end boundaries, code has {len(_END_BOUNDARIES)}"
        )

    def test_end_boundary_names(self):
        """Doc's end-boundary table lists the same names as _END_BOUNDARIES."""
        rows = re.findall(r"^\| (?:High|Medium) \| (.+?) \|$", _design_doc_text(), re.MULTILINE)
        documented = [r.strip() for r in rows]
        code = [label for label, _ in _END_BOUNDARIES]
        # The doc writes one label for the pattern that matches both spellings.
        code = ["Discharge Diagnosis/Diagnoses" if c == "Discharge Diagnosis" else c for c in code]
        assert documented == code, f"Doc lists {documented}, code has {code}"

    def test_success_rates_match_empirical_findings(self):
        """The rates quoted here also appear in docs/empirical-findings.md."""
        quoted = set(re.findall(r"(\d{2}\.\d)%", _design_doc_text()))
        findings = (_REPO_ROOT / "docs" / "empirical-findings.md").read_text()
        missing = {r for r in quoted if f"{r}%" not in findings}
        assert not missing, (
            f"Rates in extraction-design.md absent from empirical-findings.md: {missing}"
        )

    def test_min_bhc_length_threshold(self):
        """Doc says 'shorter than N characters'."""
        text = _design_doc_text()
        match = re.search(r"shorter than (\d+) characters", text)
        assert match is not None, "Could not find min BHC length claim in doc"
        documented = int(match.group(1))
        assert documented == MIN_BHC_LENGTH, (
            f"Doc claims {documented}, code has MIN_BHC_LENGTH={MIN_BHC_LENGTH}"
        )


# ---------------------------------------------------------------------------
# bhc-format-coding-rules.md
# ---------------------------------------------------------------------------
_CODING_RULES_DOC = _REPO_ROOT / "docs" / "bhc-format-coding-rules.md"

# Each example is introduced by a sentence naming the label it illustrates,
# then a fenced block. The intro's first word is the expected answer.
_EXAMPLE_RE = re.compile(
    r"^([A-Za-z-]+),[^\n]*:\n\n```text\n(.*?)\n```",
    re.MULTILINE | re.DOTALL,
)


class TestCodingRulesMatchTheClassifier:
    """An example the classifier disagrees with would coach that disagreement,
    so every one of them has to be a case the two settle the same way.
    """

    def _examples(self):
        return _EXAMPLE_RE.findall(_CODING_RULES_DOC.read_text())

    def test_every_example_is_read_the_way_the_doc_says(self):
        expected = {
            "Domain-organized": lambda r: r.category == CATEGORY_DOMAIN_ORGANIZED,
            "Narrative": lambda r: r.category == CATEGORY_NARRATIVE,
            "Present": lambda r: MODIFIER_ACUTE_CHRONIC in r.modifiers,
            "Absent": lambda r: MODIFIER_ACUTE_CHRONIC not in r.modifiers,
        }
        examples = self._examples()
        assert len(examples) == 4, f"Expected 4 examples in the doc, found {len(examples)}"
        for label, text in examples:
            assert label in expected, f"Example labeled {label!r} has no expectation here"
            assert expected[label](classify_bhc_structure(text)), (
                f"The {label!r} example does not classify as the doc says"
            )

    def test_category_threshold(self):
        """Doc says 'N or more explicitly labeled sections'."""
        text = _CODING_RULES_DOC.read_text()
        match = re.search(r"\*\*(\w+) or more\*\* explicitly labeled sections", text)
        assert match is not None, "Could not find the category threshold claim in doc"
        documented = _word_to_int(match.group(1))
        assert documented == _MIN_DOMAIN_SECTIONS, (
            f"Doc claims {documented}, code has _MIN_DOMAIN_SECTIONS={_MIN_DOMAIN_SECTIONS}"
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
