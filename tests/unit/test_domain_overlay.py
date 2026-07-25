"""Tests for the domain-enabled overlay seam (WP-8 foundation for WP-9).

``apply_domain_overrides`` (src/core/overrides.py) is the pure function
applied at four call sites — ScanManager.start_scan, ScanManager.estimate_cost,
GET /api/domains?group=..., and GET /api/coverage (+ /children, /unresolved) —
so a domain disabled via the overlay is excluded from scanning, cost
estimation, and the map's source counts, without ConfigLoader itself knowing
overrides exist.
"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.core.overrides import apply_domain_overrides
from src.orchestration.scan_manager import ScanManager
from src.storage.domain_overrides import DomainOverridesStore


# ---------------------------------------------------------------------------
# Pure function
# ---------------------------------------------------------------------------

class TestApplyDomainOverrides:
    def test_no_overrides_passes_through_unchanged(self):
        domains = [{"id": "a"}, {"id": "b"}]
        assert apply_domain_overrides(domains, {}) == domains

    def test_override_disabled_removes_domain(self):
        domains = [{"id": "a"}, {"id": "b"}]
        result = apply_domain_overrides(domains, {"a": {"enabled": False}})
        assert [d["id"] for d in result] == ["b"]

    def test_override_enabled_true_keeps_domain(self):
        domains = [{"id": "a"}]
        result = apply_domain_overrides(domains, {"a": {"enabled": True}})
        assert [d["id"] for d in result] == ["a"]

    def test_override_for_unrelated_domain_is_ignored(self):
        domains = [{"id": "a"}]
        result = apply_domain_overrides(domains, {"other": {"enabled": False}})
        assert [d["id"] for d in result] == ["a"]

    def test_does_not_mutate_input_list(self):
        domains = [{"id": "a"}, {"id": "b"}]
        original = list(domains)
        apply_domain_overrides(domains, {"a": {"enabled": False}})
        assert domains == original


# ---------------------------------------------------------------------------
# ScanManager integration
# ---------------------------------------------------------------------------

def _manager_with_domains(domains, overrides_store=None):
    config = MagicMock()
    config.get_enabled_domains.return_value = domains
    settings = MagicMock()
    settings.crawl.max_pages_per_domain = 200
    settings.analysis.min_keyword_score = 3.0
    config.settings = settings
    return ScanManager(
        config=config, broadcaster=MagicMock(), domain_overrides_store=overrides_store,
    )


class TestScanManagerNoOverridesStore:
    """Backward compatibility: omitting domain_overrides_store (as every
    existing ScanManager test does) must not touch the filesystem or change
    behavior at all."""

    @pytest.mark.asyncio
    async def test_start_scan_unaffected_without_store(self):
        domains = [{"id": "d1", "name": "D1"}]
        manager = _manager_with_domains(domains)
        job = await manager.start_scan(dry_run=True)
        assert job.domain_count == 1

    def test_estimate_cost_unaffected_without_store(self):
        domains = [{"id": "d1", "name": "D1"}]
        manager = _manager_with_domains(domains)
        result = manager.estimate_cost("quick")
        assert result["domain_count"] == 1


class TestScanManagerWithOverridesStore:
    @pytest.mark.asyncio
    async def test_start_scan_excludes_disabled_domain(self, tmp_path):
        store = DomainOverridesStore(data_dir=str(tmp_path))
        store.set_enabled("d2", False)
        domains = [{"id": "d1", "name": "D1"}, {"id": "d2", "name": "D2"}]
        manager = _manager_with_domains(domains, overrides_store=store)

        job = await manager.start_scan(dry_run=True)

        assert job.domain_count == 1
        assert [dp.domain_id for dp in job.progress.domains] == ["d1"]

    def test_estimate_cost_excludes_disabled_domain(self, tmp_path):
        store = DomainOverridesStore(data_dir=str(tmp_path))
        store.set_enabled("d2", False)
        domains = [{"id": "d1", "name": "D1"}, {"id": "d2", "name": "D2"}]
        manager = _manager_with_domains(domains, overrides_store=store)

        result = manager.estimate_cost("quick")

        assert result["domain_count"] == 1


# ---------------------------------------------------------------------------
# GET /api/domains route
# ---------------------------------------------------------------------------

@pytest.fixture
def domains_client(monkeypatch, tmp_path):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    from src.api.app import app
    from src.api import deps

    config = MagicMock()
    config.get_enabled_domains.return_value = [
        {"id": "d1", "name": "D1"}, {"id": "d2", "name": "D2"},
    ]
    store = DomainOverridesStore(data_dir=str(tmp_path))

    app.dependency_overrides[deps.get_config] = lambda: config
    app.dependency_overrides[deps.get_domain_overrides_store] = lambda: store
    with TestClient(app) as c:
        yield c, store
    app.dependency_overrides.clear()


class TestDomainsRouteOverlay:
    def test_group_listing_excludes_overridden_domain(self, domains_client):
        client, store = domains_client
        store.set_enabled("d2", False)

        resp = client.get("/api/domains?group=quick")

        assert resp.status_code == 200
        ids = [d["id"] for d in resp.json()["domains"]]
        assert ids == ["d1"]

    def test_group_listing_unaffected_without_override(self, domains_client):
        client, _store = domains_client
        resp = client.get("/api/domains?group=quick")
        ids = [d["id"] for d in resp.json()["domains"]]
        assert ids == ["d1", "d2"]


# ---------------------------------------------------------------------------
# GET /api/coverage route
# ---------------------------------------------------------------------------

@pytest.fixture
def coverage_client(monkeypatch, tmp_path):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    from src.api.app import app
    from src.api import deps

    config = MagicMock()
    config.get_enabled_domains.return_value = [
        {"id": "d1", "name": "D1", "region": ["sweden"]},
        {"id": "d2", "name": "D2", "region": ["sweden"]},
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
    with TestClient(app) as c:
        yield c, store
    app.dependency_overrides.clear()


class TestCoverageRouteOverlay:
    def test_disabled_domain_excluded_from_source_totals(self, coverage_client):
        client, store = coverage_client
        store.set_enabled("d2", False)

        resp = client.get("/api/coverage")

        assert resp.status_code == 200
        assert resp.json()["totals"]["sources"] == 1

    def test_unaffected_without_override(self, coverage_client):
        client, _store = coverage_client
        resp = client.get("/api/coverage")
        assert resp.json()["totals"]["sources"] == 2
