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
        # hadm 400: LOS = 1 day, alive. Used by the self-check test as an
        # admission present in ADMISSIONS but absent from NOTEEVENTS.
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


# ---------------------------------------------------------------------------
# MetaVision-only length-stratified breakdowns
# ---------------------------------------------------------------------------


@pytest.fixture
def bucket_paths(tmp_path):
    """ADMISSIONS, NOTEEVENTS, and ICUSTAYS for the boundary-test universe.

    Returns ``(admissions, noteevents, icustays)`` paths. The 14 hadm_ids span
    every LOS bucket boundary using the inclusive lower- and upper-bound LOS
    values for each bucket. Note-category is held constant (clinical_narrative
    via Nursing notes) so the LOS/length tests do not need to coordinate which
    side of the column each bucket sits on. All admissions are MetaVision.
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
        """Every LOS bucket gets exactly the two boundary admissions seeded."""
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
        # Each bucket has exactly two seeded admissions, all clinical_narrative.
        for label in (
            "<3 days",
            "3 to 4 days",
            "5 to 6 days",
            "7 to 9 days",
            "10 to 13 days",
            "14 to 20 days",
            "21+ days",
        ):
            assert per_label[label]["clinical_narrative"] == 2, label
            assert per_label[label]["imaging_only"] == 0, label

    def test_bhc_length_buckets_have_expected_boundaries(self, bucket_paths):
        """Each BHC-length bucket gets exactly the seeded boundary admissions."""
        admissions, noteevents, icustays = bucket_paths
        # BHC char counts chosen on each bucket boundary.
        char_counts = {
            1000: 0,  # <500
            1001: 499,  # <500
            1002: 500,  # 500 to 999
            1003: 999,  # 500 to 999
            1004: 1000,  # 1,000 to 1,999
            1005: 1999,  # 1,000 to 1,999
            1006: 2000,  # 2,000 to 2,999
            1007: 2999,  # 2,000 to 2,999
            1008: 3000,  # 3,000 to 4,999
            1009: 4999,  # 3,000 to 4,999
            1010: 5000,  # 5,000 to 9,999
            1011: 9999,  # 5,000 to 9,999
            1012: 10_000,  # 10,000+
            1013: 50_000,  # 10,000+
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
        per_label = {row["label"]: row for row in breakdown["rows"]}
        for label in (
            "<500 chars",
            "500 to 999",
            "1,000 to 1,999",
            "2,000 to 2,999",
            "3,000 to 4,999",
            "5,000 to 9,999",
            "10,000+ chars",
        ):
            assert per_label[label]["clinical_narrative"] == 2, label
            assert per_label[label]["imaging_only"] == 0, label

    def test_bucket_breakdowns_exclude_non_metavision(
        self, admissions_gz, noteevents_gz, icustays_gz
    ):
        """CareVue and mixed admissions are dropped before bucketing.

        Fixture has hadm 100 (carevue, LOS 5), 200 (metavision, LOS 1), 300
        (metavision, LOS 10). The MetaVision-only LOS buckets must therefore
        contain only the two metavision rows: 1 in '<3 days', 1 in
        '10 to 13 days'.
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
        breakdown = stats["note_category_availability"]["by_los_bucket"]
        assert breakdown is not None
        per_label = {row["label"]: row for row in breakdown["rows"]}
        totals = {
            label: row["clinical_narrative"] + row["imaging_only"]
            for label, row in per_label.items()
        }
        assert totals["<3 days"] == 1  # hadm 200
        assert totals["10 to 13 days"] == 1  # hadm 300
        # CareVue hadm 100 (LOS 5) does not show up in the 5 to 6 days bucket.
        assert totals["5 to 6 days"] == 0
        # Total over all buckets equals MetaVision admission count.
        assert sum(totals.values()) == 2

    def test_bucket_row_totals_equal_metavision_total(
        self, admissions_gz, noteevents_gz, icustays_gz
    ):
        """Row counts in the LOS breakdown sum to the MetaVision row total.

        Strongest self-check from the handoff: each new sub-block must sum to
        the same MetaVision count reported in the by-ICU-system block.
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
        metavision_total = (
            by_dbsource["metavision"]["clinical_narrative"]
            + by_dbsource["metavision"]["imaging_only"]
        )
        for breakdown_key in ("by_los_bucket", "by_bhc_length_bucket"):
            breakdown = nca[breakdown_key]
            assert breakdown is not None
            row_sum = sum(r["clinical_narrative"] + r["imaging_only"] for r in breakdown["rows"])
            assert row_sum + breakdown["excluded"] == metavision_total, breakdown_key

    def test_excluded_counts_missing_los(self, tmp_path):
        """Admissions with NULL ADMITTIME or DISCHTIME are excluded and counted."""
        csv = (
            "ROW_ID,SUBJECT_ID,HADM_ID,ADMITTIME,DISCHTIME,HOSPITAL_EXPIRE_FLAG\n"
            # hadm 1000: valid LOS = 5
            "1,100,1000,2150-01-01 00:00:00,2150-01-06 00:00:00,0\n"
            # hadm 1001: missing DISCHTIME -> NaN LOS
            "2,100,1001,2150-01-01 00:00:00,,0\n"
        )
        path = tmp_path / "ADMISSIONS.csv.gz"
        with gzip.open(path, "wt") as f:
            f.write(csv)

        # ICUSTAYS for both hadm_ids so they are both metavision.
        icu = (
            "ROW_ID,SUBJECT_ID,HADM_ID,ICUSTAY_ID,DBSOURCE\n"
            "1,100,1000,10000,metavision\n"
            "2,100,1001,10001,metavision\n"
        )
        icu_path = tmp_path / "ICUSTAYS.csv.gz"
        with gzip.open(icu_path, "wt") as f:
            f.write(icu)

        # NOTEEVENTS for both hadm_ids.
        notes = (
            "ROW_ID,SUBJECT_ID,HADM_ID,CATEGORY,TEXT\n"
            "1,100,1000,Nursing,Some text\n"
            "2,100,1001,Nursing,Some text\n"
        )
        notes_path = tmp_path / "NOTEEVENTS.csv.gz"
        with gzip.open(notes_path, "wt") as f:
            f.write(notes)

        results = [
            _result(hadm_id=1000, subject_id=100),
            _result(hadm_id=1001, subject_id=100),
        ]
        stats = compute_population_stats(
            results,
            admissions_path=str(path),
            noteevents_path=str(notes_path),
            icustays_path=str(icu_path),
        )
        breakdown = stats["note_category_availability"]["by_los_bucket"]
        assert breakdown is not None
        # hadm 1000 lands in '5 to 6 days'. hadm 1001 is excluded.
        per_label = {row["label"]: row for row in breakdown["rows"]}
        assert per_label["5 to 6 days"]["clinical_narrative"] == 1
        assert breakdown["excluded"] == 1
