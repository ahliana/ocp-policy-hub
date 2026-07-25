"""Tests for GET /api/keywords and PUT /api/keywords/overrides (WP-10).

GET returns the merged (YAML + overlay) keyword config plus a separate
``overrides`` section so the UI can show what's custom. PUT validates
categories exist, languages are among the 20 keywords.yaml ships with, terms
are non-empty strings <=80 chars, and thresholds are within sane ranges.
"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.storage.keyword_overrides import KeywordOverridesStore


def _keywords_config():
    return {
        "keywords": {
            "subject": {
                "weight": 3.0,
                "description": "Core subject",
                "terms": {"en": ["waste heat"], "de": ["Abwärme"]},
            },
            "context": {
                "weight": 1.0,
                "description": "Context",
                "terms": {"en": ["data center"]},
            },
        },
        "thresholds": {"minimum_keyword_score": 5.0, "minimum_matches": 2},
        "exclusions": ["job opening"],
        "url_bonuses": {"gov_tld_bonus": 1.0},
        "stricter_requirements": {"required_combinations": {"enabled": True}},
    }


@pytest.fixture
def config_and_store(tmp_path):
    config = MagicMock()
    config.keywords_config = _keywords_config()
    store = KeywordOverridesStore(data_dir=str(tmp_path))
    return config, store


@pytest.fixture
def client(config_and_store, monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    config, store = config_and_store
    from src.api.app import app
    from src.api import deps

    app.dependency_overrides[deps.get_config] = lambda: config
    app.dependency_overrides[deps.get_keyword_overrides_store] = lambda: store
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /api/keywords
# ---------------------------------------------------------------------------

class TestGetKeywordsAdminGate:
    def test_local_open_mode_returns_200(self, client):
        resp = client.get("/api/keywords")
        assert resp.status_code == 200

    def test_remote_non_admin_gets_403(self, config_and_store, monkeypatch):
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        config, store = config_and_store
        from src.api.app import app
        from src.api import deps

        app.dependency_overrides[deps.get_config] = lambda: config
        app.dependency_overrides[deps.get_keyword_overrides_store] = lambda: store
        try:
            with TestClient(app, client=("203.0.113.5", 12345)) as c:
                resp = c.get("/api/keywords")
        finally:
            app.dependency_overrides.clear()
        assert resp.status_code == 403


class TestGetKeywordsShape:
    def test_response_shape(self, client):
        resp = client.get("/api/keywords")
        body = resp.json()
        assert set(body.keys()) == {
            "categories", "thresholds", "exclusions",
            "url_bonuses", "stricter_requirements", "overrides",
        }

    def test_categories_include_weight_description_terms(self, client):
        body = client.get("/api/keywords").json()
        subject = body["categories"]["subject"]
        assert subject["weight"] == 3.0
        assert subject["description"] == "Core subject"
        assert subject["terms"]["en"] == ["waste heat"]

    def test_url_bonuses_and_stricter_requirements_passthrough(self, client):
        body = client.get("/api/keywords").json()
        assert body["url_bonuses"] == {"gov_tld_bonus": 1.0}
        assert body["stricter_requirements"] == {"required_combinations": {"enabled": True}}

    def test_overrides_section_empty_by_default(self, client):
        body = client.get("/api/keywords").json()
        assert body["overrides"] == {"categories": {}, "thresholds": {}}

    def test_merged_view_reflects_stored_overrides(self, config_and_store, monkeypatch):
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        config, store = config_and_store
        store.update({
            "categories": {"subject": {"en": {"added": ["heat pump"], "removed": []}}},
            "thresholds": {"minimum_keyword_score": 2.0},
        })
        from src.api.app import app
        from src.api import deps

        app.dependency_overrides[deps.get_config] = lambda: config
        app.dependency_overrides[deps.get_keyword_overrides_store] = lambda: store
        try:
            with TestClient(app) as c:
                body = c.get("/api/keywords").json()
        finally:
            app.dependency_overrides.clear()

        assert body["categories"]["subject"]["terms"]["en"] == ["waste heat", "heat pump"]
        assert body["thresholds"]["minimum_keyword_score"] == 2.0
        assert body["overrides"]["categories"]["subject"]["en"]["added"] == ["heat pump"]


# ---------------------------------------------------------------------------
# PUT /api/keywords/overrides
# ---------------------------------------------------------------------------

class TestPutOverridesValid:
    def test_valid_added_term_persists(self, client, config_and_store):
        _config, store = config_and_store
        resp = client.put(
            "/api/keywords/overrides",
            json={
                "categories": {"subject": {"en": {"added": ["heat pump"], "removed": []}}},
                "thresholds": {},
            },
        )
        assert resp.status_code == 200
        assert store.get()["categories"]["subject"]["en"]["added"] == ["heat pump"]

    def test_valid_threshold_override_persists(self, client, config_and_store):
        _config, store = config_and_store
        resp = client.put(
            "/api/keywords/overrides",
            json={"categories": {}, "thresholds": {"minimum_keyword_score": 3.0, "minimum_matches": 1}},
        )
        assert resp.status_code == 200
        assert store.get()["thresholds"] == {"minimum_keyword_score": 3.0, "minimum_matches": 1}

    def test_empty_overrides_clears(self, client, config_and_store):
        _config, store = config_and_store
        store.update({
            "categories": {"subject": {"en": {"added": ["x"], "removed": []}}},
            "thresholds": {"minimum_matches": 1},
        })
        resp = client.put("/api/keywords/overrides", json={"categories": {}, "thresholds": {}})
        assert resp.status_code == 200
        assert store.get() == {"categories": {}, "thresholds": {}}


class TestPutOverridesValidationMatrix:
    def test_unknown_category_422(self, client):
        resp = client.put(
            "/api/keywords/overrides",
            json={"categories": {"bogus_cat": {"en": {"added": ["x"], "removed": []}}}, "thresholds": {}},
        )
        assert resp.status_code == 422

    def test_unknown_language_422(self, client):
        resp = client.put(
            "/api/keywords/overrides",
            json={"categories": {"subject": {"xx": {"added": ["x"], "removed": []}}}, "thresholds": {}},
        )
        assert resp.status_code == 422

    def test_empty_term_string_422(self, client):
        resp = client.put(
            "/api/keywords/overrides",
            json={"categories": {"subject": {"en": {"added": [""], "removed": []}}}, "thresholds": {}},
        )
        assert resp.status_code == 422

    def test_term_over_80_chars_422(self, client):
        resp = client.put(
            "/api/keywords/overrides",
            json={"categories": {"subject": {"en": {"added": ["x" * 81], "removed": []}}}, "thresholds": {}},
        )
        assert resp.status_code == 422

    def test_term_at_80_chars_ok(self, client):
        resp = client.put(
            "/api/keywords/overrides",
            json={"categories": {"subject": {"en": {"added": ["x" * 80], "removed": []}}}, "thresholds": {}},
        )
        assert resp.status_code == 200

    def test_score_over_50_422(self, client):
        resp = client.put(
            "/api/keywords/overrides",
            json={"categories": {}, "thresholds": {"minimum_keyword_score": 51}},
        )
        assert resp.status_code == 422

    def test_score_negative_422(self, client):
        resp = client.put(
            "/api/keywords/overrides",
            json={"categories": {}, "thresholds": {"minimum_keyword_score": -1}},
        )
        assert resp.status_code == 422

    def test_score_at_bounds_ok(self, client):
        resp = client.put(
            "/api/keywords/overrides",
            json={"categories": {}, "thresholds": {"minimum_keyword_score": 0}},
        )
        assert resp.status_code == 200
        resp = client.put(
            "/api/keywords/overrides",
            json={"categories": {}, "thresholds": {"minimum_keyword_score": 50}},
        )
        assert resp.status_code == 200

    def test_matches_over_20_422(self, client):
        resp = client.put(
            "/api/keywords/overrides",
            json={"categories": {}, "thresholds": {"minimum_matches": 21}},
        )
        assert resp.status_code == 422

    def test_matches_negative_422(self, client):
        resp = client.put(
            "/api/keywords/overrides",
            json={"categories": {}, "thresholds": {"minimum_matches": -1}},
        )
        assert resp.status_code == 422

    def test_matches_at_bounds_ok(self, client):
        resp = client.put(
            "/api/keywords/overrides",
            json={"categories": {}, "thresholds": {"minimum_matches": 20}},
        )
        assert resp.status_code == 200


class TestPutOverridesAdminGate:
    def test_gated_by_middleware_when_token_set(self, config_and_store, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        config, store = config_and_store
        from src.api.app import app
        from src.api import deps

        app.dependency_overrides[deps.get_config] = lambda: config
        app.dependency_overrides[deps.get_keyword_overrides_store] = lambda: store
        try:
            with TestClient(app) as c:
                denied = c.put("/api/keywords/overrides", json={"categories": {}, "thresholds": {}})
                allowed = c.put(
                    "/api/keywords/overrides", json={"categories": {}, "thresholds": {}},
                    headers={"X-Admin-Token": "secret"},
                )
        finally:
            app.dependency_overrides.clear()
        assert denied.status_code == 401
        assert allowed.status_code == 200
