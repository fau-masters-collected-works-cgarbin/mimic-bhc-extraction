"""Tests for the corpus figures in scripts/bhc_format_stats.py.

Covers the dataset labels, the folding rule, and the per-category medians.
"""

import bhc_format_stats as stats
from mimic_bhc.structure import (
    CATEGORY_DOMAIN_ORGANIZED,
    CATEGORY_NARRATIVE,
    CATEGORY_STANDARDIZED_TEMPLATE,
    MODIFIER_ACUTE_CHRONIC,
    MODIFIER_TRANSITIONAL_ISSUES,
    StructureResult,
)


def _row(category, length, modifiers=()):
    return StructureResult(category=category, modifiers=modifiers), length


class TestDatasetFor:
    def test_hadm_id_width_names_the_dataset(self):
        assert stats.dataset_for("100018") == "mimic-iii"
        assert stats.dataset_for("26488509") == "mimic-iv"


class TestSummarize:
    def test_templates_count_as_domain_organized_and_as_their_own_subtype(self):
        """The coding rules label a template domain-organized."""
        summary = stats.summarize(
            [
                _row(CATEGORY_NARRATIVE, 1000),
                _row(CATEGORY_DOMAIN_ORGANIZED, 3000),
                _row(CATEGORY_STANDARDIZED_TEMPLATE, 3000),
            ]
        )
        assert (summary.narrative, summary.domain, summary.template) == (1, 2, 1)

    def test_medians_are_taken_after_folding(self):
        """A template's length belongs to the domain-organized median it is reported in."""
        summary = stats.summarize(
            [
                _row(CATEGORY_NARRATIVE, 1000),
                _row(CATEGORY_NARRATIVE, 2000),
                _row(CATEGORY_DOMAIN_ORGANIZED, 3000),
                _row(CATEGORY_STANDARDIZED_TEMPLATE, 5000),
            ]
        )
        assert (summary.narrative_median, summary.domain_median) == (1500, 4000)

    def test_acute_chronic_is_counted_whatever_the_category(self):
        summary = stats.summarize(
            [
                _row(CATEGORY_NARRATIVE, 1000, (MODIFIER_ACUTE_CHRONIC,)),
                _row(CATEGORY_DOMAIN_ORGANIZED, 3000, (MODIFIER_ACUTE_CHRONIC,)),
                _row(CATEGORY_DOMAIN_ORGANIZED, 3000, (MODIFIER_TRANSITIONAL_ISSUES,)),
            ]
        )
        assert summary.acute_chronic == 2
        assert summary.total == 3
