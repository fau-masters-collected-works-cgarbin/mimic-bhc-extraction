"""Tests for the stats CLI subcommand."""

import json
from argparse import Namespace

from mimic_bhc.cli import _load_results, _run_stats


def _make_record(  # noqa: PLR0913
    hadm_id: int,
    *,
    success: bool = True,
    bhc_text: str = "Patient was admitted for chest pain.",
    start_header: str = "Brief Hospital Course:",
    end_header: str = "Medications on Discharge",
    category: str = "narrative",
) -> dict:
    """Build a minimal extraction output record."""
    structure = {"category": category} if success else None
    return {
        "hadm_id": hadm_id,
        "subject_id": 1,
        "row_id": str(hadm_id),
        "chartdate": "2150-01-01",
        "bhc_text": bhc_text if success else None,
        "bhc_char_count": len(bhc_text) if success else 0,
        "bhc_word_count": len(bhc_text.split()) if success else 0,
        "start_header": start_header if success else None,
        "end_header": end_header if success else None,
        "extraction_success": success,
        "failure_reason": None if success else "no_header",
        "quality_flags": [],
        "bhc_structure": structure,
        "bhc_start": 0 if success else None,
        "bhc_end": len(bhc_text) if success else None,
        "note_length": 500,
    }


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
