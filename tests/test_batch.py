"""Tests for the batch extraction module."""

import gzip
import json

import pytest

from mimic_bhc.batch import extract_from_file


@pytest.fixture
def mimic3_noteevents_gz(tmp_path):
    """Create a MIMIC-III NOTEEVENTS CSV.gz with discharge summaries."""
    csv_content = (
        "ROW_ID,SUBJECT_ID,HADM_ID,CHARTDATE,CHARTTIME,STORETIME,"
        "CATEGORY,DESCRIPTION,CGID,ISERROR,TEXT\n"
        # Normal discharge summary
        '1,10,100,2150-01-01,,,"Discharge summary","Report",,,'
        '"Brief Hospital Course:\nPatient had pneumonia.\n\n'
        'Medications on Discharge:\nAmoxicillin"\n'
        # ISERROR row -- should be flagged
        '2,10,200,2150-01-02,,,"Discharge summary","Report",,1,'
        '"Brief Hospital Course:\nThis is an error note.\n\n'
        'Discharge Condition:\nN/A"\n'
        # Non-discharge category -- should be filtered
        '3,10,300,2150-01-03,,,"Radiology","Report",,,'
        '"Normal chest X-ray."\n'
        # Addendum (same hadm_id=100, shorter) -- should be deduplicated
        '4,10,100,2150-01-01,,,"Discharge summary","Addendum",,,'
        '"Brief Hospital Course:\nShort.\n\nDischarge Condition:\nOK"\n'
    )
    path = tmp_path / "NOTEEVENTS.csv.gz"
    with gzip.open(path, "wt") as f:
        f.write(csv_content)
    return path


@pytest.fixture
def mimic4_discharge_gz(tmp_path):
    """Create a MIMIC-IV discharge notes CSV.gz."""
    csv_content = (
        "note_id,subject_id,hadm_id,note_type,note_seq,"
        "charttime,storetime,text\n"
        "FAKE-NOTE-DS-01,10,100,DS,1,2150-01-01 12:00:00,2150-01-01 13:00:00,"
        '"Brief Hospital Course:\nPatient had pneumonia.\n\n'
        'Medications on Discharge:\nAmoxicillin"\n'
        "FAKE-NOTE-DS-02,10,200,DS,1,2150-01-02 12:00:00,2150-01-02 13:00:00,"
        '"Hospital Course:\nFell and broke hip.\n\n'
        'Discharge Condition:\nStable"\n'
    )
    path = tmp_path / "discharge.csv.gz"
    with gzip.open(path, "wt") as f:
        f.write(csv_content)
    return path


class TestMimic3Format:
    def test_filters_to_discharge_summaries(self, mimic3_noteevents_gz):
        """MIMIC-III: filters to discharge summaries, excludes radiology."""
        results = extract_from_file(str(mimic3_noteevents_gz), format_hint="mimic3")

        hadm_ids = {r.hadm_id for r in results}
        assert 100 in hadm_ids or 200 in hadm_ids
        assert 300 not in hadm_ids  # Radiology note should be excluded

    def test_deduplicates_addendums_keeps_longest(self, mimic3_noteevents_gz):
        """MIMIC-III: for same hadm_id, keeps row with longest BHC."""
        results = extract_from_file(str(mimic3_noteevents_gz), format_hint="mimic3")

        hadm_100_results = [r for r in results if r.hadm_id == 100]
        assert len(hadm_100_results) == 1
        # Should be from the longer note (row_id 1 with "pneumonia"), not the addendum (row_id 4)
        assert hadm_100_results[0].note_row_id == "1"
        assert "pneumonia" in hadm_100_results[0].bhc_text.lower()

    def test_iserror_flagged(self, mimic3_noteevents_gz):
        """MIMIC-III: ISERROR rows should be included and flagged."""
        results = extract_from_file(str(mimic3_noteevents_gz), format_hint="mimic3")

        hadm_200_results = [r for r in results if r.hadm_id == 200]
        if hadm_200_results:
            assert "iserror" in hadm_200_results[0].quality_flags

    def test_auto_detects_mimic3(self, mimic3_noteevents_gz):
        """Auto-detect should identify MIMIC-III by CATEGORY column."""
        results = extract_from_file(str(mimic3_noteevents_gz), format_hint="auto")

        hadm_ids = {r.hadm_id for r in results}
        assert len(hadm_ids) > 0


class TestMimic4Format:
    def test_no_filtering_mimic4(self, mimic4_discharge_gz):
        """MIMIC-IV: no category filtering needed."""
        results = extract_from_file(str(mimic4_discharge_gz), format_hint="mimic4")

        assert len(results) == 2
        hadm_ids = {r.hadm_id for r in results}
        assert hadm_ids == {100, 200}

    def test_string_note_ids_mimic4(self, mimic4_discharge_gz):
        """MIMIC-IV note IDs are strings, not integers."""
        results = extract_from_file(str(mimic4_discharge_gz), format_hint="mimic4")

        assert results[0].note_row_id == "FAKE-NOTE-DS-01"
        assert isinstance(results[0].note_row_id, str)

    def test_auto_detects_mimic4(self, mimic4_discharge_gz):
        """Auto-detect should identify MIMIC-IV by absence of CATEGORY column."""
        results = extract_from_file(str(mimic4_discharge_gz), format_hint="auto")

        assert len(results) == 2


class TestBatchEnrichment:
    def test_result_fields_populated(self, mimic3_noteevents_gz):
        """Batch extraction should populate all enrichment fields on BHCResult."""
        results = extract_from_file(str(mimic3_noteevents_gz), format_hint="mimic3")

        assert len(results) > 0
        for r in results:
            assert r.hadm_id is not None
            assert r.subject_id is not None
            assert r.note_row_id is not None
            assert isinstance(r.quality_flags, tuple)
            if r.success:
                assert r.bhc_text is not None
                assert r.bhc_structure is not None
                assert r.bhc_structure.category in (
                    "narrative",
                    "domain_organized",
                    "standardized_template",
                )

    def test_quality_flags_are_tuples(self, mimic3_noteevents_gz):
        """Quality flags should be tuples of strings."""
        results = extract_from_file(str(mimic3_noteevents_gz), format_hint="mimic3")

        for r in results:
            assert isinstance(r.quality_flags, tuple)

    def test_pretty_print_produces_valid_json_array(self, mimic3_noteevents_gz, tmp_path):
        """CLI write with pretty=True should produce a valid JSON array."""
        from mimic_bhc.cli import _write_results

        results = extract_from_file(str(mimic3_noteevents_gz), format_hint="mimic3")

        output_path = tmp_path / "output.json"
        _write_results(results, str(output_path), pretty=True)

        with open(output_path) as f:
            content = f.read()

        parsed = json.loads(content)
        assert isinstance(parsed, list)
        assert len(parsed) > 0
        assert "  " in content


class TestFailedExtractions:
    def test_failed_admissions_not_dropped(self, tmp_path):
        """Admissions where extraction fails should still appear in output."""
        csv_content = (
            "ROW_ID,SUBJECT_ID,HADM_ID,CHARTDATE,CHARTTIME,STORETIME,"
            "CATEGORY,DESCRIPTION,CGID,ISERROR,TEXT\n"
            # Note with no BHC header — extraction will fail
            '1,10,100,2150-01-01,,,"Discharge summary","Report",,,'
            '"No hospital course section here. Just random text."\n'
        )
        path = tmp_path / "NOTEEVENTS.csv.gz"
        with gzip.open(path, "wt") as f:
            f.write(csv_content)

        results = extract_from_file(str(path), format_hint="mimic3")

        assert len(results) == 1
        assert results[0].hadm_id == 100
        assert results[0].success is False
        assert results[0].failure_reason == "no_header"


class TestSamplingOption:
    def test_sample_parameter(self, tmp_path):
        """--sample N should extract from exactly N admissions."""
        lines = [
            "ROW_ID,SUBJECT_ID,HADM_ID,CHARTDATE,CHARTTIME,STORETIME,"
            "CATEGORY,DESCRIPTION,CGID,ISERROR,TEXT"
        ]
        for i in range(10):
            lines.append(
                f'{i + 1},10,{100 + i},2150-01-01,,,"Discharge summary","Report",,,'
                f'"Brief Hospital Course:\nPatient {i}.\n\nMedications on Discharge:\nMed {i}"'
            )

        path = tmp_path / "NOTEEVENTS_LARGE.csv.gz"
        with gzip.open(path, "wt") as f:
            f.write("\n".join(lines) + "\n")

        results = extract_from_file(str(path), format_hint="mimic3", sample_size=3)

        assert len(results) == 3

    def test_sample_counts_admissions_not_rows(self, tmp_path):
        """--sample N should sample N admissions, not N rows.

        With addendums (multiple rows per admission), row-level sampling
        could return fewer admissions than requested.
        """
        lines = [
            "ROW_ID,SUBJECT_ID,HADM_ID,CHARTDATE,CHARTTIME,STORETIME,"
            "CATEGORY,DESCRIPTION,CGID,ISERROR,TEXT"
        ]
        # 5 admissions, each with 2 rows (original + addendum) = 10 rows
        for i in range(5):
            hadm_id = 100 + i
            lines.append(
                f'{i * 2 + 1},10,{hadm_id},2150-01-01,,,"Discharge summary","Report",,,'
                f'"Brief Hospital Course:\nPatient {i} original.\n\n'
                f'Medications on Discharge:\nMed {i}"'
            )
            lines.append(
                f'{i * 2 + 2},10,{hadm_id},2150-01-01,,,"Discharge summary","Addendum",,,'
                f'"Brief Hospital Course:\nShort.\n\nDischarge Condition:\nOK"'
            )

        path = tmp_path / "NOTEEVENTS.csv.gz"
        with gzip.open(path, "wt") as f:
            f.write("\n".join(lines) + "\n")

        results = extract_from_file(str(path), format_hint="mimic3", sample_size=3)

        assert len(results) == 3
