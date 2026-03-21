"""Tests for BHC extraction logic."""

from mimic_bhc.extract import extract_bhc

# ---------------------------------------------------------------------------
# Synthetic note fixtures
# ---------------------------------------------------------------------------
STANDARD_NOTE = """\
Admission Date: 2150-01-01
Discharge Date: 2150-01-05

Service: Medicine

Brief Hospital Course:
The patient was admitted with chest pain. Cardiac enzymes were negative.
Echocardiogram showed normal EF. Patient was discharged in stable condition.

Medications on Discharge:
Aspirin 81mg daily
Metoprolol 25mg twice daily

Discharge Condition:
Stable.
"""

UPPERCASE_HEADER_NOTE = """\
BRIEF HOSPITAL COURSE:
Patient admitted for pneumonia. Treated with IV antibiotics.
Improved over three days.

DISCHARGE MEDICATIONS:
Amoxicillin 500mg three times daily
"""

NO_HEADER_NOTE = """\
Admission Date: 2150-01-01
Discharge Date: 2150-01-03

The patient presented with abdominal pain.

Medications on Discharge:
Acetaminophen PRN
"""

INTERNAL_SUBHEADERS_NOTE = """\
Brief Hospital Course:
The patient was admitted with multisystem organ failure.

Cardiovascular:
Troponin was elevated. Started on heparin drip.

Renal:
Creatinine peaked at 3.2. Nephrology consulted.

Infectious Disease:
Blood cultures grew MRSA. Vancomycin started.

Discharge Condition:
Critical but stable.
"""

NO_END_BOUNDARY_NOTE = """\
Brief Hospital Course:
Patient was admitted for observation after a fall.
CT head was negative. Patient ambulated without difficulty.
"""

EMPTY_BHC_NOTE = """\
Brief Hospital Course:

Medications on Discharge:
Aspirin 81mg daily
"""

ABBREVIATED_HEADER_NOTE = """\
Hosp Course:
Patient admitted for elective hip replacement. Surgery uneventful.
Discharged to rehab on POD 3.

Followup Instructions:
Orthopedics in 2 weeks.
"""

HOSPITAL_COURSE_HEADER_NOTE = """\
Hospital Course:
Patient was admitted for COPD exacerbation. Treated with steroids and
nebulizers. Oxygen requirement resolved.

Discharge Instructions:
Continue inhalers as prescribed.
"""

DEIDENTIFICATION_ARTIFACTS_NOTE = """\
Brief Hospital Course:
The patient [**Known firstname 123**] was admitted on [**2150-1-1**].
Treated with [**Doctor Last Name 456**] protocol. Improved.

Discharge Disposition:
Home
"""

INLINE_TEXT_HEADER_NOTE = """\
Admission Date: 2150-01-01

HOSPITAL COURSE:  The patient was admitted with sepsis.
Treated with IV antibiotics. Blood cultures cleared on day 3.
Patient improved and was discharged in stable condition.

Discharge Medications:
Ciprofloxacin 500mg twice daily
"""

SUMMARY_OF_HEADER_NOTE = """\
Admission Date: 2150-01-01

SUMMARY OF HOSPITAL COURSE:  Patient presented with CHF exacerbation.
Diuresed with IV Lasix. Weight decreased by 5kg.

Discharge Condition:
Improved.
"""

CONCISE_SUMMARY_HEADER_NOTE = """\
Admission Date: 2150-01-01

CONCISE SUMMARY OF HOSPITAL COURSE BY ISSUE/SYSTEM:

Cardiovascular:
Acute systolic heart failure. Started on dobutamine.

Renal:
AKI secondary to cardiorenal syndrome. Creatinine peaked at 2.8.

Discharge Disposition:
Home with services.
"""


# ---------------------------------------------------------------------------
# extract_bhc tests
# ---------------------------------------------------------------------------
class TestExtractBhc:
    def test_standard_extraction(self):
        result = extract_bhc(STANDARD_NOTE, hadm_id=100)
        assert result.success is True
        assert result.hadm_id == 100
        assert "chest pain" in result.bhc_text
        assert "Cardiac enzymes" in result.bhc_text
        assert result.start_header == "Brief Hospital Course:"
        assert result.end_header == "Medications on Discharge"
        assert result.failure_reason is None
        assert result.bhc_format == "chronological"

    def test_case_insensitive_uppercase(self):
        result = extract_bhc(UPPERCASE_HEADER_NOTE, hadm_id=101)
        assert result.success is True
        assert "pneumonia" in result.bhc_text
        assert result.end_header == "Discharge Medications"

    def test_no_header(self):
        result = extract_bhc(NO_HEADER_NOTE, hadm_id=102)
        assert result.success is False
        assert result.failure_reason == "no_header"
        assert result.bhc_text is None
        assert result.start_header is None
        assert result.bhc_format is None

    def test_internal_subheaders_preserved(self):
        result = extract_bhc(INTERNAL_SUBHEADERS_NOTE, hadm_id=103)
        assert result.success is True
        assert "Cardiovascular:" in result.bhc_text
        assert "Renal:" in result.bhc_text
        assert "Infectious Disease:" in result.bhc_text
        assert result.end_header == "Discharge Condition"
        assert result.bhc_format == "problem_oriented"

    def test_no_end_boundary(self):
        result = extract_bhc(NO_END_BOUNDARY_NOTE, hadm_id=104)
        assert result.success is True
        assert "observation after a fall" in result.bhc_text
        assert result.end_header is None

    def test_empty_section(self):
        result = extract_bhc(EMPTY_BHC_NOTE, hadm_id=105)
        assert result.success is False
        assert result.failure_reason == "empty_section"

    def test_abbreviated_header(self):
        result = extract_bhc(ABBREVIATED_HEADER_NOTE, hadm_id=106)
        assert result.success is True
        assert "hip replacement" in result.bhc_text
        assert result.end_header == "Followup Instructions"

    def test_hospital_course_header(self):
        result = extract_bhc(HOSPITAL_COURSE_HEADER_NOTE, hadm_id=107)
        assert result.success is True
        assert "COPD exacerbation" in result.bhc_text
        assert result.start_header == "Hospital Course:"

    def test_deidentification_artifacts(self):
        result = extract_bhc(DEIDENTIFICATION_ARTIFACTS_NOTE, hadm_id=108)
        assert result.success is True
        assert "[**Known firstname 123**]" in result.bhc_text
        assert result.end_header == "Discharge Disposition"

    def test_note_row_id_passed_through(self):
        result = extract_bhc(STANDARD_NOTE, hadm_id=100, note_row_id="42")
        assert result.note_row_id == "42"

    def test_inline_text_after_header(self):
        """Header with text continuing on the same line after the colon."""
        result = extract_bhc(INLINE_TEXT_HEADER_NOTE, hadm_id=109)
        assert result.success is True
        assert "sepsis" in result.bhc_text
        assert result.end_header == "Discharge Medications"
        assert result.bhc_format == "chronological"

    def test_summary_of_hospital_course_header(self):
        """'SUMMARY OF HOSPITAL COURSE' variant header."""
        result = extract_bhc(SUMMARY_OF_HEADER_NOTE, hadm_id=110)
        assert result.success is True
        assert "CHF exacerbation" in result.bhc_text
        assert result.end_header == "Discharge Condition"

    def test_concise_summary_header(self):
        """'CONCISE SUMMARY OF HOSPITAL COURSE' variant header."""
        result = extract_bhc(CONCISE_SUMMARY_HEADER_NOTE, hadm_id=111)
        assert result.success is True
        assert "dobutamine" in result.bhc_text
        assert result.bhc_format == "problem_oriented"

    def test_cross_line_hospital_course_not_matched(self):
        """'hospital' at end of line + 'course' at start of next should NOT match."""
        note = """\
The patient had a complicated hospital
course during the admission.

Medications on Discharge:
Aspirin 81mg
"""
        result = extract_bhc(note, hadm_id=112)
        assert result.success is False
        assert result.failure_reason == "no_header"

    def test_bhc_offsets_match_text(self):
        """bhc_start/bhc_end offsets should slice back to the extracted text."""
        result = extract_bhc(STANDARD_NOTE, hadm_id=113)
        assert result.success is True
        assert result.bhc_start is not None
        assert result.bhc_end is not None
        assert STANDARD_NOTE[result.bhc_start : result.bhc_end] == result.bhc_text

    def test_bhc_offsets_with_inline_header(self):
        """Offsets should be correct even when text follows the header on the same line."""
        result = extract_bhc(INLINE_TEXT_HEADER_NOTE, hadm_id=114)
        assert result.success is True
        assert INLINE_TEXT_HEADER_NOTE[result.bhc_start : result.bhc_end] == result.bhc_text

    def test_mid_sentence_hospital_course_not_matched(self):
        """Mid-sentence 'hospital course' should NOT match because the ^ anchor
        requires the pattern to start at the beginning of a line. Only line-initial
        headers are recognized."""
        note = """\
Admission Date: 2150-01-01

The patient had a complicated hospital course during this admission.
He was admitted with pneumonia and treated with antibiotics.
His fever resolved on day 3 and he was discharged home.

Medications on Discharge:
Aspirin 81mg
"""
        result = extract_bhc(note, hadm_id=115)
        assert result.success is False
        assert result.failure_reason == "no_header"


# ---------------------------------------------------------------------------
# Priority-based header matching tests
# ---------------------------------------------------------------------------
class TestHeaderPriorityMatching:
    def test_brief_header_preferred_over_bare_hospital_course(self):
        """When both 'hospital course' and 'Brief Hospital Course:' appear,
        the more specific 'Brief Hospital Course:' should be selected."""
        note = """\
Admission Date: 2150-01-01

His
hospital course has been complicated by increasing cerebral
edema and brainstem herniation.

Brief Hospital Course:
Mr. Smith was admitted to the ICU for close monitoring after
a craniotomy. He was started on mannitol for cerebral edema.

Medications on Discharge:
Mannitol 20% IV
"""
        result = extract_bhc(note, hadm_id=200)
        assert result.success is True
        assert result.start_header == "Brief Hospital Course:"
        assert "admitted to the ICU" in result.bhc_text
        assert "has been complicated" not in result.bhc_text

    def test_bare_hospital_course_used_when_no_specific_match(self):
        """When only 'Hospital Course:' appears (no 'Brief' variant),
        the less specific pattern should still match."""
        note = """\
Admission Date: 2150-01-01

Hospital Course:
Patient was treated for pneumonia with IV antibiotics.

Medications on Discharge:
Amoxicillin 500mg
"""
        result = extract_bhc(note, hadm_id=201)
        assert result.success is True
        assert result.start_header == "Hospital Course:"
        assert "pneumonia" in result.bhc_text

    def test_summary_prefix_preferred_over_bare(self):
        """'Summary of Hospital Course' should match before bare
        'Hospital Course' if both appear."""
        note = """\
Admission Date: 2150-01-01

During the hospital course, the patient developed a fever.

Summary of Hospital Course:
Patient was admitted with CHF. Diuresed with IV Lasix.

Discharge Condition:
Stable.
"""
        result = extract_bhc(note, hadm_id=202)
        assert result.success is True
        assert "Summary" in result.start_header or "SUMMARY" in result.start_header
        assert "CHF" in result.bhc_text
        assert "developed a fever" not in result.bhc_text

    def test_nicu_course_matched_at_correct_priority(self):
        """NICU Course is a distinct pattern and should match independently."""
        note = """\
Date of Birth: 2150-01-01

NICU Course:
Baby was admitted for observation. Feeding well.

Discharge Condition:
Stable.
"""
        result = extract_bhc(note, hadm_id=204)
        assert result.success is True
        assert "NICU" in result.start_header
        assert "observation" in result.bhc_text

    def test_abbreviated_hosp_course_is_lowest_priority(self):
        """'Hosp. Course' is the least specific pattern and should only
        match when no higher-priority pattern exists."""
        note = """\
Hosp. Course:
Patient admitted for elective procedure.

Discharge Condition:
Stable.
"""
        result = extract_bhc(note, hadm_id=205)
        assert result.success is True
        assert "Hosp." in result.start_header


# ---------------------------------------------------------------------------
# Format classification tests
# ---------------------------------------------------------------------------
class TestBhcFormatClassification:
    def test_chronological_no_subheaders(self):
        result = extract_bhc(STANDARD_NOTE, hadm_id=1)
        assert result.bhc_format == "chronological"

    def test_problem_oriented_multiple_systems(self):
        result = extract_bhc(INTERNAL_SUBHEADERS_NOTE, hadm_id=2)
        assert result.bhc_format == "problem_oriented"

    def test_single_subheader_is_chronological(self):
        """A single organ-system sub-header is not enough to classify as
        problem-oriented -- it could be a false positive."""
        note = """\
Brief Hospital Course:
Patient admitted with chest pain.

Cardiovascular:
Troponin negative. Discharged in stable condition.

Medications on Discharge:
Aspirin 81mg daily
"""
        result = extract_bhc(note, hadm_id=3)
        assert result.bhc_format == "chronological"

    def test_two_subheaders_is_problem_oriented(self):
        note = """\
Brief Hospital Course:
Patient admitted with multiorgan failure.

Cardiovascular:
Troponin elevated. Started on heparin.

Renal:
Creatinine peaked at 3.2.

Discharge Condition:
Stable.
"""
        result = extract_bhc(note, hadm_id=4)
        assert result.bhc_format == "problem_oriented"

    def test_abbreviated_subheaders(self):
        """Common abbreviations like Neuro, GI, FEN should be recognized."""
        note = """\
Brief Hospital Course:
Complex admission.

Neuro:
Alert and oriented.

GI:
Tolerating diet.

FEN:
Electrolytes repleted.

Discharge Condition:
Stable.
"""
        result = extract_bhc(note, hadm_id=5)
        assert result.bhc_format == "problem_oriented"

    def test_cardiac_subheader_recognized(self):
        """'Cardiac:' is a common organ-system sub-header."""
        note = """\
Brief Hospital Course:
Patient admitted with chest pain and renal failure.

Cardiac:
Troponin elevated. Started on heparin drip.

Renal:
Creatinine peaked at 3.2. Nephrology consulted.

Discharge Condition:
Stable.
"""
        result = extract_bhc(note, hadm_id=7)
        assert result.bhc_format == "problem_oriented"

    def test_neurologic_subheader_recognized(self):
        """'Neurologic:' (without trailing '-al') should match."""
        note = """\
Brief Hospital Course:
Patient admitted after stroke.

Neurologic:
Alert and oriented. Strength improving.

Respiratory:
Extubated on day 2. Room air.

Discharge Condition:
Stable.
"""
        result = extract_bhc(note, hadm_id=8)
        assert result.bhc_format == "problem_oriented"

    def test_disease_specific_headers_are_chronological(self):
        """Disease-name sub-headers (not organ systems) should not
        trigger problem-oriented classification."""
        note = """\
Brief Hospital Course:
73 y.o. man with CAD presents with nausea.

# Upper GI bleed:
 NG lavage revealed pink fluid. Received PRBCs.

# Hypoglycemia:
FSBG 50-60 on morning of admission. Resolved with PO intake.

# Depression:
Continued on Zoloft.

Medications on Discharge:
Pantoprazole 40mg daily
"""
        result = extract_bhc(note, hadm_id=9)
        assert result.bhc_format == "chronological"

    def test_failed_extraction_has_no_format(self):
        result = extract_bhc(NO_HEADER_NOTE, hadm_id=6)
        assert result.bhc_format is None
