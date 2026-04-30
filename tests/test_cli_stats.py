"""Tests for the stats CLI subcommand."""

import json
from argparse import Namespace

import pytest

from mimic_bhc.cli import (
    _print_bucketed_availability,
    _resolve_mimic_paths,
    _run_stats,
)
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
        extraction_success=success,
        bhc_text=bhc_text if success else None,
        start_header=start_header if success else None,
        end_header=end_header if success else None,
        failure_reason=None if success else "no_header",
        hadm_id=hadm_id,
        row_id=str(hadm_id),
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

    def test_patient_concentration_always_shown(self, tmp_path, capsys):
        path = tmp_path / "results.jsonl"
        # Two patients: one with two admissions (hadm 1+2 share subject_id 100),
        # one with a single admission (hadm 3, subject_id 200).
        records = [
            {**_make_record(1), "subject_id": 100},
            {**_make_record(2), "subject_id": 100},
            {**_make_record(3), "subject_id": 200},
        ]
        path.write_text("\n".join(json.dumps(r) for r in records))

        args = Namespace(
            input=str(path),
            mimic_dir=None,
            admissions=None,
            noteevents=None,
            icustays=None,
        )
        ret = _run_stats(args)
        assert ret == 0

        output = capsys.readouterr().out
        assert "Patient concentration:" in output
        assert "Distinct patients:" in output
        assert "Patients with 1 admission:" in output
        assert "Patients with 2 admissions:" in output
        # Population-stat sections should NOT appear without --mimic-dir
        assert "Note-category availability:" not in output
        assert "Length of stay" not in output
        assert "Mortality:" not in output

    def test_resolve_mimic_paths_exits_when_required_table_missing(self):
        """ADMISSIONS provided without NOTEEVENTS: exit with code 2.

        Population stats need both tables. Passing only one is a config error,
        and the CLI must surface it loudly rather than silently skip the
        population block.
        """
        args = Namespace(
            mimic_dir=None,
            admissions="/some/admissions.csv.gz",
            noteevents=None,
            icustays=None,
        )
        with pytest.raises(SystemExit) as exc:
            _resolve_mimic_paths(args)
        assert exc.value.code == 2

    def test_resolve_mimic_paths_returns_none_when_no_source_supplied(self):
        """No --mimic-dir and no per-table flags: skip population stats silently."""
        args = Namespace(
            mimic_dir=None,
            admissions=None,
            noteevents=None,
            icustays=None,
        )
        assert _resolve_mimic_paths(args) is None


# ---------------------------------------------------------------------------
# _print_bucketed_availability
# ---------------------------------------------------------------------------


def _bucket_row(
    label: str,
    *,
    total: tuple[int, int] = (0, 0),
    carevue: tuple[int, int] = (0, 0),
    metavision: tuple[int, int] = (0, 0),
) -> dict:
    """Build a BucketRow. Each tuple is (clinical_narrative, imaging_only)."""
    return {
        "label": label,
        "total": {"clinical_narrative": total[0], "imaging_only": total[1]},
        "carevue": {"clinical_narrative": carevue[0], "imaging_only": carevue[1]},
        "metavision": {
            "clinical_narrative": metavision[0],
            "imaging_only": metavision[1],
        },
    }


class TestPrintBucketedAvailability:
    def test_renders_header_data_and_total_rows(self, capsys):
        breakdown = {
            "rows": [
                _bucket_row(
                    "<3 days",
                    total=(4, 1),
                    carevue=(2, 1),
                    metavision=(2, 0),
                ),
                _bucket_row(
                    "3 to 4 days",
                    total=(6, 4),
                    carevue=(3, 2),
                    metavision=(3, 2),
                ),
            ],
            "excluded": {"total": 0, "carevue": 0, "metavision": 0},
        }
        _print_bucketed_availability("By LOS bucket:", breakdown)
        out = capsys.readouterr().out
        assert "By LOS bucket:" in out
        assert "All count" in out
        assert "MetaVision count" in out
        assert "clin_narr %" in out
        # <3 days bucket clinical_narrative %: 4/5 = 80.0.
        assert "80.0" in out
        # Total row counts (Total / CareVue / MetaVision) anchored against
        # the literal Total prefix so substrings inside other rows do not match.
        assert "Total        15" in out
        assert "Mixed and No-ICU systems are aggregated" in out
        assert "admissions excluded due to missing value" not in out

    def test_renders_excluded_comment(self, capsys):
        breakdown = {
            "rows": [
                _bucket_row("<3 days", total=(1, 0), metavision=(1, 0)),
            ],
            "excluded": {"total": 7, "carevue": 4, "metavision": 3},
        }
        _print_bucketed_availability("By LOS bucket:", breakdown)
        out = capsys.readouterr().out
        assert "admissions excluded due to missing value" in out
        assert "7 total" in out
        assert "4 CareVue" in out
        assert "3 MetaVision" in out
        # Per-system excluded counts surfaced in the comment.
        assert "7 total" in out
        assert "4 CareVue" in out
        assert "3 MetaVision" in out
