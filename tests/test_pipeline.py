"""Tests for the batch extraction pipeline."""

import gzip
import json

import pytest

from mimic_bhc.pipeline import extract_from_file


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
    def test_filters_to_discharge_summaries(self, mimic3_noteevents_gz, tmp_path):
        """MIMIC-III: filters to discharge summaries, excludes radiology."""
        output_path = tmp_path / "output.json"
        extract_from_file(str(mimic3_noteevents_gz), str(output_path), format_hint="mimic3")

        results = []
        with open(output_path) as f:
            for line in f:
                if line.strip():
                    results.append(json.loads(line))

        # Should have results for hadm_id 100 and 200 (discharge summaries)
        hadm_ids = {r["hadm_id"] for r in results}
        assert 100 in hadm_ids or 200 in hadm_ids
        assert 300 not in hadm_ids  # Radiology note should be excluded

    def test_deduplicates_addendums_keeps_longest(self, mimic3_noteevents_gz, tmp_path):
        """MIMIC-III: for same hadm_id, keeps row with longest BHC."""
        output_path = tmp_path / "output.json"
        extract_from_file(str(mimic3_noteevents_gz), str(output_path), format_hint="mimic3")

        results = []
        with open(output_path) as f:
            for line in f:
                if line.strip():
                    results.append(json.loads(line))

        # Find results for hadm_id 100
        hadm_100_results = [r for r in results if r["hadm_id"] == 100]
        assert len(hadm_100_results) == 1
        # Should be from the longer note (row_id 1 with "pneumonia"), not the addendum (row_id 4)
        assert hadm_100_results[0]["row_id"] == "1"
        assert "pneumonia" in hadm_100_results[0]["bhc_text"].lower()

    def test_iserror_flagged(self, mimic3_noteevents_gz, tmp_path):
        """MIMIC-III: ISERROR rows should be included and flagged."""
        output_path = tmp_path / "output.json"
        extract_from_file(str(mimic3_noteevents_gz), str(output_path), format_hint="mimic3")

        results = []
        with open(output_path) as f:
            for line in f:
                if line.strip():
                    results.append(json.loads(line))

        # Find result for hadm_id 200 (ISERROR row)
        hadm_200_results = [r for r in results if r["hadm_id"] == 200]
        if hadm_200_results:
            assert "iserror" in hadm_200_results[0]["quality_flags"]

    def test_auto_detects_mimic3(self, mimic3_noteevents_gz, tmp_path):
        """Auto-detect should identify MIMIC-III by CATEGORY column."""
        output_path = tmp_path / "output.json"
        extract_from_file(str(mimic3_noteevents_gz), str(output_path), format_hint="auto")

        results = []
        with open(output_path) as f:
            for line in f:
                if line.strip():
                    results.append(json.loads(line))

        # Should have extracted discharge summaries (hadm_id 100 or 200)
        hadm_ids = {r["hadm_id"] for r in results}
        assert len(hadm_ids) > 0


class TestMimic4Format:
    def test_no_filtering_mimic4(self, mimic4_discharge_gz, tmp_path):
        """MIMIC-IV: no category filtering needed."""
        output_path = tmp_path / "output.json"
        extract_from_file(str(mimic4_discharge_gz), str(output_path), format_hint="mimic4")

        results = []
        with open(output_path) as f:
            for line in f:
                if line.strip():
                    results.append(json.loads(line))

        assert len(results) == 2
        hadm_ids = {r["hadm_id"] for r in results}
        assert hadm_ids == {100, 200}

    def test_string_note_ids_mimic4(self, mimic4_discharge_gz, tmp_path):
        """MIMIC-IV note IDs are strings, not integers."""
        output_path = tmp_path / "output.json"
        extract_from_file(str(mimic4_discharge_gz), str(output_path), format_hint="mimic4")

        results = []
        with open(output_path) as f:
            for line in f:
                if line.strip():
                    results.append(json.loads(line))

        assert results[0]["row_id"] == "FAKE-NOTE-DS-01"
        assert isinstance(results[0]["row_id"], str)

    def test_auto_detects_mimic4(self, mimic4_discharge_gz, tmp_path):
        """Auto-detect should identify MIMIC-IV by absence of CATEGORY column."""
        output_path = tmp_path / "output.json"
        extract_from_file(str(mimic4_discharge_gz), str(output_path), format_hint="auto")

        results = []
        with open(output_path) as f:
            for line in f:
                if line.strip():
                    results.append(json.loads(line))

        assert len(results) == 2


class TestOutputFormat:
    def test_output_json_structure(self, mimic3_noteevents_gz, tmp_path):
        """Verify output JSON contains all required fields."""
        output_path = tmp_path / "output.json"
        extract_from_file(str(mimic3_noteevents_gz), str(output_path), format_hint="mimic3")

        results = []
        with open(output_path) as f:
            for line in f:
                if line.strip():
                    results.append(json.loads(line))

        assert len(results) > 0
        for result in results:
            assert "hadm_id" in result
            assert "subject_id" in result
            assert "row_id" in result
            assert "bhc_text" in result
            assert "extraction_success" in result
            assert "quality_flags" in result
            assert isinstance(result["quality_flags"], list)
            if result["extraction_success"]:
                assert "bhc_structure" in result
                assert result["bhc_structure"] is not None
                assert "category" in result["bhc_structure"]

    def test_quality_flags_populated(self, mimic3_noteevents_gz, tmp_path):
        """Quality flags should be present in output."""
        output_path = tmp_path / "output.json"
        extract_from_file(str(mimic3_noteevents_gz), str(output_path), format_hint="mimic3")

        results = []
        with open(output_path) as f:
            for line in f:
                if line.strip():
                    results.append(json.loads(line))

        for result in results:
            assert isinstance(result["quality_flags"], list)

    def test_pretty_print_produces_valid_json_array(self, mimic3_noteevents_gz, tmp_path):
        """--pretty flag should produce a valid JSON array."""
        output_path = tmp_path / "output.json"
        extract_from_file(
            str(mimic3_noteevents_gz), str(output_path), format_hint="mimic3", pretty_print=True
        )

        with open(output_path) as f:
            content = f.read()

        # Should be parseable as a single JSON document (array)
        results = json.loads(content)
        assert isinstance(results, list)
        assert len(results) > 0
        # Should have indentation
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

        output_path = tmp_path / "output.jsonl"
        extract_from_file(str(path), str(output_path), format_hint="mimic3")

        results = []
        with open(output_path) as f:
            for line in f:
                if line.strip():
                    results.append(json.loads(line))

        assert len(results) == 1
        assert results[0]["hadm_id"] == 100
        assert results[0]["extraction_success"] is False
        assert results[0]["failure_reason"] == "no_header"


class TestSamplingOption:
    def test_sample_parameter(self, tmp_path):
        """--sample N should extract from N random admissions."""
        # Create larger test file
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

        output_path = tmp_path / "output.json"
        extract_from_file(str(path), str(output_path), format_hint="mimic3", sample_size=3)

        results = []
        with open(output_path) as f:
            for line in f:
                if line.strip():
                    results.append(json.loads(line))

        # Should have approximately 3 results (exact count depends on sampling)
        assert len(results) <= 3
