"""Tests for AdminGateMiddleware, especially the loopback-only fallback used
when ADMIN_TOKEN is unset (world-facing deploy safety: a public deploy that
forgot to set ADMIN_TOKEN must not let remote visitors run paid scans or
replace the stored API key)."""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.core.models import Policy, PolicyType
from src.storage.leads import LeadStore
from src.storage.store import PolicyStore


def _policy(url: str) -> Policy:
    return Policy(
        url=url,
        policy_name="Policy",
        jurisdiction="Sweden",
        policy_type=PolicyType.LAW,
        summary="s",
        relevance_score=7,
        review_status="new",
    )


@pytest.fixture
def store(tmp_path):
    s = PolicyStore(data_dir=str(tmp_path))
    s.add_policies([_policy("https://a.gov/1")])
    return s


@pytest.fixture
def lead_store(tmp_path):
    return LeadStore(data_dir=str(tmp_path))


@pytest.fixture(autouse=True)
def _overrides(store, lead_store, tmp_path, monkeypatch):
    from src.api import deps
    from src.api.app import app
    from src.api.routes import leads as tips_route

    # The tips limiter is a module singleton whose daily counter persists in
    # OCP_DATA_DIR (default: the repo's real data/). Without this reset, the
    # exempt-route tests here inherit whatever today's earlier runs consumed
    # of the 100/day cap and start failing with 429s mid-afternoon (L009:
    # verdict depending on untracked local state + the real clock).
    monkeypatch.setenv("OCP_DATA_DIR", str(tmp_path))
    tips_route.reset_tip_limits_for_tests(data_dir=str(tmp_path))

    manager = MagicMock()
    manager.get_all_policies.return_value = []
    app.dependency_overrides[deps.get_policy_store] = lambda: store
    app.dependency_overrides[deps.get_scan_manager] = lambda: manager
    app.dependency_overrides[deps.get_lead_store] = lambda: lead_store
    yield
    app.dependency_overrides.clear()


def _patch_review(client):
    return client.patch(
        "/api/policies/review",
        json={"url": "https://a.gov/1", "review_status": "reviewed"},
    )


class TestTokenGatedModeUnchanged:
    """ADMIN_TOKEN set: behavior is exactly as before this change."""

    def test_missing_token_rejected(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        from src.api.app import app

        with TestClient(app) as c:
            resp = _patch_review(c)
        assert resp.status_code == 401

    def test_correct_token_allowed(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        from src.api.app import app

        with TestClient(app) as c:
            resp = c.patch(
                "/api/policies/review",
                json={"url": "https://a.gov/1", "review_status": "reviewed"},
                headers={"X-Admin-Token": "secret"},
            )
        assert resp.status_code == 200

    def test_remote_client_with_correct_token_allowed(self, monkeypatch):
        """Token-gated mode never inspects client host — only the token."""
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        from src.api.app import app

        with TestClient(app, client=("203.0.113.9", 12345)) as c:
            resp = c.patch(
                "/api/policies/review",
                json={"url": "https://a.gov/1", "review_status": "reviewed"},
                headers={"X-Admin-Token": "secret"},
            )
        assert resp.status_code == 200


class TestUngatedModeLoopbackOnly:
    """ADMIN_TOKEN unset: non-GET /api requests are loopback-only."""

    def test_loopback_ipv4_allowed(self, monkeypatch):
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        from src.api.app import app

        with TestClient(app, client=("127.0.0.1", 12345)) as c:
            resp = _patch_review(c)
        assert resp.status_code == 200

    def test_loopback_ipv6_allowed(self, monkeypatch):
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        from src.api.app import app

        with TestClient(app, client=("::1", 12345)) as c:
            resp = _patch_review(c)
        assert resp.status_code == 200

    def test_default_testclient_host_is_treated_as_loopback(self, monkeypatch):
        """Starlette's TestClient reports host 'testclient' with no real
        socket; the rest of the unit test suite depends on this counting as
        a trusted local caller when ADMIN_TOKEN is unset."""
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        from src.api.app import app

        with TestClient(app) as c:
            resp = _patch_review(c)
        assert resp.status_code == 200

    def test_forwarded_request_rejected_even_from_loopback_peer(self, monkeypatch):
        """Behind a reverse proxy (Caddy), the TCP peer is the proxy on
        loopback while the real client is remote; the forwarded header is the
        tell. Without this, a public deploy that forgot ADMIN_TOKEN would
        trust 100% of proxied traffic."""
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        from src.api.app import app

        with TestClient(app, client=("127.0.0.1", 12345)) as c:
            resp = c.patch(
                "/api/policies/review",
                json={"url": "https://a.gov/1", "review_status": "reviewed"},
                headers={"X-Forwarded-For": "203.0.113.5"},
            )
        assert resp.status_code == 403
        assert "ADMIN_TOKEN" in resp.json()["detail"]

    def test_forwarded_exempt_route_stays_open(self, monkeypatch):
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        from src.api.app import app

        with TestClient(app, client=("127.0.0.1", 12345)) as c:
            resp = c.post(
                "/api/tips",
                json={"url": "https://8.8.8.8/heat-law"},
                headers={"X-Forwarded-For": "203.0.113.5"},
            )
        assert resp.status_code == 200

    def test_remote_client_rejected_with_403(self, monkeypatch):
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        from src.api.app import app

        with TestClient(app, client=("203.0.113.5", 12345)) as c:
            resp = _patch_review(c)
        assert resp.status_code == 403
        assert "ADMIN_TOKEN" in resp.json()["detail"]

    def test_exempt_routes_stay_open_for_remote_client(self, monkeypatch):
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        from src.api.app import app

        with TestClient(app, client=("203.0.113.5", 12345)) as c:
            resp = c.post("/api/tips", json={"url": "https://8.8.8.8/heat-law"})
        assert resp.status_code == 200

    def test_get_routes_stay_open_for_remote_client(self, monkeypatch):
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        from src.api.app import app

        with TestClient(app, client=("203.0.113.5", 12345)) as c:
            resp = c.get("/api/policies")
        assert resp.status_code == 200
