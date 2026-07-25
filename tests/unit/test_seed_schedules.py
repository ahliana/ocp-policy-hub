"""Tests for `python -m src.storage.seed_schedules` (WP-11).

Seeds only "Monthly full scan" (domains=all, channels crawl+law_apis+
transposition, monthly:1:06:00, no ceiling) - the one recurring server-crontab
job that maps onto ScanManager. The weekly news sweep is explicitly out of
scope: news runs outside ScanManager entirely (see
src/orchestration/scan_manager.py's _domain_channel - "news" always yields 0
domains), so there is nothing for a schedules row to fire.

Idempotent: running the seed twice, or against a table that already has any
row, adds nothing the second time.
"""

from src.storage.schedules import SchedulesStore
from src.storage.seed_schedules import seed


class TestSeed:
    def test_seeds_monthly_full_scan(self, tmp_path):
        store = SchedulesStore(data_dir=str(tmp_path))
        result = seed(data_dir=str(tmp_path))

        rows = store.list()
        assert len(rows) == 1
        row = rows[0]
        assert row["name"] == "Monthly full scan"
        assert row["domains"] == "all"
        assert set(row["channels"]) == {"crawl", "law_apis", "transposition"}
        assert row["cadence"] == "monthly:1:06:00"
        assert row["monthly_ceiling_usd"] is None
        assert row["deep"] is False
        assert result["seeded"] == 1

    def test_idempotent_on_rerun(self, tmp_path):
        seed(data_dir=str(tmp_path))
        result = seed(data_dir=str(tmp_path))

        store = SchedulesStore(data_dir=str(tmp_path))
        assert len(store.list()) == 1
        assert result["seeded"] == 0
        assert result["skipped_reason"] == "schedules table is not empty"

    def test_does_not_seed_when_any_row_already_exists(self, tmp_path):
        store = SchedulesStore(data_dir=str(tmp_path))
        store.create(
            name="Some other schedule", domains="quick", channels=["crawl"],
            deep=False, topic=None, cadence="weekly:0:06:00",
        )
        result = seed(data_dir=str(tmp_path))

        assert len(store.list()) == 1
        assert result["seeded"] == 0

    def test_does_not_seed_a_news_schedule(self, tmp_path):
        """Out of scope by design: news has no ScanManager path (see the
        module docstring), so it must never appear as a seeded row."""
        store = SchedulesStore(data_dir=str(tmp_path))
        seed(data_dir=str(tmp_path))

        names = {row["name"] for row in store.list()}
        assert "Weekly signals sweep" not in names
        assert not any("news" in (row["channels"] or []) for row in store.list())
