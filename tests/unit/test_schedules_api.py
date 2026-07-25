"""Tests for the /api/schedules routes (WP-11).

GET is admin-gated manually (it's a GET, so AdminGateMiddleware doesn't
cover it - same pattern as /api/cost-projection, /api/sources/status,
/api/scans/history). POST/PUT/DELETE/run-now are non-GET, so
AdminGateMiddleware covers those automatically; these tests exercise them
in the default (loopback-open) test mode along with their own validation.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.core.config import ConfigurationError
from src.core.models import ScanJob, ScanStatus
from src.storage.schedules import SchedulesStore


def _config():
    config = MagicMock()

    def get_enabled_domains(group):
        if group == "bogus-scope":
            raise ConfigurationError(f"Unknown group/region/domain: '{group}'.")
        return [{"id": "d1"}]

    config.get_enabled_domains.side_effect = get_enabled_domains
    return config


def _manager():
    manager = MagicMock()
    manager.estimate_cost.return_value = {"estimated_cost_usd": 2.0, "domain_count": 3}
    manager.jobs = {}
    manager.start_scan = AsyncMock(
        return_value=ScanJob(scan_id="scan-xyz", status=ScanStatus.RUNNING, domain_group="quick")
    )
    return manager


def _history():
    history = MagicMock()
    history.stats.return_value = {
        "runs": 0, "mean_cost_usd": None, "last_cost_usd": None, "mean_policies": None,
    }
    return history


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    from src.api.app import app
    from src.api import deps

    config = _config()
    manager = _manager()
    history = _history()
    store = SchedulesStore(data_dir=str(tmp_path))

    app.dependency_overrides[deps.get_config] = lambda: config
    app.dependency_overrides[deps.get_scan_manager] = lambda: manager
    app.dependency_overrides[deps.get_scan_history_store] = lambda: history
    app.dependency_overrides[deps.get_schedules_store] = lambda: store
    yield {"config": config, "manager": manager, "history": history, "store": store}
    app.dependency_overrides.clear()


@pytest.fixture
def client(env):
    from src.api.app import app
    with TestClient(app) as c:
        yield c


def _create_body(**overrides):
    body = {
        "name": "Monthly full scan",
        "domains": "all",
        "channels": ["crawl", "law_apis"],
        "deep": False,
        "cadence": "monthly:1:06:00",
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# GET /api/schedules - admin gate + shape
# ---------------------------------------------------------------------------

class TestListAdminGate:
    def test_non_admin_gets_403(self, env, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        from src.api.app import app
        with TestClient(app) as c:
            resp = c.get("/api/schedules")
            assert resp.status_code == 403

    def test_admin_with_token_succeeds(self, env, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        from src.api.app import app
        with TestClient(app) as c:
            resp = c.get("/api/schedules", headers={"X-Admin-Token": "secret"})
            assert resp.status_code == 200

    def test_local_open_mode_counts_as_admin(self, client):
        resp = client.get("/api/schedules")
        assert resp.status_code == 200


class TestListShape:
    def test_empty_list(self, client):
        resp = client.get("/api/schedules")
        assert resp.status_code == 200
        assert resp.json() == {"schedules": []}

    def test_includes_estimate_and_per_month_figures(self, client, env):
        env["store"].create(
            name="A", domains="quick", channels=["crawl"], deep=False,
            topic=None, cadence="weekly:0:06:00",
        )
        resp = client.get("/api/schedules")
        assert resp.status_code == 200
        rows = resp.json()["schedules"]
        assert len(rows) == 1
        row = rows[0]
        assert row["name"] == "A"
        assert row["estimate_usd"] == 2.0
        assert "per_month_usd" in row
        assert row["per_month_usd"] == pytest.approx(2.0 * (52 / 12), abs=0.01)

    def test_monthly_cadence_uses_monthly_runs_per_month(self, client, env):
        env["store"].create(
            name="A", domains="quick", channels=["crawl"], deep=False,
            topic=None, cadence="monthly:1:06:00",
        )
        resp = client.get("/api/schedules")
        row = resp.json()["schedules"][0]
        assert row["per_month_usd"] == 2.0

    def test_unresolvable_scope_row_returns_null_cost_not_500(self, client, env):
        # A schedule whose scope later becomes unresolvable (config edit /
        # reload) must not 500 the whole list - the admin still needs to see
        # and delete it (review finding).
        env["store"].create(
            name="Broken", domains="bogus-scope", channels=["crawl"], deep=False,
            topic=None, cadence="monthly:1:06:00",
        )
        env["manager"].estimate_cost.side_effect = ConfigurationError("gone")
        resp = client.get("/api/schedules")
        assert resp.status_code == 200
        row = resp.json()["schedules"][0]
        assert row["estimate_usd"] is None
        assert row["per_month_usd"] is None


# ---------------------------------------------------------------------------
# POST /api/schedules - create + validation
# ---------------------------------------------------------------------------

class TestCreate:
    def test_creates_schedule(self, client):
        resp = client.post("/api/schedules", json=_create_body())
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Monthly full scan"
        assert body["domains"] == "all"
        assert body["cadence"] == "monthly:1:06:00"
        assert body["enabled"] is True

    def test_blank_name_is_422(self, client):
        resp = client.post("/api/schedules", json=_create_body(name=""))
        assert resp.status_code == 422

    def test_bad_cadence_is_422(self, client):
        resp = client.post("/api/schedules", json=_create_body(cadence="daily:06:00"))
        assert resp.status_code == 422

    def test_bad_channel_is_422(self, client):
        resp = client.post("/api/schedules", json=_create_body(channels=["not-a-channel"]))
        assert resp.status_code == 422

    def test_empty_channels_is_422(self, client):
        # An explicit [] would persist as "no channels" but silently scan
        # crawl anyway at fire time - reject it (review finding).
        resp = client.post("/api/schedules", json=_create_body(channels=[]))
        assert resp.status_code == 422

    def test_omitted_channels_defaults_to_crawl(self, client):
        body = _create_body()
        del body["channels"]
        resp = client.post("/api/schedules", json=body)
        assert resp.status_code == 200
        assert resp.json()["channels"] == ["crawl"]

    def test_unknown_scope_is_400(self, client):
        resp = client.post("/api/schedules", json=_create_body(domains="bogus-scope"))
        assert resp.status_code == 400
        assert "bogus-scope" in resp.json()["detail"]

    def test_ceiling_persisted(self, client):
        resp = client.post("/api/schedules", json=_create_body(monthly_ceiling_usd=15.0))
        assert resp.json()["monthly_ceiling_usd"] == 15.0


# ---------------------------------------------------------------------------
# PUT /api/schedules/{id} - update
# ---------------------------------------------------------------------------

class TestUpdate:
    def test_missing_is_404(self, client):
        resp = client.put("/api/schedules/nonexistent", json={"name": "X"})
        assert resp.status_code == 404

    def test_partial_update(self, client):
        created = client.post("/api/schedules", json=_create_body()).json()
        resp = client.put(f"/api/schedules/{created['id']}", json={"name": "Renamed"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"
        assert resp.json()["domains"] == "all"

    def test_toggle_enabled(self, client):
        created = client.post("/api/schedules", json=_create_body()).json()
        resp = client.put(f"/api/schedules/{created['id']}", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    def test_bad_cadence_is_422(self, client):
        created = client.post("/api/schedules", json=_create_body()).json()
        resp = client.put(f"/api/schedules/{created['id']}", json={"cadence": "bogus"})
        assert resp.status_code == 422

    def test_unknown_scope_is_400(self, client):
        created = client.post("/api/schedules", json=_create_body()).json()
        resp = client.put(f"/api/schedules/{created['id']}", json={"domains": "bogus-scope"})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# DELETE /api/schedules/{id}
# ---------------------------------------------------------------------------

class TestDelete:
    def test_missing_is_404(self, client):
        resp = client.delete("/api/schedules/nonexistent")
        assert resp.status_code == 404

    def test_deletes(self, client):
        created = client.post("/api/schedules", json=_create_body()).json()
        resp = client.delete(f"/api/schedules/{created['id']}")
        assert resp.status_code == 200
        assert client.get("/api/schedules").json()["schedules"] == []


# ---------------------------------------------------------------------------
# POST /api/schedules/{id}/run-now
# ---------------------------------------------------------------------------

class TestRunNow:
    def test_missing_is_404(self, client):
        resp = client.post("/api/schedules/nonexistent/run-now")
        assert resp.status_code == 404

    def test_fires_through_start_scan(self, client, env):
        created = client.post("/api/schedules", json=_create_body(domains="quick")).json()
        resp = client.post(f"/api/schedules/{created['id']}/run-now")
        assert resp.status_code == 200
        env["manager"].start_scan.assert_awaited_once()
        assert resp.json()["last_scan_id"] == "scan-xyz"

    def test_respects_ceiling(self, client, env):
        created = client.post(
            "/api/schedules", json=_create_body(domains="quick", monthly_ceiling_usd=10.0),
        ).json()
        # completed_at must fall in the *current* UTC calendar month, since
        # run-now checks month_spend() against the real current time.
        completed_at = datetime.utcnow().replace(day=1).isoformat()
        env["store"]._conn.execute(
            "INSERT INTO scans (scan_id, domain_group, status, completed_at, cost_usd) "
            "VALUES (?, ?, 'completed', ?, ?)",
            ("s1", "quick", completed_at, 20.0),
        )
        env["store"]._conn.commit()

        resp = client.post(f"/api/schedules/{created['id']}/run-now")
        assert resp.status_code == 200
        env["manager"].start_scan.assert_not_awaited()
        body = resp.json()
        assert body["last_scan_id"] is None
        assert "monthly ceiling reached" in body["paused_reason"]
