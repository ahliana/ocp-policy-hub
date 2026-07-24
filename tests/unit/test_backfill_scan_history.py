"""Tests for the scan-history backfill importer (WP-5).

Parses a fixture data/logs/audit.jsonl into scans rows: pairs scan_started/
scan_completed events by scan_id, skips unpaired scan_started events,
dry-run previews without writing, and re-running is a no-op (idempotent by
scan_id).
"""

import json

import pytest

from src.storage.backfill_scan_history import backfill
from src.storage.scan_history import ScanHistoryStore


def _write_audit_log(data_dir, events: list[dict]) -> None:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(e) for e in events]
    (log_dir / "audit.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def paired_events():
    return [
        {
            "event": "scan_started", "scan_id": "abc123", "domain_group": "quick",
            "domain_count": 5, "timestamp": "2026-01-01T00:00:00+00:00",
        },
        {
            "event": "policy_found", "scan_id": "abc123", "url": "https://a.gov/x",
            "timestamp": "2026-01-01T00:01:00+00:00",
        },
        {
            "event": "scan_completed", "scan_id": "abc123", "domain_group": "quick",
            "domains_scanned": 5, "policies_found": 2, "cost_usd": 0.42,
            "duration_s": 60.0, "timestamp": "2026-01-01T00:02:00+00:00",
        },
    ]


class TestPairing:
    def test_dry_run_reports_without_writing(self, tmp_path, paired_events):
        _write_audit_log(tmp_path, paired_events)

        result = backfill(data_dir=str(tmp_path), dry_run=True)

        assert result["audit_events_read"] == 3
        assert result["paired_scans_found"] == 1
        assert result["would_add"] == 1
        assert result["new_rows"] == 0
        assert ScanHistoryStore(data_dir=str(tmp_path)).list() == []

    def test_real_run_writes_row(self, tmp_path, paired_events):
        _write_audit_log(tmp_path, paired_events)

        result = backfill(data_dir=str(tmp_path), dry_run=False)

        assert result["new_rows"] == 1
        rows = ScanHistoryStore(data_dir=str(tmp_path)).list()
        assert len(rows) == 1
        row = rows[0]
        assert row["scan_id"] == "abc123"
        assert row["domain_group"] == "quick"
        assert row["status"] == "completed"
        assert row["started_at"] == "2026-01-01T00:00:00+00:00"
        assert row["completed_at"] == "2026-01-01T00:02:00+00:00"
        assert row["domains_scanned"] == 5
        assert row["policies_found"] == 2
        assert row["cost_usd"] == 0.42
        # Historical events never recorded mode/channels.
        assert row["mode"] is None
        assert row["channels"] == []

    def test_rerun_is_idempotent(self, tmp_path, paired_events):
        _write_audit_log(tmp_path, paired_events)

        backfill(data_dir=str(tmp_path), dry_run=False)
        second = backfill(data_dir=str(tmp_path), dry_run=False)

        assert second["new_rows"] == 0
        assert second["already_present"] == 1
        assert len(ScanHistoryStore(data_dir=str(tmp_path)).list()) == 1

    def test_unpaired_scan_started_is_skipped(self, tmp_path):
        _write_audit_log(tmp_path, [
            {
                "event": "scan_started", "scan_id": "orphan", "domain_group": "quick",
                "timestamp": "2026-01-01T00:00:00+00:00",
            },
        ])

        result = backfill(data_dir=str(tmp_path), dry_run=False)

        assert result["paired_scans_found"] == 0
        assert result["new_rows"] == 0
        assert ScanHistoryStore(data_dir=str(tmp_path)).list() == []

    def test_no_audit_file_is_a_noop(self, tmp_path):
        result = backfill(data_dir=str(tmp_path), dry_run=False)
        assert result == {
            "audit_events_read": 0,
            "paired_scans_found": 0,
            "already_present": 0,
            "new_rows": 0,
            "would_add": 0,
        }

    def test_multiple_independent_scans(self, tmp_path):
        _write_audit_log(tmp_path, [
            {"event": "scan_started", "scan_id": "s1", "domain_group": "quick",
             "timestamp": "2026-01-01T00:00:00+00:00"},
            {"event": "scan_completed", "scan_id": "s1", "domain_group": "quick",
             "domains_scanned": 1, "policies_found": 0, "cost_usd": 0.0,
             "timestamp": "2026-01-01T00:01:00+00:00"},
            {"event": "scan_started", "scan_id": "s2", "domain_group": "eu",
             "timestamp": "2026-01-02T00:00:00+00:00"},
            {"event": "scan_completed", "scan_id": "s2", "domain_group": "eu",
             "domains_scanned": 3, "policies_found": 1, "cost_usd": 1.5,
             "timestamp": "2026-01-02T00:03:00+00:00"},
        ])

        result = backfill(data_dir=str(tmp_path), dry_run=False)

        assert result["new_rows"] == 2
        ids = {r["scan_id"] for r in ScanHistoryStore(data_dir=str(tmp_path)).list()}
        assert ids == {"s1", "s2"}

    def test_malformed_lines_are_skipped(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "audit.jsonl").write_text(
            "not json\n"
            + json.dumps({
                "event": "scan_started", "scan_id": "s1", "domain_group": "quick",
                "timestamp": "2026-01-01T00:00:00+00:00",
              }) + "\n"
            + json.dumps({
                "event": "scan_completed", "scan_id": "s1", "domain_group": "quick",
                "domains_scanned": 1, "policies_found": 0, "cost_usd": 0.0,
                "timestamp": "2026-01-01T00:01:00+00:00",
              }) + "\n",
            encoding="utf-8",
        )

        result = backfill(data_dir=str(tmp_path), dry_run=False)
        assert result["new_rows"] == 1

    def test_existing_rows_untouched_by_rerun(self, tmp_path, paired_events):
        _write_audit_log(tmp_path, paired_events)
        backfill(data_dir=str(tmp_path), dry_run=False)

        store = ScanHistoryStore(data_dir=str(tmp_path))
        # Simulate a live update to the row (as ScanManager would do).
        store.record_completion(
            scan_id="abc123", status="completed", completed_at="2099-01-01T00:00:00",
            domains_scanned=999, policies_found=999, cost_usd=999.0,
        )

        backfill(data_dir=str(tmp_path), dry_run=False)

        row = store.list()[0]
        # Backfill's INSERT OR IGNORE must not clobber the live update.
        assert row["domains_scanned"] == 999
