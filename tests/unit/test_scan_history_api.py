"""Tests for GET /api/scans/history (WP-5) — admin-only scan run history.

403 for non-admin callers, paginated shape with total/limit/offset,
domain_group/status filters, newest-first ordering.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from src.storage.scan_history import ScanHistoryStore


@pytest.fixture
def history(tmp_path):
    h = ScanHistoryStore(data_dir=str(tmp_path))
    base = datetime(2026, 1, 1)
    h.record_start(
        scan_id="s1", domain_group="quick", mode="standard",
        channels=["crawl"], started_at=base,
    )
    h.record_completion(
        scan_id="s1", status="completed", completed_at=base,
        domains_scanned=5, policies_found=2, cost_usd=0.5,
    )
    h.record_start(
        scan_id="s2", domain_group="eu", mode="deep",
        channels=["crawl", "law_apis"], started_at=base + timedelta(hours=1),
    )
    h.record_completion(
        scan_id="s2", status="failed", completed_at=base + timedelta(hours=1),
        domains_scanned=3, policies_found=0, cost_usd=0.1,
    )
    return h


def _client(history, monkeypatch, admin_token=None):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    if admin_token:
        monkeypatch.setenv("ADMIN_TOKEN", admin_token)

    from src.api.app import app
    from src.api import deps

    app.dependency_overrides[deps.get_scan_history_store] = lambda: history
    return TestClient(app)


@pytest.fixture
def client(history, monkeypatch):
    c = _client(history, monkeypatch)
    with c:
        yield c
    from src.api.app import app
    app.dependency_overrides.clear()


class TestAdminGate:
    def test_non_admin_gets_403(self, history, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        from src.api.app import app
        from src.api import deps

        app.dependency_overrides[deps.get_scan_history_store] = lambda: history
        try:
            with TestClient(app) as c:
                resp = c.get("/api/scans/history")
                assert resp.status_code == 403
        finally:
            app.dependency_overrides.clear()

    def test_admin_with_token_succeeds(self, history, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        from src.api.app import app
        from src.api import deps

        app.dependency_overrides[deps.get_scan_history_store] = lambda: history
        try:
            with TestClient(app) as c:
                resp = c.get(
                    "/api/scans/history", headers={"X-Admin-Token": "secret"},
                )
                assert resp.status_code == 200
        finally:
            app.dependency_overrides.clear()

    def test_local_open_mode_counts_as_admin(self, client):
        resp = client.get("/api/scans/history")
        assert resp.status_code == 200


class TestShape:
    def test_response_shape(self, client):
        resp = client.get("/api/scans/history")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"scans", "total", "limit", "offset"}
        assert data["total"] == 2
        assert data["limit"] == 100
        assert data["offset"] == 0
        assert len(data["scans"]) == 2

    def test_newest_first(self, client):
        resp = client.get("/api/scans/history")
        ids = [s["scan_id"] for s in resp.json()["scans"]]
        assert ids == ["s2", "s1"]

    def test_scan_row_fields(self, client):
        resp = client.get("/api/scans/history")
        row = next(s for s in resp.json()["scans"] if s["scan_id"] == "s1")
        assert row["domain_group"] == "quick"
        assert row["mode"] == "standard"
        assert row["channels"] == ["crawl"]
        assert row["status"] == "completed"
        assert row["domains_scanned"] == 5
        assert row["policies_found"] == 2
        assert row["cost_usd"] == 0.5


class TestFilters:
    def test_filter_by_domain_group(self, client):
        resp = client.get("/api/scans/history", params={"domain_group": "eu"})
        data = resp.json()
        assert data["total"] == 1
        assert data["scans"][0]["scan_id"] == "s2"

    def test_filter_by_status(self, client):
        resp = client.get("/api/scans/history", params={"status": "failed"})
        data = resp.json()
        assert data["total"] == 1
        assert data["scans"][0]["scan_id"] == "s2"

    def test_no_match_returns_empty(self, client):
        resp = client.get("/api/scans/history", params={"domain_group": "nonexistent"})
        data = resp.json()
        assert data["total"] == 0
        assert data["scans"] == []


class TestPagination:
    def test_default_limit_100(self, client):
        resp = client.get("/api/scans/history")
        assert resp.json()["limit"] == 100

    def test_limit_capped_at_500(self, client):
        resp = client.get("/api/scans/history", params={"limit": 1000})
        assert resp.status_code == 422

    def test_custom_limit_and_offset(self, client):
        resp = client.get("/api/scans/history", params={"limit": 1, "offset": 1})
        data = resp.json()
        assert data["limit"] == 1
        assert data["offset"] == 1
        assert data["total"] == 2
        assert len(data["scans"]) == 1
        assert data["scans"][0]["scan_id"] == "s1"
