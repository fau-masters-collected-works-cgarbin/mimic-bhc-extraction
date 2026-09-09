"""Tests for BHC organizational structure classification (3-category taxonomy)."""

from mimic_bhc.structure import (
    CATEGORY_DOMAIN_ORGANIZED,
    CATEGORY_NARRATIVE,
    CATEGORY_STANDARDIZED_TEMPLATE,
    MODIFIER_ACUTE_CHRONIC,
    MODIFIER_TRANSITIONAL_ISSUES,
    classify_bhc_structure,
)

# ---------------------------------------------------------------------------
# Narrative
# ---------------------------------------------------------------------------

NARRATIVE_SIMPLE = """\
The patient was admitted with chest pain. Cardiac enzymes were negative.
Echocardiogram showed normal EF. Patient was discharged in stable condition.
"""

NARRATIVE_WITH_TEMPORAL = """\
The patient presented to the ED with shortness of breath. She was admitted
to the MICU for oxygen support. On hospital day 2, she was weaned to
room air. She was transferred to the floor on HD3 and discharged home
on HD5 in stable condition.
"""

NARRATIVE_WITH_TRANSITIONAL = """\
The patient was admitted for COPD exacerbation. She was treated with
steroids and bronchodilators with improvement over 3 days.

Transitional Issues:
- Continue prednisone taper
- Follow up with pulmonology in 2 weeks
"""


class TestCategoryA:
    def test_simple_narrative(self):
        result = classify_bhc_structure(NARRATIVE_SIMPLE)
        assert result.category == CATEGORY_NARRATIVE

    def test_temporal_narrative(self):
        result = classify_bhc_structure(NARRATIVE_WITH_TEMPORAL)
        assert result.category == CATEGORY_NARRATIVE

    def test_narrative_with_transitional_issues(self):
        result = classify_bhc_structure(NARRATIVE_WITH_TRANSITIONAL)
        assert result.category == CATEGORY_NARRATIVE
        assert MODIFIER_TRANSITIONAL_ISSUES in result.modifiers

    def test_narrative_has_no_numbering(self):
        result = classify_bhc_structure(NARRATIVE_SIMPLE)
        assert result.numbering_convention is None


# ---------------------------------------------------------------------------
# Domain-organized
# ---------------------------------------------------------------------------

DOMAIN_HASH = """\
55yo M with HTN, DM admitted with chest pain.

# Chest pain: Troponin negative x3. EKG unchanged. Likely musculoskeletal.
Discharged with ibuprofen.

# Hypertension: Continued home amlodipine. BPs well controlled.

# Diabetes: Continued metformin. A1c 7.2, at goal.
"""

DOMAIN_NUMBERED = """\
85yo F admitted with pneumonia and UTI.

1. Pneumonia: Treated with levofloxacin x5 days. CXR improved.
Afebrile at discharge.

2. UTI: Urine culture grew E. coli. Treated with ciprofloxacin.

3. Anemia: Hct stable in low 30s. Iron studies pending.
"""

DOMAIN_PARENTHESIZED = """\
73yo woman with CNS lymphoma admitted with altered mental status.

(1) Altered Mental Status: CT head showed increased midline shift.
Loaded with phenytoin. EEG consistent with encephalopathy.

(2) CNS Lymphoma: Repeat MRI showed response to chemotherapy.

(3) Code status: Comfort measures only.
"""

DOMAIN_STANDALONE_HEADERS = """\
Brief course by system:
Neuro: Head CT negative. Mental status improved after haldol was weaned.

Respiratory: Required ventilator support initially. Weaned to trach collar
by hospital day 6.

Cardiovascular: Rate controlled with lopressor. Stable hemodynamics.
"""

DOMAIN_DOUBLE_HASH = """\
Progressive metastatic NSCLC with worsening dyspnea.

## Dyspnea: CT chest showed progressive tumor burden. Started on
ipratropium and home oxygen.

## Pain: Continued fentanyl patch with hydromorphone for breakthrough.

## Code Status: DNR/DNI
"""

DOMAIN_SLASH_NUMBERED = """\
40yo woman with AML admitted for failure to thrive.

1/ FAILURE TO THRIVE: Dehydrated on admission, resolved with IVF.
Strong component of depression.

2/ GVHD: Maintained on immunosuppression. Prednisone tapered.

3/ PAIN: Continued dilaudid and lidocaine patch.
"""

DOMAIN_WITH_ACUTE_CHRONIC = """\
Patient summary.

ACUTE ISSUES:
# GI Bleeding: CTA showed active extravasation. S/p coiling.

# HTN: BP meds held during bleed, restarted at discharge.

CHRONIC ISSUES:
# GERD: Continued PPI.

# HLD: Continued statin.
"""

DOMAIN_WITH_TRANSITIONAL = """\
65yo M with CAP and delirium.

# CAP: Treated with augmentin. Afebrile at discharge.

# Delirium: Resolved with treatment of infection.

TRANSITIONAL ISSUES:
- Repeat CXR in 1 week
- Follow up with PCP
"""


class TestCategoryB:
    def test_hash_prefixed(self):
        result = classify_bhc_structure(DOMAIN_HASH)
        assert result.category == CATEGORY_DOMAIN_ORGANIZED
        assert result.numbering_convention == "#"

    def test_numbered(self):
        result = classify_bhc_structure(DOMAIN_NUMBERED)
        assert result.category == CATEGORY_DOMAIN_ORGANIZED
        assert result.numbering_convention == "N."

    def test_parenthesized(self):
        result = classify_bhc_structure(DOMAIN_PARENTHESIZED)
        assert result.category == CATEGORY_DOMAIN_ORGANIZED
        assert result.numbering_convention == "(N)"

    def test_standalone_headers(self):
        result = classify_bhc_structure(DOMAIN_STANDALONE_HEADERS)
        assert result.category == CATEGORY_DOMAIN_ORGANIZED
        assert result.numbering_convention == "standalone"

    def test_double_hash(self):
        result = classify_bhc_structure(DOMAIN_DOUBLE_HASH)
        assert result.category == CATEGORY_DOMAIN_ORGANIZED
        assert result.numbering_convention == "##"

    def test_slash_numbered(self):
        result = classify_bhc_structure(DOMAIN_SLASH_NUMBERED)
        assert result.category == CATEGORY_DOMAIN_ORGANIZED
        assert result.numbering_convention == "N/"

    def test_acute_chronic_partition(self):
        result = classify_bhc_structure(DOMAIN_WITH_ACUTE_CHRONIC)
        assert result.category == CATEGORY_DOMAIN_ORGANIZED
        assert MODIFIER_ACUTE_CHRONIC in result.modifiers

    def test_transitional_issues(self):
        result = classify_bhc_structure(DOMAIN_WITH_TRANSITIONAL)
        assert result.category == CATEGORY_DOMAIN_ORGANIZED
        assert MODIFIER_TRANSITIONAL_ISSUES in result.modifiers

    def test_b_needs_two_sections(self):
        """A single labeled section is not enough for B."""
        single_entry = """\
Patient admitted with pneumonia.

# Pneumonia: Treated with antibiotics. Improved.
"""
        result = classify_bhc_structure(single_entry)
        assert result.category == CATEGORY_NARRATIVE

    def test_closing_paren_numbered(self):
        """1) Problem convention should be detected as B."""
        text = """\
Patient admitted with multiple issues.

1) GIB: Colonoscopy showed diverticulosis with clot.

2) Afib: Rate controlled with verapamil.

3) Syncope: Likely from hypovolemia.
"""
        result = classify_bhc_structure(text)
        assert result.category == CATEGORY_DOMAIN_ORGANIZED
        assert result.numbering_convention == "N)"

    def test_mixed_conventions(self):
        """When no single convention dominates, numbering_convention is 'mixed'."""
        text = """\
Patient admitted with multiple issues.

# CHF exacerbation: Diuresed with IV Lasix.

1. UTI: Treated with ciprofloxacin.

* Anemia: Transfused 1 unit PRBCs.
"""
        result = classify_bhc_structure(text)
        assert result.category == CATEGORY_DOMAIN_ORGANIZED
        assert result.numbering_convention == "mixed"

    def test_discharge_headers_not_counted(self):
        """Discharge-summary section headers inside the BHC should not
        cause a narrative to be misclassified as domain-organized.

        Regression test for case 121120 where 'CONDITION ON DISCHARGE:'
        and 'DISCHARGE MEDICATIONS:' appeared inside the extracted BHC
        due to extraction boundary issues.
        """
        narrative_with_discharge_headers = """\
The patient was admitted and treated with antibiotics. She improved
over three days and was ready for discharge on POD4.

CONDITION ON DISCHARGE: Stable and improved.

DISCHARGE MEDICATIONS: Amoxicillin 500mg three times daily.
"""
        result = classify_bhc_structure(narrative_with_discharge_headers)
        assert result.category == CATEGORY_NARRATIVE

    def test_every_end_boundary_is_excluded_from_the_section_count(self):
        """Every end-boundary section is ignored when it leaks into the BHC.

        Two of each label, so failing to ignore them would clear the
        two-section threshold and read as domain-organized.
        """
        from mimic_bhc.patterns import _END_BOUNDARIES

        for label, _ in _END_BOUNDARIES:
            bhc = (
                "The patient was admitted and treated with antibiotics.\n"
                "She improved over three days.\n"
                "\n"
                f"{label.upper()}: Stable and improved.\n"
                "\n"
                f"{label.upper()}: A second one, to clear the two-section threshold.\n"
            )
            result = classify_bhc_structure(bhc)
            assert result.category == CATEGORY_NARRATIVE, f"{label!r} counted as a labeled section"


# ---------------------------------------------------------------------------
# Standardized template
# ---------------------------------------------------------------------------

NICU_TEMPLATE = """\
Infant admitted to NICU for respiratory distress.

Respiratory: Required CPAP for 24 hours, then weaned to room air.

Cardiovascular: Hemodynamically stable. No murmur.

Fluids, electrolytes and nutrition: Started feeds on DOL1.
Advanced to full volume without difficulty.

Gastrointestinal: Peak bilirubin 10.7 on DOL3. Phototherapy discontinued.

Hematology: CBC on admission normal. No transfusions.

Infectious disease: Not treated with antibiotics. Blood culture negative.

Neurology: Normal neurologic examination.

Sensory: Hearing screening passed bilaterally.

Psychosocial: Parents involved in care.
"""

NICU_PARTIAL = """\
Infant in NICU.

Respiratory: Room air.

Cardiovascular: Stable.

Fluids, electrolytes and nutrition: Feeds advanced.

Hematology: Normal CBC.

Sensory: Hearing screening pending.
"""


class TestCategoryC:
    def test_nicu_template_full(self):
        result = classify_bhc_structure(NICU_TEMPLATE)
        assert result.category == CATEGORY_STANDARDIZED_TEMPLATE

    def test_nicu_template_partial(self):
        """5 NICU domains should still classify as C (threshold is 4)."""
        result = classify_bhc_structure(NICU_PARTIAL)
        assert result.category == CATEGORY_STANDARDIZED_TEMPLATE

    def test_nicu_below_threshold(self):
        """3 NICU domains is below the standardized-template threshold."""
        three_domains = """\
Respiratory: Room air.

Cardiovascular: Stable.

Neurology: Normal exam.
"""
        result = classify_bhc_structure(three_domains)
        assert result.category != CATEGORY_STANDARDIZED_TEMPLATE

    def test_nicu_domains_without_specific_marker(self):
        """4+ NICU domains without Sensory or Psychosocial classify as domain-organized.

        Regression test for cases like 134125 and 195087 where organ-system
        entries (Cardiovascular, Respiratory, etc.) matched NICU domains
        but were not NICU templates.
        """
        organ_system_review = """\
1. Cardiovascular: Stable hemodynamics. No arrhythmias.

2. Respiratory: Weaned to room air on HD2.

3. Hematology: Hct stable. No transfusions.

4. Infectious disease: Afebrile. Blood cultures negative.

5. Neurology: Alert and oriented. No focal deficits.
"""
        result = classify_bhc_structure(organ_system_review)
        assert result.category == CATEGORY_DOMAIN_ORGANIZED


# ---------------------------------------------------------------------------
# Modifier detection
# ---------------------------------------------------------------------------


class TestModifiers:
    def test_acute_chronic_variants(self):
        """Case, the issues/problems noun, compound keywords, dividers, and bold all fire."""
        for header in [
            "ACUTE ISSUES:",
            "Acute Issues:",
            "ACTIVE ISSUES:",
            "CHRONIC ISSUES:",
            "Chronic Issues:",
            "CHRONIC/STABLE ISSUES:",
            "ACUTE/ACTIVE ISSUES:",
            "INACTIVE ISSUES:",
            "INACTIVE/CHRONIC ISSUES:",
            ">> ACTIVE ISSUES:",
            ">> ACTIVE ISSUES",
            "*Chronic Issues:",
            # "Problems" is the same partition under a different noun.
            "ACUTE/ACTIVE PROBLEMS:",
            "CHRONIC/STABLE PROBLEMS:",
            "ACTIVE PROBLEMS",
            # Markdown bold wraps the header without changing it.
            "**ACUTE ISSUES**",
        ]:
            text = f"""\
{header}
# Problem1: details

# Problem2: details
"""
            result = classify_bhc_structure(text)
            assert MODIFIER_ACUTE_CHRONIC in result.modifiers, f"Failed to detect: {header}"

    def test_transitional_variants(self):
        """Case, the optional 'care', the short 'Transitions' form, and a hash prefix all fire."""
        for header in [
            "Transitional Issues:",
            "TRANSITIONAL ISSUES:",
            "Transitional Issues",
            "TRANSITIONAL CARE ISSUES:",
            "Transitions:",
            "#Transitions:",
        ]:
            text = f"""\
# Problem1: details

# Problem2: details

{header}
- follow up item
"""
            result = classify_bhc_structure(text)
            assert MODIFIER_TRANSITIONAL_ISSUES in result.modifiers, f"Failed to detect: {header}"

    def test_no_false_acute_chronic(self):
        """Mid-sentence 'acute issues' or 'chronic issue' should not trigger the modifier."""
        text = """\
# Problem1: No active issues. Continued home medications.

# Problem2: This is a chronic issue for the patient.
"""
        result = classify_bhc_structure(text)
        assert MODIFIER_ACUTE_CHRONIC not in result.modifiers

    def test_no_false_acute_chronic_problems(self):
        """A line opening with the keyword but continuing into prose is not a header."""
        text = """\
Chronic problems were managed per the outpatient regimen.

# Problem1: details

# Problem2: details
"""
        result = classify_bhc_structure(text)
        assert MODIFIER_ACUTE_CHRONIC not in result.modifiers

    def test_no_false_transitional(self):
        """'transition' mid-sentence should not trigger the modifier."""
        text = """\
The patient made a smooth transition from IV to oral antibiotics.
She was discharged in stable condition.
"""
        result = classify_bhc_structure(text)
        assert MODIFIER_TRANSITIONAL_ISSUES not in result.modifiers
