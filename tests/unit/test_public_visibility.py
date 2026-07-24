"""Tests for the public review visibility posture (src/storage/public_visibility.py)
and its settings routes/health field (WP-3).

Mirrors src/storage/cost_settings.py's store pattern, but persists to the
shared SQLite kv table (see src/storage/db.py) rather than its own JSON file.
"""

import pytest
from fastapi.testclient import TestClient

from src.storage.public_visibility import (
    PublicVisibilitySettings,
    PublicVisibilityStore,
)


class TestPublicVisibilitySettingsModel:
    def test_default_mode_is_default_all(self):
        assert PublicVisibilitySettings().mode == "default_all"

    def test_invalid_mode_rejected(self):
        with pytest.raises(ValueError):
            PublicVisibilitySettings(mode="hide_everything")

    @pytest.mark.parametrize("mode", ["default_all", "default_reviewed", "reviewed_only"])
    def test_every_valid_mode_accepted(self, mode):
        assert PublicVisibilitySettings(mode=mode).mode == mode


class TestPublicVisibilityStore:
    def test_fresh_store_yields_default(self, tmp_path):
        store = PublicVisibilityStore(data_dir=str(tmp_path))
        assert store.get().mode == "default_all"

    def test_update_persists_in_kv_table(self, tmp_path):
        store = PublicVisibilityStore(data_dir=str(tmp_path))
        store.update(PublicVisibilitySettings(mode="reviewed_only"))
        assert store.get().mode == "reviewed_only"

    def test_persists_across_fresh_store_instance(self, tmp_path):
        store = PublicVisibilityStore(data_dir=str(tmp_path))
        store.update(PublicVisibilitySettings(mode="default_reviewed"))

        reloaded = PublicVisibilityStore(data_dir=str(tmp_path))
        assert reloaded.get().mode == "default_reviewed"

    def test_stored_in_kv_table_under_expected_name(self, tmp_path):
        from src.storage import db as storage_db

        store = PublicVisibilityStore(data_dir=str(tmp_path))
        store.update(PublicVisibilitySettings(mode="reviewed_only"))

        conn = storage_db.connect(str(tmp_path))
        raw = storage_db.kv_get(conn, "public_visibility")
        assert raw == {"mode": "reviewed_only"}


@pytest.fixture
def visibility_store(tmp_path):
    return PublicVisibilityStore(data_dir=str(tmp_path))


@pytest.fixture
def client(visibility_store, monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)

    from src.api.app import app
    from src.api import deps

    app.dependency_overrides[deps.get_public_visibility_store] = lambda: visibility_store
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestGetPublicVisibility:
    def test_default(self, client):
        resp = client.get("/api/settings/public-visibility")
        assert resp.status_code == 200
        assert resp.json() == {"mode": "default_all"}

    def test_reflects_updated_value(self, client, visibility_store):
        visibility_store.update(PublicVisibilitySettings(mode="reviewed_only"))
        resp = client.get("/api/settings/public-visibility")
        assert resp.json() == {"mode": "reviewed_only"}


class TestPutPublicVisibility:
    @pytest.mark.parametrize("mode", ["default_all", "default_reviewed", "reviewed_only"])
    def test_put_each_valid_value(self, client, visibility_store, mode):
        resp = client.put("/api/settings/public-visibility", json={"mode": mode})
        assert resp.status_code == 200
        assert resp.json() == {"mode": mode}
        assert visibility_store.get().mode == mode

    def test_put_invalid_value_rejected(self, client):
        resp = client.put("/api/settings/public-visibility", json={"mode": "invisible"})
        assert resp.status_code == 422

    def test_put_missing_mode_rejected(self, client):
        resp = client.put("/api/settings/public-visibility", json={})
        assert resp.status_code == 422

    def test_requires_admin_token_when_gate_active(self, visibility_store, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        from src.api.app import app
        from src.api import deps

        app.dependency_overrides[deps.get_public_visibility_store] = lambda: visibility_store
        try:
            with TestClient(app) as c:
                denied = c.put("/api/settings/public-visibility", json={"mode": "reviewed_only"})
                allowed = c.put(
                    "/api/settings/public-visibility",
                    json={"mode": "reviewed_only"},
                    headers={"X-Admin-Token": "secret"},
                )
        finally:
            app.dependency_overrides.clear()
        assert denied.status_code == 401
        assert allowed.status_code == 200

    def test_get_stays_open_when_gate_active(self, visibility_store, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        from src.api.app import app
        from src.api import deps

        app.dependency_overrides[deps.get_public_visibility_store] = lambda: visibility_store
        try:
            with TestClient(app) as c:
                resp = c.get("/api/settings/public-visibility")
        finally:
            app.dependency_overrides.clear()
        assert resp.status_code == 200


class TestHealthCarriesMode:
    def test_health_reports_default(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["public_review_visibility"] == "default_all"

    def test_health_reports_updated_mode(self, client, visibility_store):
        visibility_store.update(PublicVisibilitySettings(mode="reviewed_only"))
        resp = client.get("/health")
        assert resp.json()["public_review_visibility"] == "reviewed_only"
