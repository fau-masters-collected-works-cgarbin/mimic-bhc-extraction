"""Tests for the validation-sample draw in scripts/sample_structure_validation.py.

A defect here invalidates the validation. These tests cover the group a case
is assigned to, the exclusion list, and reproducing the draw from the recorded
seed.
"""

import sample_structure_validation as sample
from mimic_bhc.structure import (
    CATEGORY_DOMAIN_ORGANIZED,
    CATEGORY_NARRATIVE,
    CATEGORY_STANDARDIZED_TEMPLATE,
    MODIFIER_ACUTE_CHRONIC,
    MODIFIER_TRANSITIONAL_ISSUES,
    StructureResult,
)

NARRATIVE_TEXT = "The patient was admitted and improved over three days.\n"
DOMAIN_TEXT = "# Pneumonia\nTreated.\n\n# Anemia\nStable.\n"
ACUTE_CHRONIC_TEXT = "ACUTE ISSUES:\n# Pneumonia\nTreated.\n\n# Anemia\nStable.\n"


class TestGroupFor:
    def test_narrative(self):
        result = StructureResult(category=CATEGORY_NARRATIVE)
        assert sample.group_for(result) == sample.GROUP_NARRATIVE

    def test_domain_organized(self):
        result = StructureResult(category=CATEGORY_DOMAIN_ORGANIZED)
        assert sample.group_for(result) == sample.GROUP_DOMAIN

    def test_standardized_template_folds_into_domain(self):
        """The coding rules tell the coder to assign domain-organized to a template."""
        result = StructureResult(category=CATEGORY_STANDARDIZED_TEMPLATE)
        assert sample.group_for(result) == sample.GROUP_DOMAIN

    def test_acute_chronic_takes_precedence_over_category(self):
        result = StructureResult(
            category=CATEGORY_DOMAIN_ORGANIZED,
            modifiers=(MODIFIER_ACUTE_CHRONIC,),
        )
        assert sample.group_for(result) == sample.GROUP_ACUTE_CHRONIC

    def test_narrative_with_acute_chronic_is_not_drawn(self):
        """No group covers it, and folding it into either would confound both rules."""
        result = StructureResult(
            category=CATEGORY_NARRATIVE,
            modifiers=(MODIFIER_ACUTE_CHRONIC,),
        )
        assert sample.group_for(result) is None

    def test_transitional_issues_does_not_affect_the_group(self):
        """No rule is scored on it, so no group is stratified by it."""
        result = StructureResult(
            category=CATEGORY_DOMAIN_ORGANIZED,
            modifiers=(MODIFIER_TRANSITIONAL_ISSUES,),
        )
        assert sample.group_for(result) == sample.GROUP_DOMAIN


class TestBuildPools:
    def test_excluded_admissions_never_enter_a_pool(self):
        records = [
            ("mimic-iii", "100", NARRATIVE_TEXT),
            ("mimic-iii", "200", NARRATIVE_TEXT),
        ]
        pools = sample.build_pools(records, exclude={"100"})
        assert pools[(sample.GROUP_NARRATIVE, "mimic-iii")] == ["200"]

    def test_pools_are_sorted_so_the_draw_does_not_depend_on_file_order(self):
        forward = [("mimic-iii", h, NARRATIVE_TEXT) for h in ("300", "100", "200")]
        reverse = list(reversed(forward))
        key = (sample.GROUP_NARRATIVE, "mimic-iii")
        assert sample.build_pools(forward, exclude=set())[key] == ["100", "200", "300"]
        assert sample.build_pools(reverse, exclude=set())[key] == ["100", "200", "300"]

    def test_each_admission_reaches_the_pool_its_text_implies(self):
        records = [
            ("mimic-iii", "100", NARRATIVE_TEXT),
            ("mimic-iv", "200", DOMAIN_TEXT),
            ("mimic-iv", "300", ACUTE_CHRONIC_TEXT),
        ]
        pools = sample.build_pools(records, exclude=set())
        assert pools[(sample.GROUP_NARRATIVE, "mimic-iii")] == ["100"]
        assert pools[(sample.GROUP_DOMAIN, "mimic-iv")] == ["200"]
        assert pools[(sample.GROUP_ACUTE_CHRONIC, "mimic-iv")] == ["300"]


def _full_pools():
    """A pool of 100 candidates for every group, more than any quota needs."""
    return {
        (group, dataset): [f"{group}-{dataset}-{i:03d}" for i in range(100)]
        for group, dataset in sample.QUOTAS
    }


class TestDraw:
    def test_every_quota_is_filled_exactly(self):
        cases = sample.draw(_full_pools(), seed=1)
        drawn = dict.fromkeys(sample.QUOTAS, 0)
        for case in cases:
            drawn[case.group, case.dataset] += 1
        assert drawn == sample.QUOTAS

    def test_cases_are_unique_and_numbered_from_one(self):
        cases = sample.draw(_full_pools(), seed=1)
        assert len({case.hadm_id for case in cases}) == sum(sample.QUOTAS.values())
        assert [case.case_no for case in cases] == list(range(1, len(cases) + 1))

    def test_the_same_seed_reproduces_the_draw(self):
        first = sample.draw(_full_pools(), seed=1)
        second = sample.draw(_full_pools(), seed=1)
        assert first == second

    def test_case_order_does_not_track_the_groups(self):
        """Cases in group order would tell the coder the answer from position alone."""
        groups = [case.group for case in sample.draw(_full_pools(), seed=1)]
        assert groups != sorted(groups)


class TestThresholds:
    def test_each_rule_is_scored_over_the_cases_its_limit_assumes(self):
        pooled = {"category": 0, "acute_chronic": 0}
        for (group, _), quota in sample.QUOTAS.items():
            rule = "acute_chronic" if group == sample.GROUP_ACUTE_CHRONIC else "category"
            pooled[rule] += quota
        assert pooled == {"category": 30, "acute_chronic": 10}
        assert set(pooled) == set(sample.MAX_DISAGREEMENTS)
