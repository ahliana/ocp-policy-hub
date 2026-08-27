"""Tests for src.notifications.digest (WP-44): the daily/weekly due-window
math, digest assembly from seeded fixtures, and the scheduled tick that ties
subscriptions + assembly + sending together. Every test here drives Mailer
through a mock (or leaves it unconfigured) - none opens a real socket.
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.core.models import Policy, PolicyType
from src.notifications.digest import (
    _daily_due, _weekly_due, assemble_digest, run_digest_tick, run_digest_tick_for_data_dir,
)
from src.storage.notifications import NotificationStateStore, NotificationSubscriptionsStore
from src.storage.scan_history import ScanHistoryStore
from src.storage.signals_status import FeedFailure, SignalsStatusStore, SweepSummary
from src.storage.store import PolicyStore

MONDAY_0630 = datetime(2026, 1, 5, 6, 30, 0)  # matches test_schedule_runner.py's Monday fixture
TUESDAY_0630 = datetime(2026, 1, 6, 6, 30, 0)


def _policy(**overrides) -> Policy:
    fields = {
        "url": "https://example.gov/policy",
        "policy_name": "Heat Reuse Mandate",
        "jurisdiction": "Testland",
        "policy_type": PolicyType.REGULATION,
        "summary": "Summary text.",
        "relevance_score": 8,
        "review_status": "new",
        "lifecycle_stage": "proposed",
        "discovered_at": MONDAY_0630,
    }
    fields.update(overrides)
    return Policy(**fields)


def _seed_scan(history: ScanHistoryStore, scan_id: str, domain_group: str, status: str, completed_at: datetime):
    history.record_start(scan_id, domain_group, mode="standard", channels=["crawl"], started_at=completed_at)
    history.record_completion(
        scan_id, status=status, completed_at=completed_at, domains_scanned=1,
        policies_found=0, cost_usd=1.0,
    )


# ---------------------------------------------------------------------------
# Frequency window math (pure, UTC pinned)
# ---------------------------------------------------------------------------

class TestDailyDue:
    @pytest.mark.small
    def test_before_0630_is_never_due(self):
        assert _daily_due(None, datetime(2026, 1, 5, 6, 29, 59)) is False

    @pytest.mark.small
    def test_at_0630_with_no_prior_run_is_due(self):
        assert _daily_due(None, datetime(2026, 1, 5, 6, 30, 0)) is True

    @pytest.mark.small
    def test_after_0630_with_no_prior_run_is_due(self):
        assert _daily_due(None, datetime(2026, 1, 5, 23, 0, 0)) is True

    @pytest.mark.small
    def test_already_run_today_is_not_due_again(self):
        last_run = "2026-01-05T06:30:00"
        assert _daily_due(last_run, datetime(2026, 1, 5, 12, 0, 0)) is False

    @pytest.mark.small
    def test_run_on_an_earlier_date_is_due_again(self):
        last_run = "2026-01-04T06:30:00"
        assert _daily_due(last_run, datetime(2026, 1, 5, 6, 30, 0)) is True


class TestWeeklyDue:
    @pytest.mark.small
    def test_non_monday_is_never_due(self):
        # 2026-01-06 is a Tuesday.
        assert _weekly_due(None, datetime(2026, 1, 6, 12, 0, 0)) is False

    @pytest.mark.small
    def test_monday_before_0630_is_not_due(self):
        assert _weekly_due(None, datetime(2026, 1, 5, 6, 29, 59)) is False

    @pytest.mark.small
    def test_monday_at_0630_with_no_prior_run_is_due(self):
        assert _weekly_due(None, MONDAY_0630) is True

    @pytest.mark.small
    def test_already_run_this_monday_is_not_due_again(self):
        assert _weekly_due("2026-01-05T06:30:00", datetime(2026, 1, 5, 18, 0, 0)) is False

    @pytest.mark.small
    def test_run_last_monday_is_due_again(self):
        assert _weekly_due("2025-12-29T06:30:00", MONDAY_0630) is True


# ---------------------------------------------------------------------------
# assemble_digest - early_signals
# ---------------------------------------------------------------------------

@pytest.fixture
def stores(tmp_path):
    return {
        "policy": PolicyStore(data_dir=str(tmp_path)),
        "signals": SignalsStatusStore(data_dir=str(tmp_path)),
        "history": ScanHistoryStore(data_dir=str(tmp_path)),
    }


class TestAssembleEarlySignals:
    @pytest.mark.medium
    def test_empty_when_no_policies(self, stores):
        since = datetime(2026, 1, 1)
        assert assemble_digest(
            "early_signals", since, stores["policy"], stores["signals"], stores["history"],
        ) is None

    @pytest.mark.medium
    def test_reviewed_policy_is_excluded(self, stores):
        stores["policy"].add_policies([_policy(review_status="reviewed")])
        digest = assemble_digest(
            "early_signals", datetime(2026, 1, 1),
            stores["policy"], stores["signals"], stores["history"],
        )
        assert digest is None

    @pytest.mark.medium
    def test_late_lifecycle_stage_is_excluded(self, stores):
        stores["policy"].add_policies([_policy(lifecycle_stage="enacted")])
        digest = assemble_digest(
            "early_signals", datetime(2026, 1, 1),
            stores["policy"], stores["signals"], stores["history"],
        )
        assert digest is None

    @pytest.mark.medium
    def test_discovered_before_since_is_excluded(self, stores):
        stores["policy"].add_policies([_policy(discovered_at=datetime(2026, 1, 1, 0, 0))])
        digest = assemble_digest(
            "early_signals", datetime(2026, 1, 4),
            stores["policy"], stores["signals"], stores["history"],
        )
        assert digest is None

    @pytest.mark.medium
    def test_qualifying_find_is_included(self, stores):
        stores["policy"].add_policies([_policy(
            policy_name="District Heating Mandate", jurisdiction="Testland",
            lifecycle_stage="consultation", discovered_at=datetime(2026, 1, 5, 8, 0),
        )])
        digest = assemble_digest(
            "early_signals", datetime(2026, 1, 4),
            stores["policy"], stores["signals"], stores["history"],
        )
        assert digest is not None
        assert "1 new policy find" in digest["subject"]
        assert "District Heating Mandate" in digest["body"]
        assert "Testland" in digest["body"]
        assert "consultation" in digest["body"]
        assert "Open the admin page to act on these." in digest["body"]

    @pytest.mark.medium
    def test_counts_multiple_qualifying_finds(self, stores):
        stores["policy"].add_policies([
            _policy(url="https://a.example.gov", discovered_at=datetime(2026, 1, 5, 8, 0)),
            _policy(url="https://b.example.gov", discovered_at=datetime(2026, 1, 5, 9, 0)),
        ])
        digest = assemble_digest(
            "early_signals", datetime(2026, 1, 4),
            stores["policy"], stores["signals"], stores["history"],
        )
        assert "2 new policy find" in digest["subject"]


# ---------------------------------------------------------------------------
# assemble_digest - ops_alerts
# ---------------------------------------------------------------------------

class TestAssembleOpsAlerts:
    @pytest.mark.medium
    def test_empty_when_nothing_to_report(self, stores):
        assert assemble_digest(
            "ops_alerts", datetime(2026, 1, 1),
            stores["policy"], stores["signals"], stores["history"],
        ) is None

    @pytest.mark.medium
    def test_sweep_failures_before_since_are_excluded(self, stores):
        stores["signals"].record(SweepSummary(
            ts="2026-01-01T00:00:00+00:00", feeds_tried=3, feeds_ok=1, feeds_failed=2,
            failures=[FeedFailure(name="feed:DCD", detail="HTTP 404")],
        ))
        digest = assemble_digest(
            "ops_alerts", datetime(2026, 1, 4),
            stores["policy"], stores["signals"], stores["history"],
        )
        assert digest is None

    @pytest.mark.medium
    def test_sweep_failures_since_are_included(self, stores):
        stores["signals"].record(SweepSummary(
            ts="2026-01-05T00:00:00+00:00", feeds_tried=3, feeds_ok=1, feeds_failed=2,
            failures=[FeedFailure(name="feed:DCD", detail="HTTP 404")],
        ))
        digest = assemble_digest(
            "ops_alerts", datetime(2026, 1, 4),
            stores["policy"], stores["signals"], stores["history"],
        )
        assert digest is not None
        assert "2 operational alert" in digest["subject"]
        assert "feed:DCD" in digest["body"]
        assert "HTTP 404" in digest["body"]

    @pytest.mark.medium
    def test_failed_scan_since_is_included(self, stores):
        _seed_scan(stores["history"], "s1", "quick", "failed", datetime(2026, 1, 5, 0, 0))
        digest = assemble_digest(
            "ops_alerts", datetime(2026, 1, 4),
            stores["policy"], stores["signals"], stores["history"],
        )
        assert digest is not None
        assert "quick" in digest["body"]
        assert "1 operational alert" in digest["subject"]

    @pytest.mark.medium
    def test_budget_capped_scan_since_is_included(self, stores):
        _seed_scan(
            stores["history"], "s1", "eu", "completed_budget_reached", datetime(2026, 1, 5, 0, 0),
        )
        digest = assemble_digest(
            "ops_alerts", datetime(2026, 1, 4),
            stores["policy"], stores["signals"], stores["history"],
        )
        assert digest is not None
        assert "stopped early after reaching their budget" in digest["body"]

    @pytest.mark.medium
    def test_scan_completed_before_since_is_excluded(self, stores):
        _seed_scan(stores["history"], "s1", "quick", "failed", datetime(2026, 1, 1, 0, 0))
        digest = assemble_digest(
            "ops_alerts", datetime(2026, 1, 4),
            stores["policy"], stores["signals"], stores["history"],
        )
        assert digest is None

    @pytest.mark.medium
    def test_completed_scan_is_not_an_alert(self, stores):
        _seed_scan(stores["history"], "s1", "quick", "completed", datetime(2026, 1, 5, 0, 0))
        digest = assemble_digest(
            "ops_alerts", datetime(2026, 1, 4),
            stores["policy"], stores["signals"], stores["history"],
        )
        assert digest is None

    @pytest.mark.medium
    def test_sweep_and_scan_failures_sum_into_one_total(self, stores):
        stores["signals"].record(SweepSummary(
            ts="2026-01-05T00:00:00+00:00", feeds_tried=2, feeds_ok=1, feeds_failed=1,
            failures=[FeedFailure(name="feed:X", detail="timeout")],
        ))
        _seed_scan(stores["history"], "s1", "quick", "failed", datetime(2026, 1, 5, 1, 0))
        digest = assemble_digest(
            "ops_alerts", datetime(2026, 1, 4),
            stores["policy"], stores["signals"], stores["history"],
        )
        assert "2 operational alert" in digest["subject"]


class TestAssembleDigestUnknownTopic:
    @pytest.mark.medium
    def test_raises_for_unknown_topic(self, stores):
        with pytest.raises(ValueError):
            assemble_digest(
                "bogus", datetime(2026, 1, 1),
                stores["policy"], stores["signals"], stores["history"],
            )


# ---------------------------------------------------------------------------
# run_digest_tick - the scheduled job, mailer mocked
# ---------------------------------------------------------------------------

def _mailer(configured: bool) -> MagicMock:
    mailer = MagicMock()
    mailer.smtp_configured = configured
    mailer.send = MagicMock(return_value=True)
    return mailer


@pytest.fixture
def tick_stores(tmp_path):
    return {
        "subscriptions": NotificationSubscriptionsStore(data_dir=str(tmp_path)),
        "state": NotificationStateStore(data_dir=str(tmp_path)),
        "policy": PolicyStore(data_dir=str(tmp_path)),
        "signals": SignalsStatusStore(data_dir=str(tmp_path)),
        "history": ScanHistoryStore(data_dir=str(tmp_path)),
    }


class TestRunDigestTick:
    @pytest.mark.medium
    def test_not_due_is_a_noop(self, tick_stores):
        mailer = _mailer(configured=True)
        fired = run_digest_tick(
            tick_stores["subscriptions"], tick_stores["state"], mailer,
            tick_stores["policy"], tick_stores["signals"], tick_stores["history"],
            now=datetime(2026, 1, 6, 3, 0, 0),  # Tuesday, before 06:30
        )
        assert fired == {}
        mailer.send.assert_not_called()
        assert tick_stores["state"].get_last_digest() is None

    @pytest.mark.medium
    def test_unconfigured_smtp_records_skip_for_due_frequencies(self, tick_stores):
        mailer = _mailer(configured=False)
        fired = run_digest_tick(
            tick_stores["subscriptions"], tick_stores["state"], mailer,
            tick_stores["policy"], tick_stores["signals"], tick_stores["history"],
            now=TUESDAY_0630,  # daily only
        )
        assert fired == {"daily": {"topics_sent": {}, "skipped": "no credentials"}}
        mailer.send.assert_not_called()
        last_digest = tick_stores["state"].get_last_digest()
        assert last_digest["frequencies"]["daily"]["skipped"] == "no credentials"

    @pytest.mark.medium
    def test_both_frequencies_fire_on_monday(self, tick_stores):
        mailer = _mailer(configured=False)
        fired = run_digest_tick(
            tick_stores["subscriptions"], tick_stores["state"], mailer,
            tick_stores["policy"], tick_stores["signals"], tick_stores["history"],
            now=MONDAY_0630,
        )
        assert set(fired) == {"daily", "weekly"}
        assert tick_stores["state"].get_digest_last_run("daily") == MONDAY_0630.isoformat()
        assert tick_stores["state"].get_digest_last_run("weekly") == MONDAY_0630.isoformat()

    @pytest.mark.medium
    def test_configured_with_nothing_to_report_sends_nothing(self, tick_stores):
        tick_stores["subscriptions"].create(
            email="a@example.com", topics=["early_signals", "ops_alerts"], frequency="daily",
        )
        mailer = _mailer(configured=True)
        fired = run_digest_tick(
            tick_stores["subscriptions"], tick_stores["state"], mailer,
            tick_stores["policy"], tick_stores["signals"], tick_stores["history"],
            now=TUESDAY_0630,
        )
        assert fired == {"daily": {"topics_sent": {}, "skipped": None}}
        mailer.send.assert_not_called()

    @pytest.mark.medium
    def test_sends_to_matching_subscriber_only(self, tick_stores):
        tick_stores["policy"].add_policies([_policy(discovered_at=TUESDAY_0630)])
        tick_stores["subscriptions"].create(
            email="daily-early@example.com", topics=["early_signals"], frequency="daily",
        )
        tick_stores["subscriptions"].create(
            email="daily-ops-only@example.com", topics=["ops_alerts"], frequency="daily",
        )
        tick_stores["subscriptions"].create(
            email="immediate@example.com", topics=["early_signals"], frequency="immediate",
        )
        mailer = _mailer(configured=True)

        fired = run_digest_tick(
            tick_stores["subscriptions"], tick_stores["state"], mailer,
            tick_stores["policy"], tick_stores["signals"], tick_stores["history"],
            now=TUESDAY_0630,
        )

        assert fired["daily"]["topics_sent"] == {"early_signals": 1}
        mailer.send.assert_called_once()
        recipients, subject, _body = mailer.send.call_args[0]
        assert recipients == ["daily-early@example.com"]
        assert "1 new policy find" in subject


# ---------------------------------------------------------------------------
# run_digest_tick_for_data_dir - real-wiring, no credentials, end to end
# ---------------------------------------------------------------------------

class TestRunDigestTickForDataDir:
    @pytest.mark.medium
    def test_provable_without_credentials(self, tmp_path):
        # No config/notifications.yaml at all under this tmp config dir -
        # Mailer.smtp_configured is False by construction; nothing here can
        # reach a real socket.
        run_digest_tick_for_data_dir(
            data_dir=str(tmp_path), config_dir=str(tmp_path), now=MONDAY_0630,
        )
        last_digest = NotificationStateStore(data_dir=str(tmp_path)).get_last_digest()
        assert last_digest["frequencies"]["daily"]["skipped"] == "no credentials"
        assert last_digest["frequencies"]["weekly"]["skipped"] == "no credentials"

    @pytest.mark.medium
    def test_never_raises_even_if_a_dependency_blows_up(self, tmp_path, monkeypatch, caplog):
        import logging

        import src.notifications.digest as digest_module

        def _boom(*args, **kwargs):
            # A realistic failure class - the wrapper's catch is deliberately
            # narrowed (sqlite3/OSError/ValueError/KeyError), not blind.
            import sqlite3
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(digest_module, "run_digest_tick", _boom)
        with caplog.at_level(logging.ERROR, logger="src.notifications.digest"):
            # Must not raise - and the failure must be visible in the log,
            # not silently swallowed.
            run_digest_tick_for_data_dir(
                data_dir=str(tmp_path), config_dir=str(tmp_path), now=MONDAY_0630,
            )
        assert any("digest tick failed" in r.message.lower() for r in caplog.records)
