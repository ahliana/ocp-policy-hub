"""Tests for src/storage/backfill_lifecycle.py (WP-2b).

Fixture rows are inserted directly with raw sqlite3 (bypassing the normal
insert helpers) so the lifecycle_stage COLUMN and the `raw` JSON can be put
into the exact mismatched states this backfill exists to fix: a NULL column
with a valid stage in raw, a NULL column with an invalid stage in raw, and a
NULL column with the field missing from raw entirely.
"""

import json
import sqlite3

import pytest

from src.storage import db as storage_db
from src.storage.backfill_lifecycle import backfill_lifecycle


def _insert_row(conn, url, column_stage, raw_extra=None):
    raw = {"url": url, "policy_name": f"Policy {url}", "jurisdiction": "Testland"}
    if raw_extra:
        raw.update(raw_extra)
    conn.execute(
        """
        INSERT INTO policies (url, policy_name, jurisdiction, lifecycle_stage, raw)
        VALUES (?, ?, ?, ?, ?)
        """,
        (url, raw["policy_name"], raw["jurisdiction"], column_stage, json.dumps(raw)),
    )


@pytest.fixture
def fixture_db(tmp_path):
    """A db with: a NULL-column row whose raw carries a valid stage, one
    whose raw carries an invalid stage, one whose raw is missing the field,
    and one already-populated row (column non-NULL) that must stay untouched
    even though its raw is deliberately inconsistent with its column."""
    conn = storage_db.connect(tmp_path)
    _insert_row(conn, "https://a.gov/valid", None, {"lifecycle_stage": "enacted"})
    _insert_row(conn, "https://b.gov/invalid", None, {"lifecycle_stage": "made_up_stage"})
    _insert_row(conn, "https://c.gov/missing", None, {})
    _insert_row(conn, "https://d.gov/already-set", "passed", {"lifecycle_stage": "amended"})
    conn.commit()
    conn.close()
    return tmp_path


def _row(data_dir, url):
    conn = sqlite3.connect(data_dir / storage_db.DB_FILENAME)
    row = conn.execute(
        "SELECT lifecycle_stage, raw FROM policies WHERE url = ?", (url,)
    ).fetchone()
    conn.close()
    return row[0], json.loads(row[1])


class TestBackfillLifecycle:
    def test_derives_valid_stage_from_raw(self, fixture_db):
        backfill_lifecycle(fixture_db)
        column, raw = _row(fixture_db, "https://a.gov/valid")
        assert column == "enacted"
        assert raw["lifecycle_stage"] == "enacted"

    def test_invalid_raw_stage_falls_back_to_unknown_in_both_places(self, fixture_db):
        backfill_lifecycle(fixture_db)
        column, raw = _row(fixture_db, "https://b.gov/invalid")
        assert column == "unknown"
        assert raw["lifecycle_stage"] == "unknown"

    def test_missing_raw_field_falls_back_to_unknown_and_is_added_to_raw(self, fixture_db):
        backfill_lifecycle(fixture_db)
        column, raw = _row(fixture_db, "https://c.gov/missing")
        assert column == "unknown"
        assert raw["lifecycle_stage"] == "unknown"

    def test_already_populated_row_is_left_untouched(self, fixture_db):
        backfill_lifecycle(fixture_db)
        column, raw = _row(fixture_db, "https://d.gov/already-set")
        assert column == "passed"
        assert raw["lifecycle_stage"] == "amended"

    def test_summary_counts_examined_and_updated_rows(self, fixture_db):
        summary = backfill_lifecycle(fixture_db)
        assert summary["examined"] == 3
        assert summary["updated"] == 3

    def test_summary_reports_resulting_lifecycle_stage_counts(self, fixture_db):
        summary = backfill_lifecycle(fixture_db)
        assert summary["counts"] == {"enacted": 1, "unknown": 2, "passed": 1}

    def test_dry_run_writes_nothing(self, fixture_db):
        summary = backfill_lifecycle(fixture_db, dry_run=True)

        assert summary["examined"] == 3
        assert summary["updated"] == 0

        for url in ("https://a.gov/valid", "https://b.gov/invalid", "https://c.gov/missing"):
            column, raw = _row(fixture_db, url)
            assert column is None
            assert "lifecycle_stage" not in raw or raw["lifecycle_stage"] in (
                "made_up_stage", "enacted",
            )

    def test_second_run_is_idempotent(self, fixture_db):
        first = backfill_lifecycle(fixture_db)
        assert first["updated"] == 3

        second = backfill_lifecycle(fixture_db)
        assert second["examined"] == 0
        assert second["updated"] == 0
        # Resulting counts unchanged by the idempotent second pass.
        assert second["counts"] == first["counts"]
