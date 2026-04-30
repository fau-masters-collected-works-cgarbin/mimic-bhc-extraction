"""Tests for mimic_bhc.io (result loading and writing)."""

import json
from pathlib import Path

import pytest

from mimic_bhc.io import load_jsonl, load_results, write_jsonl, write_results
from mimic_bhc.model import BHCResult


def _make_result_dict(hadm_id: int = 1, *, success: bool = True) -> dict:
    """Create a minimal BHCResult dict for testing."""
    return {
        "extraction_success": success,
        "bhc_text": "Patient was admitted." if success else None,
        "start_header": "Brief Hospital Course:" if success else None,
        "end_header": "Discharge Medications:" if success else None,
        "failure_reason": None if success else "no_header",
        "hadm_id": hadm_id,
    }


class TestLoadResults:
    def test_load_jsonl(self, tmp_path: Path):
        """Should load line-delimited JSONL."""
        path = tmp_path / "results.jsonl"
        records = [_make_result_dict(i) for i in range(3)]
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n")

        results = load_results(path)
        assert len(results) == 3
        assert all(isinstance(r, BHCResult) for r in results)
        assert results[0].hadm_id == 0
        assert results[2].hadm_id == 2

    def test_load_json_array(self, tmp_path: Path):
        """Should load pretty-printed JSON array."""
        path = tmp_path / "results.json"
        records = [_make_result_dict(i) for i in range(2)]
        path.write_text(json.dumps(records, indent=2))

        results = load_results(path)
        assert len(results) == 2
        assert results[0].extraction_success is True

    def test_load_empty_file(self, tmp_path: Path):
        """Should return empty list for empty file."""
        path = tmp_path / "empty.jsonl"
        path.write_text("")

        results = load_results(path)
        assert results == []

    def test_load_jsonl_with_blank_lines(self, tmp_path: Path):
        """Should skip blank lines in JSONL."""
        path = tmp_path / "results.jsonl"
        record = _make_result_dict()
        path.write_text(json.dumps(record) + "\n\n" + json.dumps(record) + "\n")

        results = load_results(path)
        assert len(results) == 2

    def test_load_preserves_fields(self, tmp_path: Path):
        """Should preserve key fields through round-trip."""
        path = tmp_path / "results.jsonl"
        record = _make_result_dict(hadm_id=42, success=False)
        path.write_text(json.dumps(record) + "\n")

        results = load_results(path)
        assert results[0].hadm_id == 42
        assert results[0].extraction_success is False
        assert results[0].failure_reason == "no_header"

    def test_load_nonexistent_raises(self, tmp_path: Path):
        """Should raise FileNotFoundError for missing file."""
        with pytest.raises(FileNotFoundError):
            load_results(tmp_path / "nonexistent.jsonl")


def _make_bhc_result(hadm_id: int = 1) -> BHCResult:
    return BHCResult(
        extraction_success=True,
        bhc_text="Patient was admitted.",
        start_header="Brief Hospital Course:",
        end_header="Discharge Medications:",
        failure_reason=None,
        hadm_id=hadm_id,
    )


class TestLoadJsonl:
    def test_load_dicts(self, tmp_path: Path):
        """Should load dicts from JSONL."""
        path = tmp_path / "data.jsonl"
        records = [{"id": 1, "flag": "ok"}, {"id": 2, "flag": "short"}]
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n")

        loaded = load_jsonl(path)
        assert len(loaded) == 2
        assert loaded[0]["id"] == 1
        assert loaded[1]["flag"] == "short"

    def test_skips_blank_lines(self, tmp_path: Path):
        """Should skip blank lines."""
        path = tmp_path / "data.jsonl"
        path.write_text('{"x": 1}\n\n{"x": 2}\n')

        loaded = load_jsonl(path)
        assert len(loaded) == 2

    def test_roundtrip_with_write_jsonl(self, tmp_path: Path):
        """write_jsonl then load_jsonl should preserve data."""
        path = tmp_path / "roundtrip.jsonl"
        original = [{"hadm_id": i, "status": "ok"} for i in range(5)]
        write_jsonl(original, path)

        loaded = load_jsonl(path)
        assert loaded == original


class TestWriteResults:
    def test_write_jsonl(self, tmp_path: Path):
        """Should write JSONL by default."""
        path = tmp_path / "out.jsonl"
        results = [_make_bhc_result(i) for i in range(3)]
        write_results(results, path)

        lines = [line for line in path.read_text().splitlines() if line.strip()]
        assert len(lines) == 3
        parsed = json.loads(lines[0])
        assert parsed["hadm_id"] == 0
        assert parsed["extraction_success"] is True

    def test_write_pretty(self, tmp_path: Path):
        """Should write pretty-printed JSON array when pretty=True."""
        path = tmp_path / "out.json"
        results = [_make_bhc_result(1), _make_bhc_result(2)]
        write_results(results, path, pretty=True)

        parsed = json.loads(path.read_text())
        assert isinstance(parsed, list)
        assert len(parsed) == 2

    def test_roundtrip(self, tmp_path: Path):
        """write_results then load_results should preserve data."""
        path = tmp_path / "roundtrip.jsonl"
        original = [_make_bhc_result(i) for i in range(5)]
        write_results(original, path)

        loaded = load_results(path)
        assert len(loaded) == 5
        assert loaded[3].hadm_id == 3
        assert loaded[0].bhc_text == "Patient was admitted."

    def test_creates_parent_dirs(self, tmp_path: Path):
        """Should create parent directories if they don't exist."""
        path = tmp_path / "nested" / "dir" / "out.jsonl"
        write_results([_make_bhc_result()], path)
        assert path.exists()


class TestWriteJsonl:
    def test_write_dicts(self, tmp_path: Path):
        """Should write dicts as JSONL."""
        path = tmp_path / "audit.jsonl"
        records = [{"hadm_id": 1, "flag": "short"}, {"hadm_id": 2, "flag": "ok"}]
        write_jsonl(records, path)

        lines = [line for line in path.read_text().splitlines() if line.strip()]
        assert len(lines) == 2
        assert json.loads(lines[0])["hadm_id"] == 1

    def test_creates_parent_dirs(self, tmp_path: Path):
        """Should create parent directories if they don't exist."""
        path = tmp_path / "sub" / "audit.jsonl"
        write_jsonl([{"x": 1}], path)
        assert path.exists()
