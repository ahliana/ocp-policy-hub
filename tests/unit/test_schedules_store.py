"""Tests for SchedulesStore and compute_next_run (WP-11).

compute_next_run is a pure function: given a cadence string and "now", it
returns the next UTC datetime the schedule should fire, strictly after
"now". Cadence formats: "weekly:<dow>:<HH:MM>" (dow 0=Monday..6=Sunday,
matching datetime.weekday()) and "monthly:<dom>:<HH:MM>" (dom 1-31,
clamped to the last day of a shorter month).

The store itself is a straightforward SQLite CRUD table (schedules), plus
month_spend() which sums completed scans' cost_usd for a scope within the
current UTC calendar month - the input to the monthly-ceiling pause check.
"""

from datetime import datetime

import pytest

from src.storage.schedules import InvalidCadenceError, SchedulesStore, compute_next_run


# ---------------------------------------------------------------------------
# compute_next_run
# ---------------------------------------------------------------------------

class TestComputeNextRunWeekly:
    def test_later_this_week_before_target_time_today(self):
        # 2026-01-05 is a Monday (weekday()==0).
        now = datetime(2026, 1, 5, 5, 0)
        result = compute_next_run("weekly:0:06:30", now)
        assert result == datetime(2026, 1, 5, 6, 30)

    def test_target_time_already_passed_today_rolls_to_next_week(self):
        now = datetime(2026, 1, 5, 7, 0)
        result = compute_next_run("weekly:0:06:30", now)
        assert result == datetime(2026, 1, 12, 6, 30)

    def test_later_in_the_week(self):
        # Monday -> Thursday (dow 3).
        now = datetime(2026, 1, 5, 12, 0)
        result = compute_next_run("weekly:3:06:30", now)
        assert result == datetime(2026, 1, 8, 6, 30)

    def test_earlier_in_the_week_rolls_to_next_week(self):
        # Thursday -> Monday (dow 0), which already passed this week.
        now = datetime(2026, 1, 8, 12, 0)
        result = compute_next_run("weekly:0:06:30", now)
        assert result == datetime(2026, 1, 12, 6, 30)


class TestComputeNextRunMonthly:
    def test_before_target_day_this_month(self):
        now = datetime(2026, 1, 1, 0, 0)
        result = compute_next_run("monthly:15:06:00", now)
        assert result == datetime(2026, 1, 15, 6, 0)

    def test_after_target_day_this_month_rolls_to_next_month(self):
        now = datetime(2026, 1, 20, 0, 0)
        result = compute_next_run("monthly:15:06:00", now)
        assert result == datetime(2026, 2, 15, 6, 0)

    def test_target_time_already_passed_today_rolls_to_next_month(self):
        now = datetime(2026, 1, 15, 7, 0)
        result = compute_next_run("monthly:15:06:00", now)
        assert result == datetime(2026, 2, 15, 6, 0)

    def test_year_rollover(self):
        now = datetime(2026, 12, 20, 0, 0)
        result = compute_next_run("monthly:1:06:00", now)
        assert result == datetime(2027, 1, 1, 6, 0)

    def test_day_clamped_to_last_day_of_shorter_month(self):
        # dom=31 in February (2026 is not a leap year -> 28 days).
        now = datetime(2026, 2, 1, 0, 0)
        result = compute_next_run("monthly:31:06:00", now)
        assert result == datetime(2026, 2, 28, 6, 0)

    def test_leap_year_february(self):
        now = datetime(2028, 2, 1, 0, 0)
        result = compute_next_run("monthly:31:06:00", now)
        assert result == datetime(2028, 2, 29, 6, 0)


class TestComputeNextRunInvalid:
    @pytest.mark.parametrize("cadence", [
        "",
        "daily:06:00",
        "weekly:7:06:00",
        "weekly:0:24:00",
        "weekly:0:06:60",
        "monthly:0:06:00",
        "monthly:32:06:00",
        "weekly:0:06",
        "monthly:06:00",
        "yearly:1:1:06:00",
    ])
    def test_invalid_cadence_raises(self, cadence):
        with pytest.raises(InvalidCadenceError):
            compute_next_run(cadence, datetime(2026, 1, 1))


# ---------------------------------------------------------------------------
# SchedulesStore CRUD
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    return SchedulesStore(data_dir=str(tmp_path))


class TestCreate:
    def test_creates_row_with_defaults(self, store):
        row = store.create(
            name="Monthly full scan", domains="all",
            channels=["crawl", "law_apis"], deep=False, topic=None,
            cadence="monthly:1:06:00",
        )
        assert row["id"]
        assert row["name"] == "Monthly full scan"
        assert row["domains"] == "all"
        assert row["channels"] == ["crawl", "law_apis"]
        assert row["deep"] is False
        assert row["topic"] is None
        assert row["cadence"] == "monthly:1:06:00"
        assert row["enabled"] is True
        assert row["monthly_ceiling_usd"] is None
        assert row["paused_reason"] is None
        assert row["last_run_at"] is None
        assert row["last_scan_id"] is None
        assert row["next_run_at"] is not None
        assert row["created_at"] is not None

    def test_created_rows_have_unique_ids(self, store):
        a = store.create(name="A", domains="quick", channels=["crawl"], deep=False,
                          topic=None, cadence="weekly:0:06:00")
        b = store.create(name="B", domains="quick", channels=["crawl"], deep=False,
                          topic=None, cadence="weekly:0:06:00")
        assert a["id"] != b["id"]

    def test_invalid_cadence_raises(self, store):
        with pytest.raises(InvalidCadenceError):
            store.create(name="Bad", domains="quick", channels=["crawl"], deep=False,
                         topic=None, cadence="bogus")

    def test_ceiling_stored(self, store):
        row = store.create(
            name="Capped", domains="quick", channels=["crawl"], deep=True,
            topic=None, cadence="weekly:0:06:00", monthly_ceiling_usd=25.0,
        )
        assert row["monthly_ceiling_usd"] == 25.0


class TestGetAndList:
    def test_get_missing_returns_none(self, store):
        assert store.get("nonexistent") is None

    def test_get_returns_created_row(self, store):
        created = store.create(name="A", domains="quick", channels=["crawl"],
                                deep=False, topic=None, cadence="weekly:0:06:00")
        fetched = store.get(created["id"])
        assert fetched == created

    def test_list_empty(self, store):
        assert store.list() == []

    def test_list_returns_all(self, store):
        store.create(name="A", domains="quick", channels=["crawl"], deep=False,
                      topic=None, cadence="weekly:0:06:00")
        store.create(name="B", domains="eu", channels=["crawl"], deep=False,
                      topic=None, cadence="monthly:1:06:00")
        names = {row["name"] for row in store.list()}
        assert names == {"A", "B"}


class TestUpdate:
    def test_update_missing_returns_none(self, store):
        assert store.update("nonexistent", name="X") is None

    def test_partial_update_changes_only_given_fields(self, store):
        created = store.create(name="A", domains="quick", channels=["crawl"],
                                deep=False, topic=None, cadence="weekly:0:06:00")
        updated = store.update(created["id"], name="Renamed")
        assert updated["name"] == "Renamed"
        assert updated["domains"] == "quick"
        assert updated["cadence"] == "weekly:0:06:00"

    def test_toggle_enabled(self, store):
        created = store.create(name="A", domains="quick", channels=["crawl"],
                                deep=False, topic=None, cadence="weekly:0:06:00")
        updated = store.update(created["id"], enabled=False)
        assert updated["enabled"] is False

    def test_changing_cadence_recomputes_next_run_at(self, store):
        created = store.create(name="A", domains="quick", channels=["crawl"],
                                deep=False, topic=None, cadence="weekly:0:06:00")
        updated = store.update(created["id"], cadence="monthly:1:06:00")
        assert updated["cadence"] == "monthly:1:06:00"
        assert updated["next_run_at"] != created["next_run_at"]

    def test_update_invalid_cadence_raises(self, store):
        created = store.create(name="A", domains="quick", channels=["crawl"],
                                deep=False, topic=None, cadence="weekly:0:06:00")
        with pytest.raises(InvalidCadenceError):
            store.update(created["id"], cadence="bogus")

    def test_clear_ceiling(self, store):
        created = store.create(name="A", domains="quick", channels=["crawl"], deep=False,
                                topic=None, cadence="weekly:0:06:00", monthly_ceiling_usd=10.0)
        updated = store.update(created["id"], monthly_ceiling_usd=None)
        assert updated["monthly_ceiling_usd"] is None


class TestDelete:
    def test_delete_missing_returns_false(self, store):
        assert store.delete("nonexistent") is False

    def test_delete_removes_row(self, store):
        created = store.create(name="A", domains="quick", channels=["crawl"],
                                deep=False, topic=None, cadence="weekly:0:06:00")
        assert store.delete(created["id"]) is True
        assert store.get(created["id"]) is None


class TestMarkRan:
    def test_mark_ran_updates_fields(self, store):
        created = store.create(name="A", domains="quick", channels=["crawl"],
                                deep=False, topic=None, cadence="weekly:0:06:00")
        ran_at = datetime(2026, 1, 5, 6, 30)
        next_run = datetime(2026, 1, 12, 6, 30)
        updated = store.mark_ran(created["id"], scan_id="abc123", ran_at=ran_at, next_run_at=next_run)
        assert updated["last_scan_id"] == "abc123"
        assert updated["last_run_at"] == "2026-01-05T06:30:00"
        assert updated["next_run_at"] == "2026-01-12T06:30:00"


class TestMonthSpend:
    def test_no_scans_is_zero(self, store):
        assert store.month_spend("quick", datetime(2026, 1, 15)) == 0.0

    def test_sums_completed_scans_in_current_month(self, store):
        conn = store._conn
        conn.execute(
            "INSERT INTO scans (scan_id, domain_group, status, completed_at, cost_usd) "
            "VALUES (?, ?, 'completed', ?, ?)",
            ("s1", "quick", "2026-01-05T00:00:00", 3.0),
        )
        conn.execute(
            "INSERT INTO scans (scan_id, domain_group, status, completed_at, cost_usd) "
            "VALUES (?, ?, 'completed', ?, ?)",
            ("s2", "quick", "2026-01-20T00:00:00", 4.5),
        )
        conn.commit()
        assert store.month_spend("quick", datetime(2026, 1, 31)) == pytest.approx(7.5)

    def test_excludes_other_months(self, store):
        conn = store._conn
        conn.execute(
            "INSERT INTO scans (scan_id, domain_group, status, completed_at, cost_usd) "
            "VALUES (?, ?, 'completed', ?, ?)",
            ("s1", "quick", "2025-12-31T23:59:00", 100.0),
        )
        conn.commit()
        assert store.month_spend("quick", datetime(2026, 1, 1)) == 0.0

    def test_excludes_other_scopes(self, store):
        conn = store._conn
        conn.execute(
            "INSERT INTO scans (scan_id, domain_group, status, completed_at, cost_usd) "
            "VALUES (?, ?, 'completed', ?, ?)",
            ("s1", "eu", "2026-01-05T00:00:00", 100.0),
        )
        conn.commit()
        assert store.month_spend("quick", datetime(2026, 1, 31)) == 0.0

    def test_excludes_non_completed_scans(self, store):
        conn = store._conn
        conn.execute(
            "INSERT INTO scans (scan_id, domain_group, status, completed_at, cost_usd) "
            "VALUES (?, ?, 'failed', ?, ?)",
            ("s1", "quick", "2026-01-05T00:00:00", 100.0),
        )
        conn.commit()
        assert store.month_spend("quick", datetime(2026, 1, 31)) == 0.0
