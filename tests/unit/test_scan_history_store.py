"""Tests for ScanHistoryStore (WP-5) — the ``scans`` table.

record_start/record_completion write and update one row per scan; list()
filters and paginates newest-first; stats() aggregates actuals for the
cost-projection blend rule (WP-7).
"""

from datetime import datetime, timedelta

import pytest

from src.storage.scan_history import ScanHistoryStore


@pytest.fixture
def store(tmp_path):
    return ScanHistoryStore(data_dir=str(tmp_path))


class TestRecordStart:
    def test_creates_running_row(self, store):
        store.record_start(
            scan_id="s1", domain_group="quick", mode="standard",
            channels=["crawl"], started_at=datetime(2026, 1, 1),
        )
        rows = store.list()
        assert len(rows) == 1
        row = rows[0]
        assert row["scan_id"] == "s1"
        assert row["domain_group"] == "quick"
        assert row["mode"] == "standard"
        assert row["channels"] == ["crawl"]
        assert row["status"] == "running"
        assert row["started_at"] == "2026-01-01T00:00:00"
        assert row["completed_at"] is None

    def test_channels_round_trip_as_list(self, store):
        store.record_start(
            scan_id="s1", domain_group="quick", mode="standard",
            channels=["crawl", "law_apis"], started_at=datetime(2026, 1, 1),
        )
        assert store.list()[0]["channels"] == ["crawl", "law_apis"]

    def test_duplicate_scan_id_is_ignored(self, store):
        store.record_start(
            scan_id="s1", domain_group="quick", mode="standard",
            channels=["crawl"], started_at=datetime(2026, 1, 1),
        )
        store.record_start(
            scan_id="s1", domain_group="different", mode="deep",
            channels=["law_apis"], started_at=datetime(2026, 1, 2),
        )
        rows = store.list()
        assert len(rows) == 1
        assert rows[0]["domain_group"] == "quick"


class TestRecordCompletion:
    def test_completed_updates_row(self, store):
        store.record_start(
            scan_id="s1", domain_group="quick", mode="standard",
            channels=["crawl"], started_at=datetime(2026, 1, 1),
        )
        updated = store.record_completion(
            scan_id="s1", status="completed", completed_at=datetime(2026, 1, 1, 0, 5),
            domains_scanned=10, policies_found=3, cost_usd=1.23,
            input_tokens=1000, output_tokens=200,
        )
        assert updated is True

        row = store.list()[0]
        assert row["status"] == "completed"
        assert row["completed_at"] == "2026-01-01T00:05:00"
        assert row["domains_scanned"] == 10
        assert row["policies_found"] == 3
        assert row["cost_usd"] == 1.23
        assert row["input_tokens"] == 1000
        assert row["output_tokens"] == 200

    def test_failed_status(self, store):
        store.record_start(
            scan_id="s1", domain_group="quick", mode="standard",
            channels=["crawl"], started_at=datetime(2026, 1, 1),
        )
        store.record_completion(
            scan_id="s1", status="failed", completed_at=datetime(2026, 1, 1, 0, 2),
            domains_scanned=2, policies_found=0, cost_usd=0.0,
        )
        assert store.list()[0]["status"] == "failed"

    def test_cancelled_status(self, store):
        store.record_start(
            scan_id="s1", domain_group="quick", mode="standard",
            channels=["crawl"], started_at=datetime(2026, 1, 1),
        )
        store.record_completion(
            scan_id="s1", status="cancelled", completed_at=datetime(2026, 1, 1, 0, 1),
        )
        assert store.list()[0]["status"] == "cancelled"

    def test_missing_scan_id_returns_false(self, store):
        updated = store.record_completion(
            scan_id="nonexistent", status="completed", completed_at=datetime(2026, 1, 1),
        )
        assert updated is False


class TestList:
    def _seed(self, store):
        base = datetime(2026, 1, 1)
        store.record_start(
            scan_id="old", domain_group="quick", mode="standard",
            channels=["crawl"], started_at=base,
        )
        store.record_completion(scan_id="old", status="completed", completed_at=base)
        store.record_start(
            scan_id="new", domain_group="eu", mode="deep",
            channels=["law_apis"], started_at=base + timedelta(days=1),
        )
        store.record_completion(
            scan_id="new", status="failed", completed_at=base + timedelta(days=1),
        )

    def test_newest_first(self, store):
        self._seed(store)
        ids = [r["scan_id"] for r in store.list()]
        assert ids == ["new", "old"]

    def test_filter_by_domain_group(self, store):
        self._seed(store)
        rows = store.list(domain_group="eu")
        assert [r["scan_id"] for r in rows] == ["new"]

    def test_filter_by_status(self, store):
        self._seed(store)
        rows = store.list(status="completed")
        assert [r["scan_id"] for r in rows] == ["old"]

    def test_limit_and_offset(self, store):
        self._seed(store)
        rows = store.list(limit=1, offset=1)
        assert [r["scan_id"] for r in rows] == ["old"]

    def test_empty_store_returns_empty_list(self, store):
        assert store.list() == []


class TestCount:
    def test_count_matches_filters(self, store):
        base = datetime(2026, 1, 1)
        store.record_start(
            scan_id="s1", domain_group="quick", mode="standard",
            channels=["crawl"], started_at=base,
        )
        store.record_start(
            scan_id="s2", domain_group="quick", mode="standard",
            channels=["crawl"], started_at=base,
        )
        store.record_start(
            scan_id="s3", domain_group="eu", mode="standard",
            channels=["crawl"], started_at=base,
        )
        assert store.count() == 3
        assert store.count(domain_group="quick") == 2
        assert store.count(domain_group="eu") == 1

    def test_count_ignores_limit(self, store):
        base = datetime(2026, 1, 1)
        for i in range(5):
            store.record_start(
                scan_id=f"s{i}", domain_group="quick", mode="standard",
                channels=["crawl"], started_at=base,
            )
        assert store.count(domain_group="quick") == 5
        assert len(store.list(domain_group="quick", limit=2)) == 2


class TestStats:
    def test_no_runs_returns_none_fields(self, store):
        stats = store.stats("quick")
        assert stats == {
            "runs": 0, "mean_cost_usd": None, "last_cost_usd": None, "mean_policies": None,
        }

    def test_only_completed_runs_count(self, store):
        base = datetime(2026, 1, 1)
        store.record_start(
            scan_id="s1", domain_group="quick", mode="standard",
            channels=["crawl"], started_at=base,
        )
        store.record_completion(
            scan_id="s1", status="completed", completed_at=base,
            cost_usd=2.0, policies_found=4,
        )
        store.record_start(
            scan_id="s2", domain_group="quick", mode="standard",
            channels=["crawl"], started_at=base,
        )
        store.record_completion(
            scan_id="s2", status="failed", completed_at=base,
            cost_usd=99.0, policies_found=99,
        )

        stats = store.stats("quick")
        assert stats["runs"] == 1
        assert stats["mean_cost_usd"] == 2.0
        assert stats["mean_policies"] == 4.0

    def test_mean_and_last_over_multiple_runs(self, store):
        base = datetime(2026, 1, 1)
        for i, (cost, policies) in enumerate([(1.0, 2), (3.0, 4)]):
            scan_id = f"s{i}"
            store.record_start(
                scan_id=scan_id, domain_group="quick", mode="standard",
                channels=["crawl"], started_at=base + timedelta(hours=i),
            )
            store.record_completion(
                scan_id=scan_id, status="completed",
                completed_at=base + timedelta(hours=i, minutes=5),
                cost_usd=cost, policies_found=policies,
            )

        stats = store.stats("quick")
        assert stats["runs"] == 2
        assert stats["mean_cost_usd"] == 2.0
        assert stats["mean_policies"] == 3.0
        # Most recently completed run (s1, cost 3.0) is "last".
        assert stats["last_cost_usd"] == 3.0

    def test_stats_scoped_to_domain_group(self, store):
        base = datetime(2026, 1, 1)
        store.record_start(
            scan_id="s1", domain_group="quick", mode="standard",
            channels=["crawl"], started_at=base,
        )
        store.record_completion(
            scan_id="s1", status="completed", completed_at=base,
            cost_usd=1.0, policies_found=1,
        )
        store.record_start(
            scan_id="s2", domain_group="eu", mode="standard",
            channels=["crawl"], started_at=base,
        )
        store.record_completion(
            scan_id="s2", status="completed", completed_at=base,
            cost_usd=5.0, policies_found=5,
        )

        assert store.stats("quick")["runs"] == 1
        assert store.stats("eu")["runs"] == 1
        assert store.stats("nonexistent")["runs"] == 0
