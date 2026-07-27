"""Tests for population-level statistics over the BHC extraction universe."""

import gzip

import pytest

from mimic_bhc.model import BHCResult
from mimic_bhc.population_stats import (
    CLINICAL_NARRATIVE,
    DBSOURCE_CAREVUE,
    DBSOURCE_METAVISION,
    DBSOURCE_MIXED,
    IMAGING_ONLY,
    compute_patient_concentration,
    compute_population_stats,
    load_admissions,
    load_icu_dbsource,
    load_note_category_availability,
    load_per_admission_note_volume,
)
from mimic_bhc.structure import StructureResult


def _result(
    *,
    hadm_id: int,
    subject_id: int,
    success: bool = True,
    category: str | None = "narrative",
    bhc_char_count: int = 0,
) -> BHCResult:
    structure = StructureResult(category=category) if success and category else None
    bhc_text = "x" * bhc_char_count if bhc_char_count else None
    return BHCResult(
        extraction_success=success,
        bhc_text=bhc_text,
        start_header=None,
        end_header=None,
        failure_reason=None if success else "no_header",
        hadm_id=hadm_id,
        row_id=str(hadm_id),
        subject_id=subject_id,
        bhc_structure=structure,
    )


# ---------------------------------------------------------------------------
# compute_patient_concentration
# ---------------------------------------------------------------------------


class TestPatientConcentration:
    def test_buckets_distribution(self):
        results = [
            # patient 1: 1 admission
            _result(hadm_id=1, subject_id=10),
            # patient 2: 2 admissions
            _result(hadm_id=2, subject_id=20),
            _result(hadm_id=3, subject_id=20),
            # patient 3: 4 admissions
            _result(hadm_id=4, subject_id=30),
            _result(hadm_id=5, subject_id=30),
            _result(hadm_id=6, subject_id=30),
            _result(hadm_id=7, subject_id=30),
            # patient 4: 3 admissions
            _result(hadm_id=8, subject_id=40),
            _result(hadm_id=9, subject_id=40),
            _result(hadm_id=10, subject_id=40),
            # failed extraction does not count
            _result(hadm_id=11, subject_id=50, success=False),
        ]
        c = compute_patient_concentration(results)
        assert c["distinct_patients"] == 4
        assert c["with_1"] == 1
        assert c["with_2"] == 1
        assert c["with_3"] == 1
        assert c["with_4_plus"] == 1
        assert c["total_admissions"] == 10

    def test_empty_results(self):
        c = compute_patient_concentration([])
        assert c["distinct_patients"] == 0
        assert c["total_admissions"] == 0


# ---------------------------------------------------------------------------
# MIMIC-III source-table fixtures (trimmed to columns the loaders read)
# ---------------------------------------------------------------------------


@pytest.fixture
def admissions_gz(tmp_path):
    csv = (
        "ROW_ID,SUBJECT_ID,HADM_ID,ADMITTIME,DISCHTIME,HOSPITAL_EXPIRE_FLAG\n"
        # hadm 100: LOS = 5 days, alive
        "1,10,100,2150-01-01 00:00:00,2150-01-06 00:00:00,0\n"
        # hadm 200: LOS = 1 day, died in hospital
        "2,20,200,2150-02-01 00:00:00,2150-02-02 00:00:00,1\n"
        # hadm 300: LOS = 10 days, alive
        "3,30,300,2150-03-01 00:00:00,2150-03-11 00:00:00,0\n"
        # hadm 400: LOS = 1 day, alive. Present in ADMISSIONS, absent from NOTEEVENTS.
        "4,40,400,2150-04-01 00:00:00,2150-04-02 00:00:00,0\n"
    )
    path = tmp_path / "ADMISSIONS.csv.gz"
    with gzip.open(path, "wt") as f:
        f.write(csv)
    return path


@pytest.fixture
def noteevents_gz(tmp_path):
    csv = (
        "ROW_ID,SUBJECT_ID,HADM_ID,CATEGORY,TEXT\n"
        # hadm 100: has Nursing notes -> clinical_narrative
        "1,10,100,Nursing,Some text\n"
        "2,10,100,Discharge summary,Brief Hospital Course:\n"
        # hadm 200: only Radiology -> imaging_only
        "3,20,200,Radiology,X-ray report\n"
        "4,20,200,Discharge summary,Brief Hospital Course:\n"
        # hadm 300: has Physician note -> clinical_narrative
        '5,30,300,"Physician ",Phys note\n'
        "6,30,300,Discharge summary,Brief Hospital Course:\n"
    )
    path = tmp_path / "NOTEEVENTS.csv.gz"
    with gzip.open(path, "wt") as f:
        f.write(csv)
    return path


@pytest.fixture
def icustays_gz(tmp_path):
    csv = (
        "ROW_ID,SUBJECT_ID,HADM_ID,ICUSTAY_ID,DBSOURCE\n"
        # hadm 100: carevue
        "1,10,100,1000,carevue\n"
        # hadm 200: metavision
        "2,20,200,2000,metavision\n"
        # hadm 300: two metavision stays (still classified metavision)
        "3,30,300,3000,metavision\n"
        "4,30,300,3001,metavision\n"
    )
    path = tmp_path / "ICUSTAYS.csv.gz"
    with gzip.open(path, "wt") as f:
        f.write(csv)
    return path


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


class TestLoaders:
    def test_load_admissions_filters_to_universe(self, admissions_gz):
        df = load_admissions(str(admissions_gz), {100, 200, 300})
        assert set(df.index) == {100, 200, 300}

    def test_load_admissions_los_uses_date_boundary_count(self, tmp_path):
        """LOS = DATE_DIFF('day', admit, disch): integer day-boundary count.

        A 50-hour stay spanning Jan 1 23:00 -> Jan 4 01:00 crosses 3 day
        boundaries (Jan 1->2, Jan 2->3, Jan 3->4) so the cohort filter
        ``LOS >= 3`` keeps it. Float-day arithmetic would give 50/24 ~= 2.08
        and wrongly drop the admission. This test pins the integer behavior.
        """
        csv = (
            "ROW_ID,SUBJECT_ID,HADM_ID,ADMITTIME,DISCHTIME,HOSPITAL_EXPIRE_FLAG\n"
            "1,1,500,2150-01-01 23:00:00,2150-01-04 01:00:00,0\n"
        )
        path = tmp_path / "ADMISSIONS.csv.gz"
        with gzip.open(path, "wt") as f:
            f.write(csv)
        df = load_admissions(str(path), {500})
        assert df.loc[500, "los_days"] == 3

    def test_load_admissions_warns_on_missing_hadm(self, admissions_gz):
        """Universe contains a hadm_id not in ADMISSIONS: warn, don't raise."""
        with pytest.warns(UserWarning, match="missing from"):
            load_admissions(str(admissions_gz), {100, 999})

    def test_load_note_category_availability(self, noteevents_gz):
        df = load_note_category_availability(str(noteevents_gz), {100, 200, 300})
        assert df.loc[100, "note_category"] == CLINICAL_NARRATIVE
        assert df.loc[200, "note_category"] == IMAGING_ONLY
        assert df.loc[300, "note_category"] == CLINICAL_NARRATIVE

    def test_load_icu_dbsource(self, icustays_gz):
        df = load_icu_dbsource(str(icustays_gz), {100, 200, 300})
        assert df.loc[100, "dbsource"] == DBSOURCE_CAREVUE
        assert df.loc[200, "dbsource"] == DBSOURCE_METAVISION
        assert df.loc[300, "dbsource"] == DBSOURCE_METAVISION

    def test_load_icu_dbsource_mixed_admission(self, tmp_path):
        """Admission with stays in both systems is bucketed as 'mixed'."""
        csv = (
            "ROW_ID,SUBJECT_ID,HADM_ID,ICUSTAY_ID,DBSOURCE\n"
            "1,1,777,1,carevue\n"
            "2,1,777,2,metavision\n"
        )
        path = tmp_path / "ICUSTAYS.csv.gz"
        with gzip.open(path, "wt") as f:
            f.write(csv)
        df = load_icu_dbsource(str(path), {777})
        assert df.loc[777, "dbsource"] == DBSOURCE_MIXED

    def test_load_icu_dbsource_handles_null_dbsource(self, tmp_path):
        """All-NULL DBSOURCE group must not propagate NaN into the bucketing."""
        csv = (
            "ROW_ID,SUBJECT_ID,HADM_ID,ICUSTAY_ID,DBSOURCE\n"
            # Empty DBSOURCE field -> NULL after CSV read.
            "1,1,888,1,\n"
        )
        path = tmp_path / "ICUSTAYS.csv.gz"
        with gzip.open(path, "wt") as f:
            f.write(csv)
        df = load_icu_dbsource(str(path), {888})
        # Neither all_metavision nor all_carevue is true: falls through to mixed.
        assert df.loc[888, "dbsource"] == DBSOURCE_MIXED


# ---------------------------------------------------------------------------
# compute_population_stats (end-to-end with fixture tables)
# ---------------------------------------------------------------------------


class TestComputePopulationStats:
    def test_assembles_all_four_blocks(self, admissions_gz, noteevents_gz, icustays_gz):
        """Smoke check: all four blocks are produced with the expected totals."""
        results = [
            _result(hadm_id=100, subject_id=10, category="narrative"),
            _result(hadm_id=200, subject_id=20, category="domain_organized"),
            _result(hadm_id=300, subject_id=30, category="narrative"),
            _result(hadm_id=999, subject_id=99, success=False),  # excluded
        ]
        stats = compute_population_stats(
            results,
            admissions_path=str(admissions_gz),
            noteevents_path=str(noteevents_gz),
            icustays_path=str(icustays_gz),
        )
        assert stats["note_category_availability"]["total"] == 3
        assert stats["los_distribution"]["total"] == 3
        assert stats["format_by_note_category"]["grand_total"] == 3
        assert stats["mortality"]["total"] == 3

    def test_los_percentiles_match_pandas_default(self, admissions_gz, noteevents_gz, icustays_gz):
        """LOS values [1, 5, 10] -> mean 16/3, median 5, lt_3_days = 1."""
        results = [
            _result(hadm_id=100, subject_id=10),  # LOS = 5
            _result(hadm_id=200, subject_id=20),  # LOS = 1
            _result(hadm_id=300, subject_id=30),  # LOS = 10
        ]
        stats = compute_population_stats(
            results,
            admissions_path=str(admissions_gz),
            noteevents_path=str(noteevents_gz),
            icustays_path=str(icustays_gz),
        )
        los = stats["los_distribution"]
        assert los["median"] == pytest.approx(5.0)
        assert los["mean"] == pytest.approx((1 + 5 + 10) / 3)
        assert los["lt_3_days"] == 1  # only hadm 200

    def test_note_category_breakdown_by_dbsource(self, admissions_gz, noteevents_gz, icustays_gz):
        """by_dbsource splits the note-category counts across CareVue/MetaVision."""
        results = [
            _result(hadm_id=100, subject_id=10),  # carevue, clinical_narrative
            _result(hadm_id=200, subject_id=20),  # metavision, imaging_only
            _result(hadm_id=300, subject_id=30),  # metavision, clinical_narrative
        ]
        stats = compute_population_stats(
            results,
            admissions_path=str(admissions_gz),
            noteevents_path=str(noteevents_gz),
            icustays_path=str(icustays_gz),
        )
        by_dbsource = stats["note_category_availability"]["by_dbsource"]
        assert by_dbsource is not None
        assert by_dbsource["carevue"][CLINICAL_NARRATIVE] == 1
        assert by_dbsource["metavision"][CLINICAL_NARRATIVE] == 1
        assert by_dbsource["metavision"][IMAGING_ONLY] == 1

    def test_excludes_standardized_template_from_crosstab(self, admissions_gz, noteevents_gz):
        """Templates are excluded from the cross-tab but counted elsewhere."""
        results = [
            _result(hadm_id=100, subject_id=10, category="narrative"),
            _result(hadm_id=200, subject_id=20, category="standardized_template"),
            _result(hadm_id=300, subject_id=30, category="domain_organized"),
        ]
        stats = compute_population_stats(
            results,
            admissions_path=str(admissions_gz),
            noteevents_path=str(noteevents_gz),
        )
        cross = stats["format_by_note_category"]
        assert "standardized_template" not in cross["cells"]
        assert cross["grand_total"] == 2  # narrative + domain_organized only
        # Note-category availability and mortality count templates too.
        assert stats["note_category_availability"]["total"] == 3
        assert stats["mortality"]["total"] == 3

    def test_self_check_fails_when_hadm_unknown_to_noteevents(self, admissions_gz, noteevents_gz):
        """Cross-tab self-check fires when an admission is in ADMISSIONS but not NOTEEVENTS.

        hadm 400 is in admissions_gz (so the LOS join succeeds and no missing-
        hadm warning fires) but absent from noteevents_gz, so its
        note_category is NaN and the cross-tab drops it. The structure-
        distribution counts it as narrative, so the self-check raises.
        """
        results = [
            _result(hadm_id=100, subject_id=10, category="narrative"),
            _result(hadm_id=200, subject_id=20, category="domain_organized"),
            _result(hadm_id=400, subject_id=40, category="narrative"),
        ]
        with pytest.raises(ValueError, match="Cross-tab row totals do not match"):
            compute_population_stats(
                results,
                admissions_path=str(admissions_gz),
                noteevents_path=str(noteevents_gz),
            )

    def test_empty_universe_yields_zero_stats(self, admissions_gz, noteevents_gz):
        """All-failed extractions: every block is zero, no error raised."""
        results = [
            _result(hadm_id=1, subject_id=1, success=False),
            _result(hadm_id=2, subject_id=2, success=False),
        ]
        stats = compute_population_stats(
            results,
            admissions_path=str(admissions_gz),
            noteevents_path=str(noteevents_gz),
        )
        assert stats["note_category_availability"]["total"] == 0
        assert stats["los_distribution"]["total"] == 0
        assert stats["format_by_note_category"]["grand_total"] == 0
        assert stats["mortality"]["total"] == 0


@pytest.fixture
def bucket_paths(tmp_path):
    """Build ADMISSIONS, NOTEEVENTS, and ICUSTAYS for the boundary-test universe.

    All seeded admissions are MetaVision and clinical_narrative, so the
    Total and MetaVision columns match and the CareVue column stays empty.
    """
    los_rows = [
        (1000, 0),
        (1001, 2),
        (1002, 3),
        (1003, 4),
        (1004, 5),
        (1005, 6),
        (1006, 7),
        (1007, 9),
        (1008, 10),
        (1009, 13),
        (1010, 14),
        (1011, 20),
        (1012, 21),
        (1013, 30),
    ]
    admissions_lines = [
        "ROW_ID,SUBJECT_ID,HADM_ID,ADMITTIME,DISCHTIME,HOSPITAL_EXPIRE_FLAG",
    ]
    notes_lines = ["ROW_ID,SUBJECT_ID,HADM_ID,CATEGORY,TEXT"]
    icu_lines = ["ROW_ID,SUBJECT_ID,HADM_ID,ICUSTAY_ID,DBSOURCE"]
    for i, (hadm, los) in enumerate(los_rows, start=1):
        admit = "2150-01-01 00:00:00"
        # Discharge = admit + LOS days at midnight (so DATE_DIFF == LOS).
        disch = f"2150-01-{1 + los:02d} 00:00:00"
        admissions_lines.append(f"{i},{hadm // 10},{hadm},{admit},{disch},0")
        notes_lines.append(f"{i},{hadm // 10},{hadm},Nursing,Some text")
        icu_lines.append(f"{i},{hadm // 10},{hadm},{hadm * 10},metavision")

    admissions_path = tmp_path / "ADMISSIONS.csv.gz"
    notes_path = tmp_path / "NOTEEVENTS.csv.gz"
    icu_path = tmp_path / "ICUSTAYS.csv.gz"
    with gzip.open(admissions_path, "wt") as f:
        f.write("\n".join(admissions_lines) + "\n")
    with gzip.open(notes_path, "wt") as f:
        f.write("\n".join(notes_lines) + "\n")
    with gzip.open(icu_path, "wt") as f:
        f.write("\n".join(icu_lines) + "\n")
    return admissions_path, notes_path, icu_path


class TestLengthStratifiedBreakdowns:
    @staticmethod
    def _bucket_total(row, column):
        counts = row[column]
        return counts["clinical_narrative"] + counts["imaging_only"]

    def test_breakdowns_absent_without_icustays(self, admissions_gz, noteevents_gz):
        """No icustays_path: by_los_bucket / by_bhc_length_bucket are None."""
        results = [_result(hadm_id=100, subject_id=10)]
        stats = compute_population_stats(
            results,
            admissions_path=str(admissions_gz),
            noteevents_path=str(noteevents_gz),
        )
        nca = stats["note_category_availability"]
        assert nca["by_los_bucket"] is None
        assert nca["by_bhc_length_bucket"] is None

    def test_los_buckets_have_expected_boundaries(self, bucket_paths):
        """Every LOS bucket gets exactly the two seeded boundary admissions."""
        admissions, noteevents, icustays = bucket_paths
        results = [_result(hadm_id=h, subject_id=h // 10) for h in range(1000, 1014)]
        stats = compute_population_stats(
            results,
            admissions_path=str(admissions),
            noteevents_path=str(noteevents),
            icustays_path=str(icustays),
        )
        breakdown = stats["note_category_availability"]["by_los_bucket"]
        assert breakdown is not None

        per_label = {row["label"]: row for row in breakdown["rows"]}
        for label in (
            "<3 days",
            "3 to 4 days",
            "5 to 6 days",
            "7 to 9 days",
            "10 to 13 days",
            "14 to 20 days",
            "21+ days",
        ):
            assert per_label[label]["metavision"]["clinical_narrative"] == 2, label
        assert sum(self._bucket_total(r, "carevue") for r in breakdown["rows"]) == 0

    def test_bhc_length_buckets_route_each_admission_into_its_bucket(self, bucket_paths):
        """Smoke check: every BHC-length bucket sees exactly two admissions."""
        admissions, noteevents, icustays = bucket_paths
        char_counts = {
            1000: 0,
            1001: 499,
            1002: 500,
            1003: 999,
            1004: 1000,
            1005: 1999,
            1006: 2000,
            1007: 2999,
            1008: 3000,
            1009: 4999,
            1010: 5000,
            1011: 9999,
            1012: 10_000,
            1013: 50_000,
        }
        results = [
            _result(hadm_id=h, subject_id=h // 10, bhc_char_count=char_counts[h])
            for h in range(1000, 1014)
        ]
        stats = compute_population_stats(
            results,
            admissions_path=str(admissions),
            noteevents_path=str(noteevents),
            icustays_path=str(icustays),
        )
        breakdown = stats["note_category_availability"]["by_bhc_length_bucket"]
        assert breakdown is not None
        per_label = {
            row["label"]: row["metavision"]["clinical_narrative"] for row in breakdown["rows"]
        }
        for label in (
            "<500 chars",
            "500 to 999",
            "1,000 to 1,999",
            "2,000 to 2,999",
            "3,000 to 4,999",
            "5,000 to 9,999",
            "10,000+ chars",
        ):
            assert per_label[label] == 2, label

    def test_bucket_column_totals_match_by_dbsource(
        self, admissions_gz, noteevents_gz, icustays_gz
    ):
        """Each system column reconciles with its by-ICU-system total.

        Also pins that hadm 100 (carevue, LOS 5) appears in the CareVue
        column of the 5-to-6 bucket, which would break if bucketing leaked
        across systems.
        """
        results = [
            _result(hadm_id=100, subject_id=10),
            _result(hadm_id=200, subject_id=20),
            _result(hadm_id=300, subject_id=30),
        ]
        stats = compute_population_stats(
            results,
            admissions_path=str(admissions_gz),
            noteevents_path=str(noteevents_gz),
            icustays_path=str(icustays_gz),
        )
        nca = stats["note_category_availability"]
        by_dbsource = nca["by_dbsource"]
        assert by_dbsource is not None
        per_system_total = {
            "carevue": (
                by_dbsource["carevue"]["clinical_narrative"]
                + by_dbsource["carevue"]["imaging_only"]
            ),
            "metavision": (
                by_dbsource["metavision"]["clinical_narrative"]
                + by_dbsource["metavision"]["imaging_only"]
            ),
            "total": nca["total"],
        }
        for breakdown_key in ("by_los_bucket", "by_bhc_length_bucket"):
            breakdown = nca[breakdown_key]
            assert breakdown is not None
            for column in ("total", "carevue", "metavision"):
                row_sum = sum(self._bucket_total(r, column) for r in breakdown["rows"])
                expected = per_system_total[column]
                assert row_sum + breakdown["excluded"][column] == expected, (
                    f"{breakdown_key}/{column}"
                )

        los_per_label = {row["label"]: row for row in nca["by_los_bucket"]["rows"]}
        assert self._bucket_total(los_per_label["5 to 6 days"], "carevue") == 1

    def test_excluded_counts_missing_los_per_system(self, tmp_path):
        """Admissions with NULL DISCHTIME are counted as excluded per-system."""
        csv = (
            "ROW_ID,SUBJECT_ID,HADM_ID,ADMITTIME,DISCHTIME,HOSPITAL_EXPIRE_FLAG\n"
            "1,100,1000,2150-01-01 00:00:00,2150-01-06 00:00:00,0\n"
            "2,100,1001,2150-01-01 00:00:00,,0\n"
            "3,100,1002,2150-01-01 00:00:00,,0\n"
        )
        path = tmp_path / "ADMISSIONS.csv.gz"
        with gzip.open(path, "wt") as f:
            f.write(csv)

        icu = (
            "ROW_ID,SUBJECT_ID,HADM_ID,ICUSTAY_ID,DBSOURCE\n"
            "1,100,1000,10000,metavision\n"
            "2,100,1001,10001,metavision\n"
            "3,100,1002,10002,carevue\n"
        )
        icu_path = tmp_path / "ICUSTAYS.csv.gz"
        with gzip.open(icu_path, "wt") as f:
            f.write(icu)

        notes = (
            "ROW_ID,SUBJECT_ID,HADM_ID,CATEGORY,TEXT\n"
            "1,100,1000,Nursing,Some text\n"
            "2,100,1001,Nursing,Some text\n"
            "3,100,1002,Nursing,Some text\n"
        )
        notes_path = tmp_path / "NOTEEVENTS.csv.gz"
        with gzip.open(notes_path, "wt") as f:
            f.write(notes)

        results = [
            _result(hadm_id=1000, subject_id=100),
            _result(hadm_id=1001, subject_id=100),
            _result(hadm_id=1002, subject_id=100),
        ]
        stats = compute_population_stats(
            results,
            admissions_path=str(path),
            noteevents_path=str(notes_path),
            icustays_path=str(icu_path),
        )
        breakdown = stats["note_category_availability"]["by_los_bucket"]
        assert breakdown is not None
        assert breakdown["excluded"]["metavision"] == 1
        assert breakdown["excluded"]["carevue"] == 1


# ---------------------------------------------------------------------------
# Per-admission note volume
# ---------------------------------------------------------------------------


@pytest.fixture
def noteevents_volume_gz(tmp_path):
    """Build a NOTEEVENTS fixture with deterministic note-text lengths.

    Per admission (after excluding the discharge summary):

    - hadm 100: two Nursing notes, 4 + 6 = 10 chars total -> clinical_narrative
    - hadm 200: one Radiology note, 5 chars total -> imaging_only
    - hadm 300: discharge summary only -> imaging_only, zero source content
    """
    csv = (
        "ROW_ID,SUBJECT_ID,HADM_ID,CATEGORY,TEXT\n"
        "1,10,100,Nursing,abcd\n"
        "2,10,100,Nursing,efghij\n"
        "3,10,100,Discharge summary,zzz\n"
        "4,20,200,Radiology,abcde\n"
        "5,20,200,Discharge summary,zz\n"
        "6,30,300,Discharge summary,zzzz\n"
    )
    path = tmp_path / "NOTEEVENTS.csv.gz"
    with gzip.open(path, "wt") as f:
        f.write(csv)
    return path


class TestLoadPerAdmissionNoteVolume:
    def test_excludes_discharge_summary_and_aggregates_per_admission(self, noteevents_volume_gz):
        df = load_per_admission_note_volume(str(noteevents_volume_gz), {100, 200, 300})
        assert df.loc[100, "note_count"] == 2
        assert df.loc[100, "note_chars"] == 10
        assert df.loc[200, "note_count"] == 1
        assert df.loc[200, "note_chars"] == 5
        # hadm 300 has only a discharge summary, so the loader returns no
        # row for it. Callers fill the missing index with zeros.
        assert 300 not in df.index


class TestNoteVolumeStats:
    @pytest.fixture
    def admissions_with_volume(self, tmp_path):
        csv = (
            "ROW_ID,SUBJECT_ID,HADM_ID,ADMITTIME,DISCHTIME,HOSPITAL_EXPIRE_FLAG\n"
            "1,10,100,2150-01-01 00:00:00,2150-01-06 00:00:00,0\n"
            "2,20,200,2150-02-01 00:00:00,2150-02-02 00:00:00,1\n"
            "3,30,300,2150-03-01 00:00:00,2150-03-11 00:00:00,0\n"
        )
        path = tmp_path / "ADMISSIONS.csv.gz"
        with gzip.open(path, "wt") as f:
            f.write(csv)
        return path

    @pytest.fixture
    def icustays_volume_gz(self, tmp_path):
        csv = (
            "ROW_ID,SUBJECT_ID,HADM_ID,ICUSTAY_ID,DBSOURCE\n"
            "1,10,100,1000,carevue\n"
            "2,20,200,2000,metavision\n"
            "3,30,300,3000,metavision\n"
        )
        path = tmp_path / "ICUSTAYS.csv.gz"
        with gzip.open(path, "wt") as f:
            f.write(csv)
        return path

    def test_distributions_split_by_system_and_class(
        self, admissions_with_volume, noteevents_volume_gz, icustays_volume_gz
    ):
        """clin_narr covers hadm 100 only. imaging_only covers 200 and 300."""
        results = [
            _result(hadm_id=100, subject_id=10),
            _result(hadm_id=200, subject_id=20),
            _result(hadm_id=300, subject_id=30),
        ]
        stats = compute_population_stats(
            results,
            admissions_path=str(admissions_with_volume),
            noteevents_path=str(noteevents_volume_gz),
            icustays_path=str(icustays_volume_gz),
        )
        volume = stats["note_volume"]
        cn_all = volume["note_count"]["total"][CLINICAL_NARRATIVE]
        assert cn_all["n"] == 1
        assert cn_all["min"] == 2
        assert cn_all["max"] == 2

        # CareVue clin_narr: hadm 100 (2 notes, 10 chars)
        chars_cv_cn = volume["note_chars"]["carevue"][CLINICAL_NARRATIVE]
        assert chars_cv_cn["n"] == 1
        assert chars_cv_cn["median"] == 10
        # CareVue has no imaging_only admissions in the fixture.
        assert volume["note_chars"]["carevue"][IMAGING_ONLY]["n"] == 0

        # MetaVision imaging_only: hadm 200 (1 note, 5 chars) and hadm 300
        # (discharge-summary only, fills as 0). Median = 2.5, min = 0.
        mv_io = volume["note_chars"]["metavision"][IMAGING_ONLY]
        assert mv_io["n"] == 2
        assert mv_io["min"] == 0
        assert mv_io["max"] == 5

    def test_n_reconciles_with_by_dbsource(
        self, admissions_with_volume, noteevents_volume_gz, icustays_volume_gz
    ):
        """clin_narr.n + imaging_only.n per system equals that system's by_dbsource total."""
        results = [
            _result(hadm_id=100, subject_id=10),
            _result(hadm_id=200, subject_id=20),
            _result(hadm_id=300, subject_id=30),
        ]
        stats = compute_population_stats(
            results,
            admissions_path=str(admissions_with_volume),
            noteevents_path=str(noteevents_volume_gz),
            icustays_path=str(icustays_volume_gz),
        )
        by_dbsource = stats["note_category_availability"]["by_dbsource"]
        assert by_dbsource is not None
        for system_key, dbsource in (("carevue", "carevue"), ("metavision", "metavision")):
            expected = (
                by_dbsource[dbsource][CLINICAL_NARRATIVE] + by_dbsource[dbsource][IMAGING_ONLY]
            )
            actual = (
                stats["note_volume"]["note_count"][system_key][CLINICAL_NARRATIVE]["n"]
                + stats["note_volume"]["note_count"][system_key][IMAGING_ONLY]["n"]
            )
            assert actual == expected, system_key

    def test_carevue_metavision_omitted_without_icustays(
        self, admissions_with_volume, noteevents_volume_gz
    ):
        """No ICUSTAYS path: only the All-systems system bucket is emitted."""
        results = [_result(hadm_id=100, subject_id=10)]
        stats = compute_population_stats(
            results,
            admissions_path=str(admissions_with_volume),
            noteevents_path=str(noteevents_volume_gz),
        )
        assert set(stats["note_volume"]["note_count"]) == {"total"}
