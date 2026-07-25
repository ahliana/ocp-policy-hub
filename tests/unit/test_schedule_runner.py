"""Tests for src.orchestration.schedule_runner (WP-11).

run_due_schedules() is one tick of the loop, factored out as a standalone
coroutine so it's directly testable with a fake ScanManager, a real
SchedulesStore (SQLite, tmp_path-backed) and a frozen "now" - no FastAPI,
no real asyncio.create_task loop, no time.sleep.
"""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from src.core.models import ScanJob, ScanStatus
from src.orchestration.schedule_runner import fire_schedule, run_due_schedules
from src.storage.schedules import SchedulesStore


class FakeManager:
    """Duck-types the ScanManager surface schedule_runner depends on."""

    def __init__(self):
        self._jobs: dict[str, ScanJob] = {}
        self.start_scan = AsyncMock(side_effect=self._default_start_scan)
        self.start_scan_calls: list[dict] = []

    def _default_start_scan(self, domains_group, deep=False, channels=None, category=None):
        scan_id = f"scan-{len(self.start_scan_calls)}"
        self.start_scan_calls.append({
            "domains_group": domains_group, "deep": deep,
            "channels": channels, "category": category,
        })
        job = ScanJob(scan_id=scan_id, status=ScanStatus.RUNNING, domain_group=domains_group)
        self._jobs[scan_id] = job
        return job

    @property
    def jobs(self):
        return self._jobs

    def add_job(self, scan_id: str, domain_group: str, status: ScanStatus):
        self._jobs[scan_id] = ScanJob(scan_id=scan_id, status=status, domain_group=domain_group)


@pytest.fixture
def store(tmp_path):
    return SchedulesStore(data_dir=str(tmp_path))


@pytest.fixture
def data_dir(tmp_path):
    return str(tmp_path)


def _due_schedule(store, now, **overrides):
    row = store.create(
        name=overrides.pop("name", "Test schedule"),
        domains=overrides.pop("domains", "quick"),
        channels=overrides.pop("channels", ["crawl"]),
        deep=overrides.pop("deep", False),
        topic=overrides.pop("topic", None),
        cadence=overrides.pop("cadence", "weekly:0:06:00"),
        monthly_ceiling_usd=overrides.pop("monthly_ceiling_usd", None),
    )
    # Force it due "now" regardless of the cadence's real next occurrence.
    store._conn.execute(
        "UPDATE schedules SET next_run_at = ? WHERE id = ?",
        (now.isoformat(), row["id"]),
    )
    store._conn.commit()
    if overrides:
        store.update(row["id"], **overrides)
    return store.get(row["id"])


def _read_audit_events(data_dir: str) -> list[dict]:
    import json
    from pathlib import Path
    audit_file = Path(data_dir) / "logs" / "audit.jsonl"
    if not audit_file.exists():
        return []
    return [json.loads(line) for line in audit_file.read_text().splitlines() if line.strip()]


class TestDueFires:
    @pytest.mark.asyncio
    async def test_due_schedule_fires_and_marks_ran(self, store, data_dir):
        now = datetime(2026, 1, 5, 6, 0)
        schedule = _due_schedule(store, now, domains="quick", channels=["crawl"], deep=True)
        manager = FakeManager()

        await run_due_schedules(manager, store, data_dir=data_dir, now=now)

        manager.start_scan.assert_awaited_once()
        call = manager.start_scan_calls[0]
        assert call["domains_group"] == "quick"
        assert call["deep"] is True
        assert call["channels"] == ["crawl"]

        updated = store.get(schedule["id"])
        assert updated["last_scan_id"] == "scan-0"
        assert updated["last_run_at"] == now.isoformat()
        assert updated["next_run_at"] > now.isoformat()

    @pytest.mark.asyncio
    async def test_topic_passed_as_category(self, store, data_dir):
        now = datetime(2026, 1, 5, 6, 0)
        _due_schedule(store, now, topic="heat-reuse")
        manager = FakeManager()

        await run_due_schedules(manager, store, data_dir=data_dir, now=now)

        assert manager.start_scan_calls[0]["category"] == "heat-reuse"


class TestNotDue:
    @pytest.mark.asyncio
    async def test_future_next_run_at_does_not_fire(self, store, data_dir):
        now = datetime(2026, 1, 5, 6, 0)
        row = store.create(name="Future", domains="quick", channels=["crawl"],
                            deep=False, topic=None, cadence="weekly:0:06:00")
        # created next_run_at is in the future relative to "now" already
        manager = FakeManager()

        await run_due_schedules(manager, store, data_dir=data_dir, now=now)

        manager.start_scan.assert_not_awaited()
        assert store.get(row["id"])["last_scan_id"] is None

    @pytest.mark.asyncio
    async def test_disabled_schedule_does_not_fire_even_when_due(self, store, data_dir):
        now = datetime(2026, 1, 5, 6, 0)
        schedule = _due_schedule(store, now, enabled=False)
        manager = FakeManager()

        await run_due_schedules(manager, store, data_dir=data_dir, now=now)

        manager.start_scan.assert_not_awaited()
        assert store.get(schedule["id"])["last_scan_id"] is None


class TestBusySkips:
    @pytest.mark.asyncio
    async def test_busy_scope_is_skipped(self, store, data_dir):
        now = datetime(2026, 1, 5, 6, 0)
        schedule = _due_schedule(store, now, domains="quick")
        manager = FakeManager()
        manager.add_job("running-scan", "quick", ScanStatus.RUNNING)

        await run_due_schedules(manager, store, data_dir=data_dir, now=now)

        manager.start_scan.assert_not_awaited()
        assert store.get(schedule["id"])["last_scan_id"] is None

        events = _read_audit_events(data_dir)
        assert any(e.get("event") == "schedule_skipped_busy" for e in events)

    @pytest.mark.asyncio
    async def test_busy_skip_keeps_next_run_at_due_for_retry(self, store, data_dir):
        # A schedule skipped for busy must NOT lose its occurrence: the claim
        # is released (next_run_at restored) so the very next tick retries it
        # once the scope is free. Regression for the claim-vs-skip interaction.
        now = datetime(2026, 1, 5, 6, 0)
        schedule = _due_schedule(store, now, domains="quick")
        manager = FakeManager()
        manager.add_job("running-scan", "quick", ScanStatus.RUNNING)

        await run_due_schedules(manager, store, data_dir=data_dir, now=now)
        assert store.get(schedule["id"])["next_run_at"] == now.isoformat()  # still due

        # Scope frees up; the next tick fires it (occurrence not lost).
        manager._jobs.clear()
        await run_due_schedules(manager, store, data_dir=data_dir, now=now)
        assert store.get(schedule["id"])["last_scan_id"] is not None

    @pytest.mark.asyncio
    async def test_ceiling_pause_keeps_next_run_at_due_for_retry(self, store, data_dir):
        now = datetime(2026, 1, 5, 6, 0)
        schedule = _due_schedule(store, now, domains="quick", monthly_ceiling_usd=10.0)
        store._conn.execute(
            "INSERT INTO scans (scan_id, domain_group, status, completed_at, cost_usd) "
            "VALUES (?, ?, 'completed', ?, ?)",
            ("s1", "quick", "2026-01-01T00:00:00", 15.0),
        )
        store._conn.commit()
        manager = FakeManager()

        await run_due_schedules(manager, store, data_dir=data_dir, now=now)
        # Paused but still due, so it re-checks spend on the next tick and
        # resumes on its own once the ceiling clears.
        assert store.get(schedule["id"])["next_run_at"] == now.isoformat()
        assert "monthly ceiling reached" in store.get(schedule["id"])["paused_reason"]

    @pytest.mark.asyncio
    async def test_different_scope_running_does_not_block(self, store, data_dir):
        now = datetime(2026, 1, 5, 6, 0)
        schedule = _due_schedule(store, now, domains="quick")
        manager = FakeManager()
        manager.add_job("running-scan", "eu", ScanStatus.RUNNING)

        await run_due_schedules(manager, store, data_dir=data_dir, now=now)

        manager.start_scan.assert_awaited_once()
        assert store.get(schedule["id"])["last_scan_id"] is not None


class TestCeilingPause:
    @pytest.mark.asyncio
    async def test_ceiling_reached_pauses_and_does_not_run(self, store, data_dir):
        now = datetime(2026, 1, 5, 6, 0)
        schedule = _due_schedule(store, now, domains="quick", monthly_ceiling_usd=10.0)
        store._conn.execute(
            "INSERT INTO scans (scan_id, domain_group, status, completed_at, cost_usd) "
            "VALUES (?, ?, 'completed', ?, ?)",
            ("s1", "quick", "2026-01-01T00:00:00", 15.0),
        )
        store._conn.commit()
        manager = FakeManager()

        await run_due_schedules(manager, store, data_dir=data_dir, now=now)

        manager.start_scan.assert_not_awaited()
        updated = store.get(schedule["id"])
        assert updated["last_scan_id"] is None
        assert "monthly ceiling reached" in updated["paused_reason"]
        assert "$15.00" in updated["paused_reason"]
        assert "$10.00" in updated["paused_reason"]
        # Ceiling pause does not disable the schedule - see module docstring.
        assert updated["enabled"] is True

        events = _read_audit_events(data_dir)
        assert any(e.get("event") == "schedule_skipped_ceiling" for e in events)

    @pytest.mark.asyncio
    async def test_ceiling_clear_resumes_and_clears_paused_reason(self, store, data_dir):
        now = datetime(2026, 1, 5, 6, 0)
        schedule = _due_schedule(
            store, now, domains="quick", monthly_ceiling_usd=100.0,
            paused_reason="monthly ceiling reached ($150.00 of $100.00)",
        )
        # Spend is well under the ceiling now (e.g. a new month).
        manager = FakeManager()

        await run_due_schedules(manager, store, data_dir=data_dir, now=now)

        manager.start_scan.assert_awaited_once()
        updated = store.get(schedule["id"])
        assert updated["paused_reason"] is None
        assert updated["last_scan_id"] is not None

    @pytest.mark.asyncio
    async def test_no_ceiling_set_never_pauses(self, store, data_dir):
        now = datetime(2026, 1, 5, 6, 0)
        schedule = _due_schedule(store, now, domains="quick", monthly_ceiling_usd=None)
        store._conn.execute(
            "INSERT INTO scans (scan_id, domain_group, status, completed_at, cost_usd) "
            "VALUES (?, ?, 'completed', ?, ?)",
            ("s1", "quick", "2026-01-01T00:00:00", 100000.0),
        )
        store._conn.commit()
        manager = FakeManager()

        await run_due_schedules(manager, store, data_dir=data_dir, now=now)

        manager.start_scan.assert_awaited_once()
        assert store.get(schedule["id"])["paused_reason"] is None


class TestResilience:
    @pytest.mark.asyncio
    async def test_one_bad_schedule_does_not_stop_the_rest(self, store, data_dir):
        now = datetime(2026, 1, 5, 6, 0)
        bad = _due_schedule(store, now, domains="bad-scope", name="Bad")
        good = _due_schedule(store, now, domains="good-scope", name="Good")

        manager = FakeManager()

        async def side_effect(domains_group, deep=False, channels=None, category=None):
            if domains_group == "bad-scope":
                raise RuntimeError("boom")
            return manager._default_start_scan(domains_group, deep, channels, category)

        manager.start_scan = AsyncMock(side_effect=side_effect)

        await run_due_schedules(manager, store, data_dir=data_dir, now=now)

        assert manager.start_scan.await_count == 2
        assert store.get(good["id"])["last_scan_id"] is not None
        assert store.get(bad["id"])["last_scan_id"] is None

    @pytest.mark.asyncio
    async def test_listing_failure_does_not_raise(self, data_dir):
        """A store whose list() blows up must not crash the tick."""
        class ExplodingStore:
            def list(self):
                raise RuntimeError("db is on fire")

        manager = FakeManager()
        await run_due_schedules(manager, ExplodingStore(), data_dir=data_dir, now=datetime(2026, 1, 1))
        manager.start_scan.assert_not_awaited()


class TestClaimGuard:
    """The atomic claim prevents multiple uvicorn workers from firing the
    same due schedule on the same tick (review finding, WP-11)."""

    def test_claim_due_wins_once_then_loses(self, store):
        now = datetime(2026, 1, 5, 6, 0)
        schedule = _due_schedule(store, now, domains="quick")
        observed = store.get(schedule["id"])["next_run_at"]
        advanced = "2026-02-01T06:00:00"

        # First worker's claim wins; a second worker still observing the old
        # next_run_at loses because the row has already moved.
        assert store.claim_due(schedule["id"], observed, advanced) is True
        assert store.claim_due(schedule["id"], observed, "2026-03-01T06:00:00") is False
        assert store.get(schedule["id"])["next_run_at"] == advanced

    def test_claim_survives_a_second_store_connection(self, tmp_path):
        # Two SchedulesStore instances = two connections to the same db file,
        # standing in for two worker processes.
        now = datetime(2026, 1, 5, 6, 0)
        store_a = SchedulesStore(data_dir=str(tmp_path))
        store_b = SchedulesStore(data_dir=str(tmp_path))
        schedule = _due_schedule(store_a, now, domains="quick")
        observed = store_a.get(schedule["id"])["next_run_at"]

        assert store_a.claim_due(schedule["id"], observed, "2026-02-01T06:00:00") is True
        assert store_b.claim_due(schedule["id"], observed, "2026-02-08T06:00:00") is False

    @pytest.mark.asyncio
    async def test_due_schedule_fires_exactly_once_across_two_ticks(self, store, data_dir):
        # After a tick fires a due schedule, the claim has advanced
        # next_run_at into the future, so an immediately-following tick at the
        # same "now" finds nothing due and does not fire a second scan - the
        # single-process expression of the cross-worker guard.
        now = datetime(2026, 1, 5, 6, 0)
        _due_schedule(store, now, domains="quick", cadence="weekly:0:06:00")
        manager = FakeManager()

        await run_due_schedules(manager, store, data_dir=data_dir, now=now)
        await run_due_schedules(manager, store, data_dir=data_dir, now=now)

        manager.start_scan.assert_awaited_once()


class TestFireScheduleDirect:
    """fire_schedule() is the single-schedule primitive reused by both the
    tick loop and the run-now route - exercised directly here too."""

    @pytest.mark.asyncio
    async def test_fires_regardless_of_next_run_at(self, store, data_dir):
        now = datetime(2026, 1, 5, 6, 0)
        # Not due (next_run_at left in the future by create()).
        schedule = store.create(name="Manual", domains="quick", channels=["crawl"],
                                 deep=False, topic=None, cadence="weekly:0:06:00")
        manager = FakeManager()

        await fire_schedule(manager, store, schedule, data_dir, now)

        manager.start_scan.assert_awaited_once()
        assert store.get(schedule["id"])["last_scan_id"] is not None
