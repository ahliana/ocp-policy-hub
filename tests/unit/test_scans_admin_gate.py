"""Tests for the admin gate on GET /api/scans and GET /api/scans/{scan_id}.

These are admin operational views: they can leak unreviewed/rejected
policies plus cost/token data for any in-process scan, so unlike most GET
routes they get the full admin gate (mirrors GET /api/scans/history in the
same module) rather than just a public-visibility clamp.
"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.core.models import ScanJob, ScanProgress, ScanStatus


@pytest.fixture
def mock_manager():
    manager = MagicMock()
    manager.jobs = {
        "s1": ScanJob(
            scan_id="s1",
            status=ScanStatus.COMPLETED,
            domain_count=1,
            policy_count=0,
            progress=ScanProgress(total_domains=1, completed_domains=1),
        ),
    }
    manager.get_policies.return_value = []
    return manager


def _client(mock_manager, monkeypatch, admin_token=None, remote_host=None):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    if admin_token:
        monkeypatch.setenv("ADMIN_TOKEN", admin_token)

    from src.api.app import app
    from src.api import deps

    app.dependency_overrides[deps.get_scan_manager] = lambda: mock_manager
    if remote_host:
        return TestClient(app, client=(remote_host, 12345))
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    from src.api.app import app

    app.dependency_overrides.clear()


class TestListScansAdminGate:
    def test_non_admin_gets_403(self, mock_manager, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        with _client(mock_manager, monkeypatch, admin_token="secret") as c:
            resp = c.get("/api/scans")
        assert resp.status_code == 403

    def test_admin_with_token_succeeds(self, mock_manager, monkeypatch):
        with _client(mock_manager, monkeypatch, admin_token="secret") as c:
            resp = c.get("/api/scans", headers={"X-Admin-Token": "secret"})
        assert resp.status_code == 200

    def test_remote_client_rejected_when_token_unset(self, mock_manager, monkeypatch):
        with _client(mock_manager, monkeypatch, remote_host="203.0.113.5") as c:
            resp = c.get("/api/scans")
        assert resp.status_code == 403

    def test_local_open_mode_counts_as_admin(self, mock_manager, monkeypatch):
        with _client(mock_manager, monkeypatch) as c:
            resp = c.get("/api/scans")
        assert resp.status_code == 200


class TestGetScanAdminGate:
    def test_non_admin_gets_403(self, mock_manager, monkeypatch):
        with _client(mock_manager, monkeypatch, admin_token="secret") as c:
            resp = c.get("/api/scans/s1")
        assert resp.status_code == 403

    def test_admin_with_token_succeeds(self, mock_manager, monkeypatch):
        with _client(mock_manager, monkeypatch, admin_token="secret") as c:
            resp = c.get("/api/scans/s1", headers={"X-Admin-Token": "secret"})
        assert resp.status_code == 200

    def test_remote_client_rejected_when_token_unset(self, mock_manager, monkeypatch):
        with _client(mock_manager, monkeypatch, remote_host="203.0.113.5") as c:
            resp = c.get("/api/scans/s1")
        assert resp.status_code == 403

    def test_local_open_mode_counts_as_admin(self, mock_manager, monkeypatch):
        with _client(mock_manager, monkeypatch) as c:
            resp = c.get("/api/scans/s1")
        assert resp.status_code == 200
