"""Tests for the shared internal utilities module."""

import gzip

import pytest

from mimic_bhc._util import detect_mimic_format, is_mimic3


class TestDetectMimicFormat:
    def test_mimic3_plain_csv(self, tmp_path):
        csv = tmp_path / "notes.csv"
        csv.write_text(
            'ROW_ID,SUBJECT_ID,HADM_ID,CATEGORY,TEXT\n1,10,100,"Discharge summary","some text"\n'
        )
        assert detect_mimic_format(str(csv)) == "mimic3"

    def test_mimic3_gzipped_csv(self, tmp_path):
        gz = tmp_path / "notes.csv.gz"
        content = (
            'ROW_ID,SUBJECT_ID,HADM_ID,CATEGORY,TEXT\n1,10,100,"Discharge summary","some text"\n'
        )
        with gzip.open(gz, "wt") as f:
            f.write(content)
        assert detect_mimic_format(str(gz)) == "mimic3"

    def test_mimic4_plain_csv(self, tmp_path):
        csv = tmp_path / "notes.csv"
        csv.write_text(
            'note_id,subject_id,hadm_id,charttime,text\n1,10,100,2150-01-01,"some text"\n'
        )
        assert detect_mimic_format(str(csv)) == "mimic4"

    def test_mimic4_gzipped_csv(self, tmp_path):
        gz = tmp_path / "notes.csv.gz"
        content = 'note_id,subject_id,hadm_id,charttime,text\n1,10,100,2150-01-01,"some text"\n'
        with gzip.open(gz, "wt") as f:
            f.write(content)
        assert detect_mimic_format(str(gz)) == "mimic4"

    def test_parquet_raises(self, tmp_path):
        parquet = tmp_path / "notes.parquet"
        parquet.write_bytes(b"")
        with pytest.raises(ValueError, match="Cannot auto-detect"):
            detect_mimic_format(str(parquet))


class TestIsMimic3:
    def test_explicit_mimic3(self):
        assert is_mimic3("/fake/path.csv", "mimic3") is True

    def test_explicit_mimic4(self):
        assert is_mimic3("/fake/path.csv", "mimic4") is False

    def test_auto_detect_mimic3(self, tmp_path):
        csv = tmp_path / "notes.csv"
        csv.write_text("ROW_ID,SUBJECT_ID,HADM_ID,CATEGORY,TEXT\n")
        assert is_mimic3(str(csv), "auto") is True

    def test_auto_detect_mimic4(self, tmp_path):
        csv = tmp_path / "notes.csv"
        csv.write_text("note_id,subject_id,hadm_id,charttime,text\n")
        assert is_mimic3(str(csv), "auto") is False


class TestLoadDataframe:
    def test_csv_columns_normalized_to_lowercase(self, tmp_path):
        import duckdb

        from mimic_bhc._util import load_dataframe

        csv = tmp_path / "notes.csv"
        csv.write_text(
            'ROW_ID,SUBJECT_ID,HADM_ID,CATEGORY,TEXT\n1,10,100,"Discharge summary","some text"\n'
        )
        with duckdb.connect() as conn:
            df = load_dataframe(conn, str(csv))
        assert list(df.columns) == ["row_id", "subject_id", "hadm_id", "category", "text"]

    def test_csv_content_loaded(self, tmp_path):
        import duckdb

        from mimic_bhc._util import load_dataframe

        csv = tmp_path / "notes.csv"
        csv.write_text(
            'note_id,subject_id,hadm_id,text\n1,10,100,"hello world"\n2,20,200,"second note"\n'
        )
        with duckdb.connect() as conn:
            df = load_dataframe(conn, str(csv))
        assert len(df) == 2
        assert df.iloc[0]["text"] == "hello world"
