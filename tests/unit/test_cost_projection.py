"""Tests for GET /api/cost-projection (WP-7).

Projection math (the blend rule + cadence multipliers) as pure-function
unit tests, plus route tests for the admin gate, response shape, and the
400 on an unknown scope.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from src.api.routes.cost_projection import RUNS_PER_MONTH, _project_group
from src.core.config import ConfigurationError


# ---------------------------------------------------------------------------
# Projection math
# ---------------------------------------------------------------------------

class TestCadenceMultipliers:
    def test_monthly_is_one_run_per_month(self):
        assert RUNS_PER_MONTH["monthly"] == 1.0

    def test_weekly_is_roughly_4_33_runs_per_month(self):
        assert RUNS_PER_MONTH["weekly"] == pytest.approx(4.33)

    def test_quarterly_is_a_third_of_a_run_per_month(self):
        assert RUNS_PER_MONTH["quarterly"] == pytest.approx(1 / 3)


class TestProjectGroupEstimateOnly:
    """0 or 1 completed runs: not enough signal to trust an average, so the
    static estimate_cost() figure is used for per_month_usd."""

    def test_no_history_uses_estimate_and_history_is_none(self):
        estimate = {"estimated_cost_usd": 2.0}
        stats = {"runs": 0, "mean_cost_usd": None, "last_cost_usd": None, "mean_policies": None}

        result = _project_group("quick", estimate, stats, runs_per_month=1.0)

        assert result["group"] == "quick"
        assert result["estimate_usd"] == 2.0
        assert result["history"] is None
        assert result["per_month_usd"] == 2.0

    def test_single_completed_run_still_uses_estimate_but_surfaces_history(self):
        estimate = {"estimated_cost_usd": 2.0}
        stats = {"runs": 1, "mean_cost_usd": 5.0, "last_cost_usd": 5.0, "mean_policies": 3.0}

        result = _project_group("quick", estimate, stats, runs_per_month=1.0)

        # per_month_usd still comes from the estimate (2.0), not the single
        # actual run (5.0) - one data point isn't a trustworthy average.
        assert result["per_month_usd"] == 2.0
        assert result["history"] == {"runs": 1, "mean_cost_usd": 5.0, "last_cost_usd": 5.0}


class TestProjectGroupActualsBlend:
    """>= 2 completed runs: the mean actual cost drives per_month_usd."""

    def test_two_runs_switches_to_actuals(self):
        estimate = {"estimated_cost_usd": 2.0}
        stats = {"runs": 2, "mean_cost_usd": 3.0, "last_cost_usd": 4.0, "mean_policies": 2.0}

        result = _project_group("quick", estimate, stats, runs_per_month=1.0)

        assert result["per_month_usd"] == 3.0
        assert result["history"]["runs"] == 2
        assert result["history"]["mean_cost_usd"] == 3.0
        assert result["history"]["last_cost_usd"] == 4.0

    def test_cadence_multiplies_the_chosen_per_run_cost(self):
        estimate = {"estimated_cost_usd": 2.0}
        stats = {"runs": 3, "mean_cost_usd": 3.0, "last_cost_usd": 4.0, "mean_policies": 2.0}

        weekly = _project_group("quick", estimate, stats, runs_per_month=RUNS_PER_MONTH["weekly"])
        quarterly = _project_group(
            "quick", estimate, stats, runs_per_month=RUNS_PER_MONTH["quarterly"],
        )

        assert weekly["per_month_usd"] == pytest.approx(3.0 * 4.33, abs=0.01)
        assert quarterly["per_month_usd"] == pytest.approx(1.0, abs=0.01)

    def test_history_excludes_mean_policies_field(self):
        """The response's `history` shape is exactly {runs, mean_cost_usd,
        last_cost_usd} - mean_policies is internal to stats(), not part of
        the documented cost-projection contract."""
        estimate = {"estimated_cost_usd": 1.0}
        stats = {"runs": 2, "mean_cost_usd": 1.0, "last_cost_usd": 1.0, "mean_policies": 9.0}

        result = _project_group("quick", estimate, stats, runs_per_month=1.0)

        assert set(result["history"].keys()) == {"runs", "mean_cost_usd", "last_cost_usd"}


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

def _manager(estimate_side_effect):
    manager = MagicMock()
    manager.estimate_cost.side_effect = estimate_side_effect
    return manager


def _history(stats_by_group=None):
    history = MagicMock()
    stats_by_group = stats_by_group or {}
    history.stats.side_effect = lambda group: stats_by_group.get(
        group, {"runs": 0, "mean_cost_usd": None, "last_cost_usd": None, "mean_policies": None},
    )
    return history


def _client(monkeypatch, manager, history, env_admin_token=None):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    if env_admin_token:
        monkeypatch.setenv("ADMIN_TOKEN", env_admin_token)

    from src.api.app import app
    from src.api import deps

    app.dependency_overrides[deps.get_scan_manager] = lambda: manager
    app.dependency_overrides[deps.get_scan_history_store] = lambda: history
    return TestClient(app)


@pytest.fixture
def client(monkeypatch):
    manager = _manager(lambda group, deep=False: {"estimated_cost_usd": 2.0, "domain_count": 5})
    history = _history({"quick": {"runs": 2, "mean_cost_usd": 3.0, "last_cost_usd": 4.0, "mean_policies": 1.0}})
    c = _client(monkeypatch, manager, history)
    with c:
        yield c
    from src.api.app import app
    app.dependency_overrides.clear()


class TestAdminGate:
    def test_non_admin_gets_403(self, monkeypatch):
        manager = _manager(lambda group, deep=False: {"estimated_cost_usd": 1.0})
        history = _history()
        c = _client(monkeypatch, manager, history, env_admin_token="secret")
        try:
            with c:
                resp = c.get("/api/cost-projection", params={"groups": "quick"})
                assert resp.status_code == 403
        finally:
            from src.api.app import app
            app.dependency_overrides.clear()

    def test_admin_with_token_succeeds(self, monkeypatch):
        manager = _manager(lambda group, deep=False: {"estimated_cost_usd": 1.0})
        history = _history()
        c = _client(monkeypatch, manager, history, env_admin_token="secret")
        try:
            with c:
                resp = c.get(
                    "/api/cost-projection",
                    params={"groups": "quick"},
                    headers={"X-Admin-Token": "secret"},
                )
                assert resp.status_code == 200
        finally:
            from src.api.app import app
            app.dependency_overrides.clear()

    def test_local_open_mode_counts_as_admin(self, client):
        resp = client.get("/api/cost-projection", params={"groups": "quick"})
        assert resp.status_code == 200


class TestShape:
    def test_response_shape(self, client):
        resp = client.get("/api/cost-projection", params={"groups": "quick"})
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"items", "cadence", "total_per_month_usd"}
        assert data["cadence"] == "monthly"
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert set(item.keys()) == {"group", "estimate_usd", "history", "per_month_usd"}
        assert item["group"] == "quick"
        assert item["history"]["runs"] == 2

    def test_multiple_groups(self, monkeypatch):
        manager = _manager(lambda group, deep=False: {"estimated_cost_usd": 1.0})
        history = _history()
        c = _client(monkeypatch, manager, history)
        try:
            with c:
                resp = c.get("/api/cost-projection", params={"groups": "quick,eu"})
                data = resp.json()
                assert [item["group"] for item in data["items"]] == ["quick", "eu"]
                assert data["total_per_month_usd"] == 2.0
        finally:
            from src.api.app import app
            app.dependency_overrides.clear()

    def test_cadence_param_reflected(self, client):
        resp = client.get(
            "/api/cost-projection", params={"groups": "quick", "cadence": "weekly"},
        )
        assert resp.json()["cadence"] == "weekly"

    def test_invalid_cadence_422(self, client):
        resp = client.get(
            "/api/cost-projection", params={"groups": "quick", "cadence": "yearly"},
        )
        assert resp.status_code == 422


class TestUnknownGroup:
    def test_unknown_group_400_names_the_group(self, monkeypatch):
        def side_effect(group, deep=False):
            raise ConfigurationError(f"Unknown group/region/domain: '{group}'.")

        manager = _manager(side_effect)
        history = _history()
        c = _client(monkeypatch, manager, history)
        try:
            with c:
                resp = c.get("/api/cost-projection", params={"groups": "bogus"})
                assert resp.status_code == 400
                assert "bogus" in resp.json()["detail"]
        finally:
            from src.api.app import app
            app.dependency_overrides.clear()

    def test_missing_groups_param_422(self, client):
        resp = client.get("/api/cost-projection")
        assert resp.status_code == 422

    def test_blank_groups_param_400(self, client):
        resp = client.get("/api/cost-projection", params={"groups": " , , "})
        assert resp.status_code == 400
