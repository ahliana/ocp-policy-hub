"""Tests for GET /api/signals/status (WP-43) - admin-only sweep status surface.

Mirrors tests/unit/test_scan_history_api.py's admin-gate pattern: a GET
request bypasses AdminGateMiddleware, so the route checks request_is_admin
itself.
"""

import pytest
from fastapi.testclient import TestClient

from src.storage.signals_status import FeedFailure, SignalsStatusStore, SweepSummary


@pytest.fixture
def status_store(tmp_path):
    return SignalsStatusStore(data_dir=str(tmp_path))


def _client(store, monkeypatch, admin_token=None):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    if admin_token:
        monkeypatch.setenv("ADMIN_TOKEN", admin_token)

    from src.api.app import app
    from src.api import deps

    app.dependency_overrides[deps.get_signals_status_store] = lambda: store
    return TestClient(app)


@pytest.fixture
def client(status_store, monkeypatch):
    c = _client(status_store, monkeypatch)
    with c:
        yield c
    from src.api.app import app
    app.dependency_overrides.clear()


class TestAdminGate:
    @pytest.mark.medium
    def test_non_admin_gets_403(self, status_store, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        from src.api.app import app
        from src.api import deps

        app.dependency_overrides[deps.get_signals_status_store] = lambda: status_store
        try:
            with TestClient(app) as c:
                resp = c.get("/api/signals/status")
                assert resp.status_code == 403
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.medium
    def test_admin_with_token_succeeds(self, status_store, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        from src.api.app import app
        from src.api import deps

        app.dependency_overrides[deps.get_signals_status_store] = lambda: status_store
        try:
            with TestClient(app) as c:
                resp = c.get("/api/signals/status", headers={"X-Admin-Token": "secret"})
                assert resp.status_code == 200
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.medium
    def test_local_open_mode_counts_as_admin(self, client):
        resp = client.get("/api/signals/status")
        assert resp.status_code == 200


class TestResponseShape:
    @pytest.mark.medium
    def test_empty_when_no_sweep_has_run(self, client):
        resp = client.get("/api/signals/status")
        assert resp.status_code == 200
        assert resp.json() == {}

    @pytest.mark.medium
    def test_returns_persisted_summary(self, client, status_store):
        status_store.record(SweepSummary(
            feeds_tried=3, feeds_ok=2, feeds_failed=1,
            items_found=8, items_kept=3, leads_added=2,
            failures=[FeedFailure(name="feed:DCD", detail="HTTP 404")],
        ))
        resp = client.get("/api/signals/status")
        data = resp.json()
        assert data["feeds_tried"] == 3
        assert data["feeds_ok"] == 2
        assert data["feeds_failed"] == 1
        assert data["leads_added"] == 2
        assert data["failures"] == [{"name": "feed:DCD", "detail": "HTTP 404"}]
        assert "ts" in data
