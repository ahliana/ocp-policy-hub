"""Tests for GET /api/policies/library — the Library review view (WP-4).

An admin-only, persisted-record-only surface (no in-memory scan merge):
403 for non-admin callers, paginated shape with total/limit/offset,
filters (review_status/lifecycle_stage/jurisdiction) including rejected
policies for admins, and 422 on an unknown lifecycle_stage.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from src.core.models import Policy, PolicyType
from src.storage.store import PolicyStore


def _policy(url, name, jurisdiction="Sweden", review_status="new", lifecycle_stage="unknown",
            score=5):
    return Policy(
        url=url,
        policy_name=name,
        jurisdiction=jurisdiction,
        policy_type=PolicyType.LAW,
        summary="s",
        relevance_score=score,
        review_status=review_status,
        lifecycle_stage=lifecycle_stage,
    )


@pytest.fixture
def store(tmp_path):
    s = PolicyStore(data_dir=str(tmp_path))
    s.add_policies([
        _policy("https://a.gov/1", "Alpha", jurisdiction="Germany",
                review_status="new", lifecycle_stage="proposed", score=3),
        _policy("https://a.gov/2", "Beta", jurisdiction="France",
                review_status="promoted", lifecycle_stage="enacted", score=9),
        _policy("https://a.gov/3", "Gamma", jurisdiction="Germany",
                review_status="rejected", lifecycle_stage="unknown", score=1),
    ])
    return s


def _client(store, monkeypatch, admin_token=None):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    if admin_token:
        monkeypatch.setenv("ADMIN_TOKEN", admin_token)

    from src.api.app import app
    from src.api import deps

    manager = MagicMock()
    manager.get_all_policies.return_value = []
    app.dependency_overrides[deps.get_policy_store] = lambda: store
    app.dependency_overrides[deps.get_scan_manager] = lambda: manager
    return TestClient(app)


@pytest.fixture
def client(store, monkeypatch):
    c = _client(store, monkeypatch)
    with c:
        yield c
    from src.api.app import app
    app.dependency_overrides.clear()


class TestAdminGate:
    def test_non_admin_gets_403(self, store, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        from src.api.app import app
        from src.api import deps

        app.dependency_overrides[deps.get_policy_store] = lambda: store
        try:
            with TestClient(app) as c:
                resp = c.get("/api/policies/library")
                assert resp.status_code == 403
        finally:
            app.dependency_overrides.clear()

    def test_admin_with_token_succeeds(self, store, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        from src.api.app import app
        from src.api import deps

        app.dependency_overrides[deps.get_policy_store] = lambda: store
        try:
            with TestClient(app) as c:
                resp = c.get(
                    "/api/policies/library", headers={"X-Admin-Token": "secret"},
                )
                assert resp.status_code == 200
        finally:
            app.dependency_overrides.clear()

    def test_local_open_mode_counts_as_admin(self, client):
        # No ADMIN_TOKEN set: TestClient's "testclient" host is loopback-
        # trusted (see src/api/deps.py request_is_admin).
        resp = client.get("/api/policies/library")
        assert resp.status_code == 200


class TestShape:
    def test_response_shape(self, client):
        resp = client.get("/api/policies/library")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"policies", "total", "limit", "offset"}
        assert data["total"] == 3
        assert data["limit"] == 25
        assert data["offset"] == 0
        assert len(data["policies"]) == 3

    def test_admin_sees_rejected(self, client):
        resp = client.get("/api/policies/library")
        statuses = {p["review_status"] for p in resp.json()["policies"]}
        assert "rejected" in statuses

    def test_no_in_memory_merge(self, store, monkeypatch):
        """The Library is the persisted record only — ScanManager's
        in-memory results must not appear even if present."""
        from src.api.app import app
        from src.api import deps

        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        manager = MagicMock()
        manager.get_all_policies.return_value = [
            _policy("https://in-memory.gov/x", "InMemory Only"),
        ]
        app.dependency_overrides[deps.get_policy_store] = lambda: store
        app.dependency_overrides[deps.get_scan_manager] = lambda: manager
        try:
            with TestClient(app) as c:
                resp = c.get("/api/policies/library")
                urls = {p["url"] for p in resp.json()["policies"]}
                assert "https://in-memory.gov/x" not in urls
        finally:
            app.dependency_overrides.clear()


class TestFilters:
    def test_filter_by_review_status(self, client):
        resp = client.get("/api/policies/library", params={"review_status": "promoted"})
        data = resp.json()
        assert data["total"] == 1
        assert data["policies"][0]["url"] == "https://a.gov/2"

    def test_filter_by_lifecycle_stage(self, client):
        resp = client.get("/api/policies/library", params={"lifecycle_stage": "proposed"})
        data = resp.json()
        assert data["total"] == 1
        assert data["policies"][0]["url"] == "https://a.gov/1"

    def test_filter_by_jurisdiction_substring(self, client):
        resp = client.get("/api/policies/library", params={"jurisdiction": "german"})
        data = resp.json()
        assert data["total"] == 2

    def test_unknown_lifecycle_stage_422(self, client):
        resp = client.get("/api/policies/library", params={"lifecycle_stage": "bogus"})
        assert resp.status_code == 422


class TestPagination:
    def test_default_limit_25(self, client):
        resp = client.get("/api/policies/library")
        assert resp.json()["limit"] == 25

    def test_limit_capped_at_100(self, client):
        resp = client.get("/api/policies/library", params={"limit": 500})
        assert resp.status_code == 422

    def test_custom_limit_and_offset(self, client):
        resp = client.get(
            "/api/policies/library",
            params={"sort": "name", "limit": 1, "offset": 1},
        )
        data = resp.json()
        assert data["limit"] == 1
        assert data["offset"] == 1
        assert data["total"] == 3
        assert len(data["policies"]) == 1
        assert data["policies"][0]["policy_name"] == "Beta"


class TestSort:
    def test_sort_by_relevance_desc_default(self, client):
        resp = client.get("/api/policies/library", params={"sort": "relevance"})
        scores = [p["relevance_score"] for p in resp.json()["policies"]]
        assert scores == [9, 3, 1]
