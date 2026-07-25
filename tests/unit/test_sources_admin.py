"""Tests for GET /api/sources/status and PUT /api/sources/{id}/enabled (WP-9).

Admin-only visibility into every configured domain: YAML-enabled state, the
WP-8 enabled overlay, and (for structured connectors) whether their required
API key env var is set - reusing src/sources/check.py's source_key_status()
rather than duplicating key-readiness logic. No key values ever appear in
the response.
"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.routes.sources_admin import build_source_rows
from src.storage.domain_overrides import DomainOverridesStore


# ---------------------------------------------------------------------------
# Pure function: build_source_rows
# ---------------------------------------------------------------------------

class TestBuildSourceRows:
    def test_crawl_domain_has_no_key_status(self):
        domains = [{"id": "d1", "name": "D1", "region": ["sweden"]}]
        rows = build_source_rows(domains, {})
        assert rows[0]["key_status"] is None
        assert rows[0]["type"] == "crawl"

    def test_keyless_connector_reports_ready_not_missing(self, monkeypatch):
        # 18 of the 23 connectors need no key at all - "configured: False"
        # for them would read as 18 bogus missing-key badges in the UI.
        from src.api.routes import sources_admin

        monkeypatch.setattr(
            sources_admin, "source_key_status",
            lambda: [{"id": "riksdagen", "api_key_env": None, "key_present": False, "ready": True}],
        )
        domains = [{"id": "d1", "name": "D1", "source_type": "riksdagen"}]
        rows = build_source_rows(domains, {})
        assert rows[0]["key_status"] == {"required_env": None, "configured": True}

    def test_connector_missing_key_reported(self, monkeypatch):
        from src.api.routes import sources_admin

        monkeypatch.setattr(
            sources_admin, "source_key_status",
            lambda: [{
                "id": "legiscan", "api_key_env": "LEGISCAN_API_KEY",
                "key_present": False, "ready": False,
            }],
        )
        domains = [{"id": "d1", "name": "D1", "source_type": "legiscan"}]
        rows = build_source_rows(domains, {})
        assert rows[0]["key_status"] == {"required_env": "LEGISCAN_API_KEY", "configured": False}

    def test_no_key_values_leak_into_row(self, monkeypatch):
        """Only the env var NAME and a bool, never a value."""
        from src.api.routes import sources_admin

        monkeypatch.setattr(
            sources_admin, "source_key_status",
            lambda: [{
                "id": "legiscan", "api_key_env": "LEGISCAN_API_KEY",
                "key_present": True, "ready": True,
            }],
        )
        domains = [{"id": "d1", "name": "D1", "source_type": "legiscan"}]
        rows = build_source_rows(domains, {})
        assert "sk-" not in str(rows)
        assert rows[0]["key_status"]["configured"] is True

    def test_enabled_in_yaml_defaults_true(self):
        rows = build_source_rows([{"id": "d1", "name": "D1"}], {})
        assert rows[0]["enabled_in_yaml"] is True
        assert rows[0]["enabled_override"] is None
        assert rows[0]["effective_enabled"] is True

    def test_yaml_disabled_domain_is_effective_disabled(self):
        rows = build_source_rows([{"id": "d1", "name": "D1", "enabled": False}], {})
        assert rows[0]["enabled_in_yaml"] is False
        assert rows[0]["effective_enabled"] is False

    def test_override_false_makes_effective_disabled(self):
        rows = build_source_rows(
            [{"id": "d1", "name": "D1"}], {"d1": {"enabled": False}},
        )
        assert rows[0]["enabled_override"] is False
        assert rows[0]["effective_enabled"] is False

    def test_override_true_shows_but_yaml_still_wins_if_disabled(self):
        rows = build_source_rows(
            [{"id": "d1", "name": "D1", "enabled": False}], {"d1": {"enabled": True}},
        )
        assert rows[0]["enabled_override"] is True
        assert rows[0]["effective_enabled"] is False

    def test_region_and_name_passed_through(self):
        rows = build_source_rows(
            [{"id": "d1", "name": "D1", "region": ["sweden", "eu"]}], {},
        )
        assert rows[0]["region"] == ["sweden", "eu"]
        assert rows[0]["name"] == "D1"


# ---------------------------------------------------------------------------
# GET /api/sources/status route
# ---------------------------------------------------------------------------

def _config_with_domains(domains):
    config = MagicMock()
    config.domains_config = {"domains": domains}
    return config


@pytest.fixture
def client_and_store(monkeypatch, tmp_path):
    from src.api.app import app
    from src.api import deps

    config = _config_with_domains([
        {"id": "d1", "name": "D1", "region": ["sweden"]},
        {"id": "d2", "name": "D2", "region": ["us"], "source_type": "legiscan"},
    ])
    store = DomainOverridesStore(data_dir=str(tmp_path))

    app.dependency_overrides[deps.get_config] = lambda: config
    app.dependency_overrides[deps.get_domain_overrides_store] = lambda: store
    yield app, config, store
    app.dependency_overrides.clear()


class TestStatusRouteAdminGate:
    def test_local_open_mode_returns_200(self, client_and_store, monkeypatch):
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        app, _config, _store = client_and_store
        with TestClient(app) as c:
            resp = c.get("/api/sources/status")
        assert resp.status_code == 200

    def test_remote_non_admin_gets_403(self, client_and_store, monkeypatch):
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        app, _config, _store = client_and_store
        with TestClient(app, client=("203.0.113.5", 12345)) as c:
            resp = c.get("/api/sources/status")
        assert resp.status_code == 403

    def test_token_gated_requires_token(self, client_and_store, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        app, _config, _store = client_and_store
        with TestClient(app) as c:
            denied = c.get("/api/sources/status")
            allowed = c.get("/api/sources/status", headers={"X-Admin-Token": "secret"})
        assert denied.status_code == 403
        assert allowed.status_code == 200


class TestStatusRouteShape:
    def test_response_shape(self, client_and_store, monkeypatch):
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        app, _config, _store = client_and_store
        with TestClient(app) as c:
            resp = c.get("/api/sources/status")
        body = resp.json()
        assert set(body.keys()) == {"sources", "count"}
        assert body["count"] == 2
        row = next(r for r in body["sources"] if r["id"] == "d1")
        assert set(row.keys()) == {
            "id", "name", "type", "region", "enabled_in_yaml",
            "enabled_override", "effective_enabled", "key_status",
        }

    def test_key_status_derived_not_hardcoded(self, client_and_store, monkeypatch):
        """Row for a connector reflects the real SOURCE_REGISTRY entry's
        api_key_env, not a value baked into the route."""
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        app, _config, _store = client_and_store
        with TestClient(app) as c:
            resp = c.get("/api/sources/status")
        row = next(r for r in resp.json()["sources"] if r["id"] == "d2")
        assert row["key_status"]["required_env"] == "LEGISCAN_API_KEY"


# ---------------------------------------------------------------------------
# PUT /api/sources/{id}/enabled
# ---------------------------------------------------------------------------

class TestPutEnabled:
    def test_set_false_persists_override(self, client_and_store, monkeypatch):
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        app, _config, store = client_and_store
        with TestClient(app) as c:
            resp = c.put("/api/sources/d1/enabled", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json() == {"id": "d1", "enabled_override": False}
        assert store.get("d1") == {"enabled": False}

    def test_set_true_persists_override(self, client_and_store, monkeypatch):
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        app, _config, store = client_and_store
        with TestClient(app) as c:
            c.put("/api/sources/d1/enabled", json={"enabled": True})
        assert store.get("d1") == {"enabled": True}

    def test_set_null_clears_override(self, client_and_store, monkeypatch):
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        app, _config, store = client_and_store
        store.set_enabled("d1", False)
        with TestClient(app) as c:
            resp = c.put("/api/sources/d1/enabled", json={"enabled": None})
        assert resp.status_code == 200
        assert store.get("d1") is None

    def test_unknown_domain_404(self, client_and_store, monkeypatch):
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        app, _config, _store = client_and_store
        with TestClient(app) as c:
            resp = c.put("/api/sources/nope/enabled", json={"enabled": False})
        assert resp.status_code == 404

    def test_gated_by_middleware_when_token_set(self, client_and_store, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        app, _config, _store = client_and_store
        with TestClient(app) as c:
            denied = c.put("/api/sources/d1/enabled", json={"enabled": False})
            allowed = c.put(
                "/api/sources/d1/enabled", json={"enabled": False},
                headers={"X-Admin-Token": "secret"},
            )
        assert denied.status_code == 401
        assert allowed.status_code == 200


# ---------------------------------------------------------------------------
# Overlay integration: PUT disable -> excluded from /api/domains and coverage
# ---------------------------------------------------------------------------

class TestOverlayIntegration:
    def test_disabling_via_put_excludes_from_domains_group_listing(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        from src.api.app import app
        from src.api import deps

        config = MagicMock()
        config.domains_config = {"domains": [{"id": "d1", "name": "D1"}]}
        config.get_enabled_domains.return_value = [{"id": "d1", "name": "D1"}]
        store = DomainOverridesStore(data_dir=str(tmp_path))

        app.dependency_overrides[deps.get_config] = lambda: config
        app.dependency_overrides[deps.get_domain_overrides_store] = lambda: store
        try:
            with TestClient(app) as c:
                c.put("/api/sources/d1/enabled", json={"enabled": False})
                resp = c.get("/api/domains?group=quick")
        finally:
            app.dependency_overrides.clear()

        assert resp.json()["domains"] == []

    def test_disabling_via_put_excludes_from_coverage_sources(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        from src.api.app import app
        from src.api import deps

        config = MagicMock()
        config.domains_config = {"domains": [{"id": "d1", "name": "D1", "region": ["sweden"]}]}
        config.get_enabled_domains.return_value = [
            {"id": "d1", "name": "D1", "region": ["sweden"]},
        ]
        store = DomainOverridesStore(data_dir=str(tmp_path))
        policy_store = MagicMock()
        policy_store.get_all.return_value = []
        manager = MagicMock()
        manager.get_all_policies.return_value = []
        visibility_store = MagicMock()
        visibility_store.get.return_value = MagicMock(mode="default_all")

        app.dependency_overrides[deps.get_config] = lambda: config
        app.dependency_overrides[deps.get_domain_overrides_store] = lambda: store
        app.dependency_overrides[deps.get_policy_store] = lambda: policy_store
        app.dependency_overrides[deps.get_scan_manager] = lambda: manager
        app.dependency_overrides[deps.get_public_visibility_store] = lambda: visibility_store
        try:
            with TestClient(app) as c:
                c.put("/api/sources/d1/enabled", json={"enabled": False})
                resp = c.get("/api/coverage")
        finally:
            app.dependency_overrides.clear()

        assert resp.json()["totals"]["sources"] == 0
