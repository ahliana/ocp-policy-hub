"""Tests for POST /api/config/reload and the swappable config singleton (WP-8).

``get_config()`` used to be an ``@lru_cache`` singleton with no way to
rebuild it short of a process restart. It is now a holder that
``reload_config()`` can swap atomically: a successful reload replaces the
served ``ConfigLoader`` (and keeps the ``ScanManager`` singleton's ``config``
attribute in sync, since it captured the old instance at construction); a
YAML error leaves the previous config serving and raises so the route can
turn it into a 422 without losing the live config.
"""

import pytest
from fastapi.testclient import TestClient

from src.api import deps
from src.core.config import ConfigurationError


def _write_config(config_dir, *, max_depth=3, broken=False):
    config_dir.mkdir(parents=True, exist_ok=True)
    domains_dir = config_dir / "domains"
    domains_dir.mkdir(parents=True, exist_ok=True)

    if broken:
        (config_dir / "settings.yaml").write_text("crawl: [this is not a mapping\n", encoding="utf-8")
    else:
        (config_dir / "settings.yaml").write_text(
            f"crawl:\n  max_depth: {max_depth}\n", encoding="utf-8",
        )

    (domains_dir / "test.yaml").write_text(
        "domains:\n"
        "  - id: dom1\n"
        "    name: Dom1\n"
        "    base_url: https://a.gov\n",
        encoding="utf-8",
    )
    (config_dir / "groups.yaml").write_text(
        "groups:\n  quick:\n    description: Quick\n    domains: [dom1]\n",
        encoding="utf-8",
    )
    (config_dir / "keywords.yaml").write_text(
        "keywords:\n"
        "  subject:\n"
        "    weight: 3.0\n"
        "    terms:\n      en: [waste heat]\n"
        "thresholds:\n  minimum_keyword_score: 3.0\n  minimum_matches: 1\n",
        encoding="utf-8",
    )
    (config_dir / "url_filters.yaml").write_text("url_filters:\n  skip_paths: []\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _reset_deps_state():
    """The config/scan-manager singletons are process-lifetime state, same
    caveat @lru_cache always had — reset between tests so one test's reload
    can't leak into the next."""
    deps._config_state["instance"] = None
    deps._config_state["version"] = 0
    deps._scan_manager_state["instance"] = None
    yield
    deps._config_state["instance"] = None
    deps._config_state["version"] = 0
    deps._scan_manager_state["instance"] = None


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    directory = tmp_path / "config"
    _write_config(directory)
    monkeypatch.setenv("OCP_CONFIG_DIR", str(directory))
    return directory


# ---------------------------------------------------------------------------
# get_config() / reload_config() / get_config_version()
# ---------------------------------------------------------------------------

class TestGetConfigSingleton:
    def test_lazily_builds_from_ocp_config_dir(self, config_dir):
        config = deps.get_config()
        assert config.settings.crawl.max_depth == 3

    def test_returns_same_instance_on_repeat_calls(self, config_dir):
        assert deps.get_config() is deps.get_config()

    def test_first_build_sets_version_to_one(self, config_dir):
        deps.get_config()
        assert deps.get_config_version() == 1


class TestReloadConfigSuccess:
    def test_swaps_in_new_values(self, config_dir):
        deps.get_config()
        _write_config(config_dir, max_depth=7)

        reloaded = deps.reload_config()

        assert reloaded.settings.crawl.max_depth == 7
        assert deps.get_config().settings.crawl.max_depth == 7

    def test_bumps_config_version(self, config_dir):
        deps.get_config()
        assert deps.get_config_version() == 1
        deps.reload_config()
        assert deps.get_config_version() == 2

    def test_updates_existing_scan_manager_singleton_config(self, config_dir):
        old_config = deps.get_config()
        manager = deps.get_scan_manager()
        assert manager.config is old_config

        _write_config(config_dir, max_depth=9)
        new_config = deps.reload_config()

        assert manager.config is new_config
        assert manager.config.settings.crawl.max_depth == 9


class TestReloadConfigFailure:
    def test_broken_yaml_keeps_old_config_serving(self, config_dir):
        old_config = deps.get_config()
        _write_config(config_dir, broken=True)

        with pytest.raises(ConfigurationError):
            deps.reload_config()

        assert deps.get_config() is old_config

    def test_broken_yaml_does_not_bump_version(self, config_dir):
        deps.get_config()
        _write_config(config_dir, broken=True)

        with pytest.raises(ConfigurationError):
            deps.reload_config()

        assert deps.get_config_version() == 1

    def test_broken_yaml_leaves_scan_manager_config_untouched(self, config_dir):
        manager = deps.get_scan_manager()
        old_config = manager.config
        _write_config(config_dir, broken=True)

        with pytest.raises(ConfigurationError):
            deps.reload_config()

        assert manager.config is old_config


# ---------------------------------------------------------------------------
# POST /api/config/reload route
# ---------------------------------------------------------------------------

@pytest.fixture
def client(config_dir, monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    from src.api.app import app
    with TestClient(app) as c:
        yield c


class TestReloadRoute:
    def test_reload_returns_200_and_new_domain_count(self, client, config_dir):
        deps.get_config()
        (config_dir / "domains" / "extra.yaml").write_text(
            "domains:\n  - id: dom2\n    name: Dom2\n    base_url: https://b.gov\n",
            encoding="utf-8",
        )

        resp = client.post("/api/config/reload")

        assert resp.status_code == 200
        body = resp.json()
        assert body["reloaded"] is True
        assert body["domain_count"] == 2
        assert body["config_version"] == 2

    def test_broken_yaml_returns_422_with_message(self, client, config_dir):
        deps.get_config()
        _write_config(config_dir, broken=True)

        resp = client.post("/api/config/reload")

        assert resp.status_code == 422
        assert resp.json()["detail"]

    def test_requires_admin_token_when_gate_active(self, config_dir, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        from src.api.app import app

        with TestClient(app) as c:
            denied = c.post("/api/config/reload")
            allowed = c.post("/api/config/reload", headers={"X-Admin-Token": "secret"})

        assert denied.status_code == 401
        assert allowed.status_code == 200


# ---------------------------------------------------------------------------
# GET /health carries config_version
# ---------------------------------------------------------------------------

class TestHealthConfigVersion:
    def test_health_reports_version(self, client):
        deps.get_config()
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["config_version"] == 1

    def test_health_reflects_bump_after_reload(self, client, config_dir):
        deps.get_config()
        client.post("/api/config/reload")
        resp = client.get("/health")
        assert resp.json()["config_version"] == 2
