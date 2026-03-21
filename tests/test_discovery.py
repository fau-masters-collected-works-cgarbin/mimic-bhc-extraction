"""Tests for the discovery module (header scanning and classification)."""

from mimic_bhc.discovery import classify_header_role, discover_headers_in_texts


class TestClassifyHeaderRole:
    """Tests for classify_header_role."""

    def test_bhc_header_brief(self) -> None:
        assert classify_header_role("Brief Hospital Course") == "BHC header"

    def test_bhc_header_hospital_course(self) -> None:
        assert classify_header_role("HOSPITAL COURSE") == "BHC header"

    def test_bhc_header_nicu(self) -> None:
        assert classify_header_role("NICU Course") == "BHC header"

    def test_end_boundary_medications(self) -> None:
        assert classify_header_role("Medications on Admission") == "End boundary"

    def test_end_boundary_discharge_condition(self) -> None:
        assert classify_header_role("Discharge Condition") == "End boundary"

    def test_end_boundary_discharge_diagnosis(self) -> None:
        assert classify_header_role("Discharge Diagnoses") == "End boundary"

    def test_other_header(self) -> None:
        assert classify_header_role("History of Present Illness") == "Other"

    def test_other_chief_complaint(self) -> None:
        assert classify_header_role("Chief Complaint") == "Other"

    def test_whitespace_stripped(self) -> None:
        assert classify_header_role("  Brief Hospital Course  ") == "BHC header"


class TestDiscoverHeadersInTexts:
    """Tests for discover_headers_in_texts."""

    def test_empty_input(self) -> None:
        assert discover_headers_in_texts([]) == []

    def test_no_headers(self) -> None:
        texts = ["This is plain text with no section headers at all."]
        assert discover_headers_in_texts(texts) == []

    def test_single_bhc_header(self) -> None:
        texts = ["Some preamble\nBrief Hospital Course:\nThe patient was admitted."]
        result = discover_headers_in_texts(texts)
        headers = {r[0] for r in result}
        assert "Brief Hospital Course" in headers
        bhc_row = next(r for r in result if r[0] == "Brief Hospital Course")
        assert bhc_row[1] == 1
        assert bhc_row[2] == "BHC header"

    def test_end_boundary_detected(self) -> None:
        texts = [
            "Brief Hospital Course:\nPatient admitted.\n"
            "Medications on Admission:\nAspirin"
        ]
        result = discover_headers_in_texts(texts)
        roles = {r[0]: r[2] for r in result}
        assert roles.get("Medications on Admission") == "End boundary"

    def test_frequency_counting(self) -> None:
        note = "Chief Complaint:\nHeadache\nBrief Hospital Course:\nAdmitted."
        texts = [note, note, note]
        result = discover_headers_in_texts(texts)
        bhc_row = next(r for r in result if r[0] == "Brief Hospital Course")
        assert bhc_row[1] == 3

    def test_sorted_by_frequency(self) -> None:
        texts = [
            "Chief Complaint:\nX\nBrief Hospital Course:\nY\n"
            "Medications on Admission:\nZ",
            "Brief Hospital Course:\nA\nMedications on Admission:\nB",
        ]
        result = discover_headers_in_texts(texts)
        # BHC and Medications appear 2x, Chief Complaint 1x
        counts = [r[1] for r in result]
        assert counts == sorted(counts, reverse=True)

    def test_none_and_empty_texts_skipped(self) -> None:
        texts = ["", None, "Brief Hospital Course:\nText"]  # type: ignore[list-item]
        result = discover_headers_in_texts(texts)
        assert len(result) >= 1

    def test_multiple_header_types(self) -> None:
        texts = [
            "History of Present Illness:\nFever.\n"
            "Brief Hospital Course:\nAdmitted.\n"
            "Discharge Condition:\nStable."
        ]
        result = discover_headers_in_texts(texts)
        roles = {r[0]: r[2] for r in result}
        assert roles["Brief Hospital Course"] == "BHC header"
        assert roles["Discharge Condition"] == "End boundary"
        assert roles["History of Present Illness"] == "Other"
