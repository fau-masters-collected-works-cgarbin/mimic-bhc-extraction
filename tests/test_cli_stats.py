"""Tests for the stats CLI subcommand."""

import json
from argparse import Namespace

from mimic_bhc.cli import _load_results, _run_stats
from mimic_bhc.model import BHCResult
from mimic_bhc.structure import StructureResult


def _make_result(
    hadm_id: int = 1,
    *,
    success: bool = True,
    bhc_text: str = "Patient was admitted for chest pain.",
    start_header: str = "Brief Hospital Course:",
    end_header: str = "Medications on Discharge",
    category: str = "narrative",
) -> BHCResult:
    """Build a BHCResult for CLI stats tests."""
    structure = StructureResult(category=category) if success else None
    return BHCResult(
        success=success,
        bhc_text=bhc_text if success else None,
        start_header=start_header if success else None,
        end_header=end_header if success else None,
        failure_reason=None if success else "no_header",
        hadm_id=hadm_id,
        note_row_id=str(hadm_id),
        subject_id=1,
        chartdate="2150-01-01",
        note_length=500,
        quality_flags=(),
        bhc_structure=structure,
        bhc_start=0 if success else None,
        bhc_end=len(bhc_text) if success else None,
    )


def _make_record(
    hadm_id: int = 1,
    *,
    success: bool = True,
    bhc_text: str = "Patient was admitted for chest pain.",
    start_header: str = "Brief Hospital Course:",
    end_header: str = "Medications on Discharge",
    category: str = "narrative",
) -> dict:
    """Build a JSON record by serializing a BHCResult via to_dict()."""
    return _make_result(
        hadm_id,
        success=success,
        bhc_text=bhc_text,
        start_header=start_header,
        end_header=end_header,
        category=category,
    ).to_dict()


# ---------------------------------------------------------------------------
# _load_results
# ---------------------------------------------------------------------------


class TestLoadResults:
    def test_load_jsonl(self, tmp_path):
        path = tmp_path / "results.jsonl"
        records = [_make_record(1), _make_record(2)]
        path.write_text("\n".join(json.dumps(r) for r in records))
        loaded = _load_results(str(path))
        assert len(loaded) == 2
        assert loaded[0]["hadm_id"] == 1

    def test_load_json_array(self, tmp_path):
        path = tmp_path / "results.json"
        records = [_make_record(1), _make_record(2)]
        path.write_text(json.dumps(records, indent=2))
        loaded = _load_results(str(path))
        assert len(loaded) == 2

    def test_load_empty_file(self, tmp_path):
        path = tmp_path / "empty.jsonl"
        path.write_text("")
        loaded = _load_results(str(path))
        assert loaded == []


# ---------------------------------------------------------------------------
# _run_stats
# ---------------------------------------------------------------------------


class TestRunStats:
    def test_basic_output(self, tmp_path, capsys):
        path = tmp_path / "results.jsonl"
        records = [
            _make_record(1),
            _make_record(2, category="domain_organized"),
            _make_record(3, success=False),
        ]
        path.write_text("\n".join(json.dumps(r) for r in records))

        args = Namespace(input=str(path))
        ret = _run_stats(args)
        assert ret == 0

        output = capsys.readouterr().out
        assert "Total admissions:" in output
        assert "3" in output
        assert "Successful:" in output
        assert "Failed:" in output
        assert "no_header" in output
        assert "Brief Hospital Course:" in output
        assert "Medications on Discharge" in output
        assert "narrative" in output
        assert "domain_organized" in output

    def test_empty_input(self, tmp_path, capsys):
        path = tmp_path / "empty.jsonl"
        path.write_text("")

        args = Namespace(input=str(path))
        ret = _run_stats(args)
        assert ret == 0

        output = capsys.readouterr().out
        assert "No records found" in output

    def test_all_failures(self, tmp_path, capsys):
        path = tmp_path / "results.jsonl"
        records = [
            _make_record(1, success=False),
            _make_record(2, success=False),
        ]
        path.write_text("\n".join(json.dumps(r) for r in records))

        args = Namespace(input=str(path))
        ret = _run_stats(args)
        assert ret == 0

        output = capsys.readouterr().out
        assert "Failed:" in output
        assert "0.0%" in output  # success rate


class TestFromDictRoundtrip:
    """Verify that JSON files produced by to_dict() can be loaded back
    into BHCResult objects via from_dict(), without manual field remapping."""

    def test_load_and_reconstruct(self, tmp_path):
        records = [_make_record(1), _make_record(2, success=False)]
        path = tmp_path / "results.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in records))

        loaded = _load_results(str(path))
        reconstructed = [BHCResult.from_dict(r) for r in loaded]

        assert len(reconstructed) == 2
        assert reconstructed[0].hadm_id == 1
        assert reconstructed[0].success is True
        assert reconstructed[0].subject_id == 1
        assert reconstructed[0].bhc_structure is not None
        assert reconstructed[0].bhc_structure.category == "narrative"
        assert reconstructed[1].success is False
        assert reconstructed[1].bhc_structure is None
