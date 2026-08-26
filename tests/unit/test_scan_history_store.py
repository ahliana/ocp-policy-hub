"""Tests for ScanHistoryStore (WP-5) - the ``scans`` table.

record_start/record_completion write and update one row per scan; list()
filters and paginates newest-first; stats() aggregates actuals for the
cost-projection blend rule (WP-7).

record_domains/domains_for_scan/get (WP-23) and measured_rates (WP-25) work
off the second table, scan_domains - see their own test classes below.
"""

import sqlite3
from datetime import datetime, timedelta

import pytest

from src.core.models import DomainProgress
from src.storage.scan_history import ScanHistoryStore


@pytest.fixture
def store(tmp_path):
    return ScanHistoryStore(data_dir=str(tmp_path))


def _dp(domain_id: str, **overrides) -> DomainProgress:
    defaults = dict(domain_id=domain_id, domain_name=domain_id)
    defaults.update(overrides)
    return DomainProgress(**defaults)


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


@pytest.mark.medium
class TestRecordStartEstimateColumns:
    """WP-24: record_start's estimated_cost_usd/estimated_low_usd/
    estimated_high_usd trio round-trips through list()/get()."""

    def test_estimate_trio_round_trips(self, store):
        store.record_start(
            scan_id="s1", domain_group="quick", mode="standard",
            channels=["crawl"], started_at=datetime(2026, 1, 1),
            estimated_cost_usd=1.23, estimated_low_usd=0.5, estimated_high_usd=3.0,
        )
        row = store.list()[0]
        assert row["estimated_cost_usd"] == 1.23
        assert row["estimated_low_usd"] == 0.5
        assert row["estimated_high_usd"] == 3.0

    def test_defaults_to_null_when_omitted(self, store):
        store.record_start(
            scan_id="s1", domain_group="quick", mode="standard",
            channels=["crawl"], started_at=datetime(2026, 1, 1),
        )
        row = store.list()[0]
        assert row["estimated_cost_usd"] is None
        assert row["estimated_low_usd"] is None
        assert row["estimated_high_usd"] is None

    def test_history_row_keeps_actual_cost_usd_alongside_estimate(self, store):
        store.record_start(
            scan_id="s1", domain_group="quick", mode="standard",
            channels=["crawl"], started_at=datetime(2026, 1, 1),
            estimated_cost_usd=1.0, estimated_low_usd=0.4, estimated_high_usd=2.5,
        )
        store.record_completion(
            scan_id="s1", status="completed", completed_at=datetime(2026, 1, 1),
            cost_usd=0.87,
        )
        row = store.list()[0]
        assert row["cost_usd"] == 0.87
        assert row["estimated_cost_usd"] == 1.0


@pytest.mark.medium
class TestGet:
    """get() (WP-23) - the single-row lookup feeding the DB fallback for
    GET /api/scans/{scan_id}."""

    def test_returns_row_by_scan_id(self, store):
        store.record_start(
            scan_id="s1", domain_group="quick", mode="standard",
            channels=["crawl"], started_at=datetime(2026, 1, 1),
        )
        row = store.get("s1")
        assert row is not None
        assert row["scan_id"] == "s1"
        assert row["domain_group"] == "quick"

    def test_returns_none_for_missing_scan_id(self, store):
        assert store.get("nonexistent") is None


@pytest.mark.medium
class TestRecordDomains:
    """record_domains() (WP-23) - the per-domain funnel, one row per
    domain, written in a single transaction."""

    def test_writes_one_row_per_domain(self, store):
        store.record_domains(
            "s1",
            [
                (_dp("d1", pages_crawled=10, keywords_matched=3), "crawl"),
                (_dp("d2", pages_crawled=40, keywords_matched=1), "law_apis"),
            ],
            completed_at=datetime(2026, 1, 1),
        )
        rows = {r["domain_id"]: r for r in store.domains_for_scan("s1")}
        assert set(rows) == {"d1", "d2"}
        assert rows["d1"]["channel"] == "crawl"
        assert rows["d1"]["pages_crawled"] == 10
        assert rows["d1"]["keywords_matched"] == 3
        assert rows["d2"]["channel"] == "law_apis"

    def test_all_fields_round_trip(self, store):
        progress = _dp(
            "d1", pages_crawled=100, keywords_matched=10, filtered_keywords=5,
            filtered_screening=2, llm_skipped=1, policies_found=3, errors=1,
        )
        store.record_domains(
            "s1", [(progress, "crawl")], completed_at=datetime(2026, 1, 1, 0, 5),
        )
        row = store.domains_for_scan("s1")[0]
        assert row["pages_crawled"] == 100
        assert row["keywords_matched"] == 10
        assert row["filtered_keywords"] == 5
        assert row["filtered_screening"] == 2
        assert row["llm_skipped"] == 1
        assert row["policies_found"] == 3
        assert row["errors"] == 1
        assert row["completed_at"] == "2026-01-01T00:05:00"

    def test_empty_list_is_a_noop(self, store):
        store.record_domains("s1", [], completed_at=datetime(2026, 1, 1))
        assert store.domains_for_scan("s1") == []

    def test_rewriting_same_scan_and_domain_replaces_the_row(self, store):
        store.record_domains(
            "s1", [(_dp("d1", pages_crawled=10), "crawl")],
            completed_at=datetime(2026, 1, 1),
        )
        store.record_domains(
            "s1", [(_dp("d1", pages_crawled=99), "crawl")],
            completed_at=datetime(2026, 1, 2),
        )
        rows = store.domains_for_scan("s1")
        assert len(rows) == 1
        assert rows[0]["pages_crawled"] == 99

    def test_all_or_nothing_on_mid_batch_failure(self, store, monkeypatch):
        """A failure partway through the batch must roll back every row in
        it, not just the ones after the failure point.

        sqlite3.Connection is an immutable C type - neither its instances
        nor the class itself allow patching ``execute`` directly - so this
        swaps in a thin proxy (delegating everything, including the ``with``
        transaction protocol, to the real connection) that raises on the
        Nth INSERT instead.
        """
        class _FlakyConn:
            def __init__(self, real, fail_at):
                self._real, self._fail_at, self._n = real, fail_at, 0

            def execute(self, sql, params=()):
                if sql.strip().startswith("INSERT"):
                    self._n += 1
                    if self._n == self._fail_at:
                        raise sqlite3.OperationalError("simulated mid-batch failure")
                return self._real.execute(sql, params)

            def __enter__(self):
                return self._real.__enter__()

            def __exit__(self, *exc_info):
                return self._real.__exit__(*exc_info)

        domains = [
            (_dp("d1", pages_crawled=1), "crawl"),
            (_dp("d2", pages_crawled=2), "crawl"),
            (_dp("d3", pages_crawled=3), "crawl"),
        ]
        monkeypatch.setattr(store, "_conn", _FlakyConn(store._conn, fail_at=2))

        with pytest.raises(sqlite3.OperationalError):
            store.record_domains("s1", domains, completed_at=datetime(2026, 1, 1))

        # d1's insert (call #1) succeeded before the failure, but the whole
        # transaction must have rolled back - zero rows survive.
        assert store.domains_for_scan("s1") == []


@pytest.mark.medium
class TestDomainsForScan:
    def test_unknown_scan_returns_empty_list(self, store):
        assert store.domains_for_scan("nonexistent") == []

    def test_scoped_to_the_requested_scan(self, store):
        store.record_domains(
            "s1", [(_dp("d1"), "crawl")], completed_at=datetime(2026, 1, 1),
        )
        store.record_domains(
            "s2", [(_dp("d2"), "crawl")], completed_at=datetime(2026, 1, 1),
        )
        assert [r["domain_id"] for r in store.domains_for_scan("s1")] == ["d1"]


@pytest.mark.medium
class TestMeasuredRates:
    """measured_rates() (WP-25) - median/IQR calibration from completed
    scans' scan_domains rows, gated on a >=2-scans/>=3-rows threshold."""

    def test_no_data_returns_all_none_shape(self, store):
        rates = store.measured_rates()
        assert rates == {
            "crawl": {
                "keyword_rate": None, "screening_pass_rate": None,
                "pages_per_domain": None, "scans": 0,
                "spread": {
                    "keyword_rate": {"p25": None, "p75": None},
                    "screening_pass_rate": {"p25": None, "p75": None},
                    "pages_per_domain": {"p25": None, "p75": None},
                },
            },
            "structured": {
                "items_per_source": None, "screening_pass_rate": None, "scans": 0,
                "spread": {
                    "items_per_source": {"p25": None, "p75": None},
                    "screening_pass_rate": {"p25": None, "p75": None},
                },
            },
        }

    def _complete_scan(self, store, scan_id: str, started_at: datetime,
                       status: str = "completed") -> None:
        store.record_start(
            scan_id=scan_id, domain_group="quick", mode="standard",
            channels=["crawl"], started_at=started_at,
        )
        store.record_completion(scan_id=scan_id, status=status, completed_at=started_at)

    def test_budget_capped_scans_count_as_rate_evidence(self, store):
        """A budget-capped scan's per-domain rows are fully-scanned domains -
        excluding them would throw away the calibration data from exactly the
        scans a ceiling truncates (the common case early on)."""
        self._complete_scan(store, "s1", datetime(2026, 1, 1))
        self._complete_scan(store, "s2", datetime(2026, 1, 2),
                            status="completed_budget_reached")
        store.record_domains(
            "s1",
            [(_dp("d1", pages_crawled=10, keywords_matched=1), "crawl"),
             (_dp("d2", pages_crawled=10, keywords_matched=1), "crawl")],
            completed_at=datetime(2026, 1, 1),
        )
        store.record_domains(
            "s2", [(_dp("d3", pages_crawled=10, keywords_matched=1), "crawl")],
            completed_at=datetime(2026, 1, 2),
        )
        rates = store.measured_rates()
        assert rates["crawl"]["scans"] == 2
        assert rates["crawl"]["keyword_rate"] == pytest.approx(0.1)

    def test_below_scan_count_threshold_stays_none(self, store):
        # 1 completed scan with 3 domain rows: rows>=3 but scans<2.
        self._complete_scan(store, "s1", datetime(2026, 1, 1))
        store.record_domains(
            "s1",
            [
                (_dp("d1", pages_crawled=10, keywords_matched=1), "crawl"),
                (_dp("d2", pages_crawled=10, keywords_matched=1), "crawl"),
                (_dp("d3", pages_crawled=10, keywords_matched=1), "crawl"),
            ],
            completed_at=datetime(2026, 1, 1),
        )
        rates = store.measured_rates()
        assert rates["crawl"]["keyword_rate"] is None
        assert rates["crawl"]["scans"] == 1

    def test_below_row_count_threshold_stays_none(self, store):
        # 2 completed scans but only 2 total domain rows: scans>=2 but rows<3.
        self._complete_scan(store, "s1", datetime(2026, 1, 1))
        self._complete_scan(store, "s2", datetime(2026, 1, 2))
        store.record_domains(
            "s1", [(_dp("d1", pages_crawled=10, keywords_matched=1), "crawl")],
            completed_at=datetime(2026, 1, 1),
        )
        store.record_domains(
            "s2", [(_dp("d2", pages_crawled=10, keywords_matched=1), "crawl")],
            completed_at=datetime(2026, 1, 2),
        )
        rates = store.measured_rates()
        assert rates["crawl"]["keyword_rate"] is None
        assert rates["crawl"]["scans"] == 2

    def test_meets_threshold_computes_median_and_spread(self, store):
        # Three completed scans, one crawl domain row each - independently
        # verified via statistics.median/quantiles(n=4, method="inclusive").
        self._complete_scan(store, "s1", datetime(2026, 1, 1))
        self._complete_scan(store, "s2", datetime(2026, 1, 2))
        self._complete_scan(store, "s3", datetime(2026, 1, 3))
        store.record_domains(
            "s1",
            [(_dp("d1", pages_crawled=100, keywords_matched=10, filtered_screening=2,
                  llm_skipped=1), "crawl")],
            completed_at=datetime(2026, 1, 1),
        )
        store.record_domains(
            "s2",
            [(_dp("d2", pages_crawled=200, keywords_matched=30, filtered_screening=5,
                  llm_skipped=0), "crawl")],
            completed_at=datetime(2026, 1, 2),
        )
        store.record_domains(
            "s3",
            [(_dp("d3", pages_crawled=50, keywords_matched=2, filtered_screening=0,
                  llm_skipped=0), "crawl")],
            completed_at=datetime(2026, 1, 3),
        )

        crawl = store.measured_rates()["crawl"]
        assert crawl["scans"] == 3
        assert crawl["keyword_rate"] == pytest.approx(0.10)
        assert crawl["pages_per_domain"] == pytest.approx(100.0)
        assert crawl["screening_pass_rate"] == pytest.approx(0.8333, abs=1e-3)
        assert crawl["spread"]["keyword_rate"]["p25"] == pytest.approx(0.07)
        assert crawl["spread"]["keyword_rate"]["p75"] == pytest.approx(0.125)
        assert crawl["spread"]["pages_per_domain"]["p25"] == pytest.approx(75.0)
        assert crawl["spread"]["pages_per_domain"]["p75"] == pytest.approx(150.0)
        assert crawl["spread"]["screening_pass_rate"]["p25"] == pytest.approx(0.7667, abs=1e-3)
        assert crawl["spread"]["screening_pass_rate"]["p75"] == pytest.approx(0.9167, abs=1e-3)

    def test_only_completed_scans_contribute(self, store):
        self._complete_scan(store, "s1", datetime(2026, 1, 1))
        self._complete_scan(store, "s2", datetime(2026, 1, 2))
        store.record_start(
            scan_id="s3", domain_group="quick", mode="standard",
            channels=["crawl"], started_at=datetime(2026, 1, 3),
        )  # still "running" - never completed

        for scan_id in ("s1", "s2", "s3"):
            store.record_domains(
                scan_id,
                [(_dp(f"d-{scan_id}", pages_crawled=10, keywords_matched=1), "crawl")],
                completed_at=datetime(2026, 1, 1),
            )

        rates = store.measured_rates()
        # Only s1 + s2 count: 2 scans, 2 rows - below the >=3 row threshold.
        assert rates["crawl"]["scans"] == 2
        assert rates["crawl"]["keyword_rate"] is None

    def test_structured_bucket_combines_law_apis_and_transposition(self, store):
        self._complete_scan(store, "s1", datetime(2026, 1, 1))
        self._complete_scan(store, "s2", datetime(2026, 1, 2))
        store.record_domains(
            "s1",
            [
                (_dp("d1", pages_crawled=40, keywords_matched=40), "law_apis"),
                (_dp("d2", pages_crawled=20, keywords_matched=20), "transposition"),
            ],
            completed_at=datetime(2026, 1, 1),
        )
        store.record_domains(
            "s2",
            [(_dp("d3", pages_crawled=60, keywords_matched=60), "law_apis")],
            completed_at=datetime(2026, 1, 2),
        )

        structured = store.measured_rates()["structured"]
        assert structured["scans"] == 2
        # 3 rows total across both structured channels - meets the threshold.
        assert structured["items_per_source"] == pytest.approx(40.0)
        assert structured["screening_pass_rate"] == pytest.approx(1.0)
        # crawl bucket is untouched by structured rows.
        assert store.measured_rates()["crawl"]["scans"] == 0

    def test_screening_pass_rate_clamped_to_zero_one(self, store):
        self._complete_scan(store, "s1", datetime(2026, 1, 1))
        self._complete_scan(store, "s2", datetime(2026, 1, 2))
        # filtered_screening + llm_skipped > keywords_matched would compute
        # a negative raw rate without clamping.
        store.record_domains(
            "s1",
            [
                (_dp("d1", pages_crawled=10, keywords_matched=5, filtered_screening=4,
                     llm_skipped=3), "crawl"),
                (_dp("d2", pages_crawled=10, keywords_matched=5, filtered_screening=4,
                     llm_skipped=3), "crawl"),
            ],
            completed_at=datetime(2026, 1, 1),
        )
        store.record_domains(
            "s2",
            [(_dp("d3", pages_crawled=10, keywords_matched=5, filtered_screening=4,
                  llm_skipped=3), "crawl")],
            completed_at=datetime(2026, 1, 2),
        )

        crawl = store.measured_rates()["crawl"]
        assert crawl["screening_pass_rate"] >= 0.0
        assert crawl["spread"]["screening_pass_rate"]["p25"] >= 0.0
        assert crawl["spread"]["screening_pass_rate"]["p75"] <= 1.0

    def test_pages_crawled_zero_rows_excluded_from_keyword_rate(self, store):
        # A domain with pages_crawled=0 can't contribute a keyword_rate
        # (division by zero) but must not crash the aggregate.
        self._complete_scan(store, "s1", datetime(2026, 1, 1))
        self._complete_scan(store, "s2", datetime(2026, 1, 2))
        store.record_domains(
            "s1",
            [
                (_dp("d1", pages_crawled=0, keywords_matched=0), "crawl"),
                (_dp("d2", pages_crawled=10, keywords_matched=2), "crawl"),
            ],
            completed_at=datetime(2026, 1, 1),
        )
        store.record_domains(
            "s2",
            [(_dp("d3", pages_crawled=10, keywords_matched=2), "crawl")],
            completed_at=datetime(2026, 1, 2),
        )

        crawl = store.measured_rates()["crawl"]
        assert crawl["scans"] == 2
        # 3 rows total meets the threshold; the zero-pages row is simply
        # excluded from the keyword_rate/pages_per_domain aggregates.
        assert crawl["keyword_rate"] == pytest.approx(0.2)
