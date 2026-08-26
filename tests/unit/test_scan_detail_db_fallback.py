"""Tests for GET /api/scans/{scan_id}'s DB fallback (WP-23).

The in-memory path (manager.jobs) stays primary and returns the full live
shape. Once a completed scan's job has left process memory (a server
restart, or eviction), the route falls back to the persisted
scans/scan_domains rows - a completed scan's funnel must survive restart.
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.core.models import DomainProgress, ScanJob, ScanProgress, ScanStatus
from src.storage.scan_history import ScanHistoryStore

pytestmark = pytest.mark.medium


def _dp(domain_id: str, **overrides) -> DomainProgress:
    defaults = dict(domain_id=domain_id, domain_name=domain_id)
    defaults.update(overrides)
    return DomainProgress(**defaults)


@pytest.fixture
def history(tmp_path):
    h = ScanHistoryStore(data_dir=str(tmp_path))
    base = datetime(2026, 1, 1)
    h.record_start(
        scan_id="s1", domain_group="quick", mode="standard", channels=["crawl"],
        started_at=base, estimated_cost_usd=1.0, estimated_low_usd=0.4, estimated_high_usd=2.5,
    )
    h.record_completion(
        scan_id="s1", status="completed", completed_at=base,
        domains_scanned=1, policies_found=2, cost_usd=0.87,
        input_tokens=1000, output_tokens=200,
    )
    h.record_domains(
        "s1",
        [(_dp("test_gov", pages_crawled=42, keywords_matched=7, policies_found=2), "crawl")],
        completed_at=base,
    )
    return h


@pytest.fixture
def mock_manager():
    manager = MagicMock()
    manager.jobs = {}  # nothing in process memory - forces the DB fallback
    manager.get_policies.return_value = []
    return manager


def _client(manager, history, monkeypatch, admin_token=None) -> TestClient:
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    if admin_token:
        monkeypatch.setenv("ADMIN_TOKEN", admin_token)

    from src.api.app import app
    from src.api import deps

    app.dependency_overrides[deps.get_scan_manager] = lambda: manager
    app.dependency_overrides[deps.get_scan_history_store] = lambda: history
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    from src.api.app import app
    app.dependency_overrides.clear()


class TestDbFallback:
    def test_falls_back_to_db_when_job_not_in_memory(self, mock_manager, history, monkeypatch):
        with _client(mock_manager, history, monkeypatch) as c:
            resp = c.get("/api/scans/s1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["scan_id"] == "s1"
        assert data["status"] == "completed"
        assert data["domain_count"] == 1
        assert data["policy_count"] == 2
        assert data["policies"] == []
        assert data["cost"]["total_usd"] == 0.87
        assert data["cost"]["input_tokens"] == 1000
        assert data["budget_reached"] is False

    def test_domain_funnel_survives_in_db_fallback(self, mock_manager, history, monkeypatch):
        with _client(mock_manager, history, monkeypatch) as c:
            resp = c.get("/api/scans/s1")
        domains = resp.json()["progress"]["domains"]
        assert len(domains) == 1
        assert domains[0]["domain_id"] == "test_gov"
        assert domains[0]["channel"] == "crawl"
        assert domains[0]["pages_crawled"] == 42
        assert domains[0]["keywords_matched"] == 7
        assert domains[0]["policies_found"] == 2

    def test_unknown_scan_still_404s(self, mock_manager, history, monkeypatch):
        with _client(mock_manager, history, monkeypatch) as c:
            resp = c.get("/api/scans/does-not-exist")
        assert resp.status_code == 404

    def test_budget_reached_status_reflected_in_fallback(self, mock_manager, history, monkeypatch):
        history.record_completion(
            scan_id="s1", status="completed_budget_reached", completed_at=datetime(2026, 1, 1),
        )
        with _client(mock_manager, history, monkeypatch) as c:
            resp = c.get("/api/scans/s1")
        assert resp.json()["budget_reached"] is True

    def test_in_memory_job_still_wins_when_present(self, history, monkeypatch):
        manager = MagicMock()
        manager.jobs = {
            "s1": ScanJob(
                scan_id="s1", status=ScanStatus.RUNNING, domain_count=5,
                progress=ScanProgress(total_domains=5, completed_domains=1),
            ),
        }
        manager.get_policies.return_value = []
        with _client(manager, history, monkeypatch) as c:
            resp = c.get("/api/scans/s1")
        data = resp.json()
        # In-memory status ("running") wins over the DB row's ("completed").
        assert data["status"] == "running"
        assert data["domain_count"] == 5

    def test_admin_gate_still_applies_on_fallback_path(self, mock_manager, history, monkeypatch):
        with _client(mock_manager, history, monkeypatch, admin_token="secret") as c:
            resp = c.get("/api/scans/s1")
        assert resp.status_code == 403
