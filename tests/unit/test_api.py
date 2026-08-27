"""Tests for FastAPI API routes."""

from unittest.mock import MagicMock, AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.core.models import Policy, PolicyType, ScanJob, ScanStatus, ScanProgress


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.domains_config = {
        "domains": [
            {
                "id": "test_gov",
                "name": "Test Gov",
                "base_url": "https://test.gov",
                "enabled": True,
                "region": ["us"],
                "category": "government",
                "tags": ["energy"],
            },
        ],
    }
    config.list_domains.return_value = config.domains_config["domains"]
    config.get_enabled_domains.return_value = config.domains_config["domains"]
    config.list_groups.return_value = {"groups": {"quick": ["test_gov"]}}
    config.list_regions.return_value = ["us"]
    config.list_categories.return_value = ["government"]
    config.list_tags.return_value = ["energy"]
    return config


@pytest.fixture
def mock_store():
    store = MagicMock()
    store.get_all.return_value = []
    store.search.return_value = []
    store.get_stats.return_value = {
        "total": 0,
        "by_jurisdiction": {},
        "by_type": {},
        "by_score_range": {"1-3": 0, "4-6": 0, "7-8": 0, "9-10": 0},
        "flagged_count": 0,
    }
    return store


@pytest.fixture
def mock_manager():
    manager = MagicMock()
    manager.jobs = {}
    manager.get_all_policies.return_value = []
    manager.get_policies.return_value = []
    return manager


@pytest.fixture
def mock_broadcaster():
    return MagicMock()


@pytest.fixture
def client(mock_config, mock_store, mock_manager, mock_broadcaster):
    from src.api.app import app
    from src.api import deps

    app.dependency_overrides[deps.get_config] = lambda: mock_config
    app.dependency_overrides[deps.get_policy_store] = lambda: mock_store
    app.dependency_overrides[deps.get_scan_manager] = lambda: mock_manager
    app.dependency_overrides[deps.get_broadcaster] = lambda: mock_broadcaster

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


# --- Root & Health ---

class TestRootAndHealth:
    def test_root(self, client):
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "OCP CE HR Policy Searcher"
        assert "endpoints" in data

    def test_health(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


# --- Domains ---

class TestDomainRoutes:
    def test_list_domains(self, client):
        response = client.get("/api/domains")
        assert response.status_code == 200
        data = response.json()
        assert "domains" in data
        assert data["count"] == 1
        assert data["domains"][0]["id"] == "test_gov"

    def test_list_domains_with_group_filter(self, client, mock_config):
        response = client.get("/api/domains?group=quick")
        assert response.status_code == 200
        mock_config.get_enabled_domains.assert_called_with("quick")

    def test_list_domains_with_category_filter(self, client, mock_config):
        response = client.get("/api/domains?category=government")
        assert response.status_code == 200

    def test_list_groups(self, client):
        response = client.get("/api/groups")
        assert response.status_code == 200

    def test_list_regions(self, client):
        response = client.get("/api/regions")
        assert response.status_code == 200

    def test_list_categories(self, client):
        response = client.get("/api/categories")
        assert response.status_code == 200

    def test_list_tags(self, client):
        response = client.get("/api/tags")
        assert response.status_code == 200

    def test_get_domain_not_found(self, client):
        response = client.get("/api/domains/nonexistent_domain")
        assert response.status_code == 404
        data = response.json()
        assert "detail" in data
        assert "nonexistent_domain" in data["detail"]

    def test_list_domains_invalid_group(self, client, mock_config):
        from src.core.config import ConfigurationError
        mock_config.get_enabled_domains.side_effect = ConfigurationError(
            "Unknown group/region/domain: 'bogus'"
        )
        response = client.get("/api/domains?group=bogus")
        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "bogus" in data["detail"]


# --- Policies ---

class TestPolicyRoutes:
    def test_list_policies_empty(self, client):
        response = client.get("/api/policies")
        assert response.status_code == 200
        data = response.json()
        assert data["policies"] == []
        assert data["count"] == 0

    def test_list_policies_with_store_data(self, client, mock_store):
        mock_store.search.return_value = [
            {"url": "https://a.gov/p1", "policy_name": "P1", "jurisdiction": "US"},
        ]
        response = client.get("/api/policies")
        data = response.json()
        assert data["count"] == 1

    def test_list_policies_merges_in_memory(self, client, mock_manager):
        mock_manager.get_all_policies.return_value = [
            Policy(
                url="https://b.gov/p2",
                policy_name="P2",
                jurisdiction="DE",
                policy_type=PolicyType.LAW,
                summary="Test",
                relevance_score=7,
            )
        ]
        response = client.get("/api/policies")
        data = response.json()
        assert data["count"] == 1

    def test_list_policies_deduplicates(self, client, mock_store, mock_manager):
        mock_store.search.return_value = [
            {"url": "https://a.gov/p1", "policy_name": "P1"},
        ]
        mock_manager.get_all_policies.return_value = [
            Policy(
                url="https://a.gov/p1",
                policy_name="P1",
                jurisdiction="US",
                policy_type=PolicyType.LAW,
                summary="Test",
                relevance_score=7,
            )
        ]
        response = client.get("/api/policies")
        data = response.json()
        assert data["count"] == 1  # deduplicated

    def test_list_policies_filters(self, client):
        response = client.get("/api/policies?jurisdiction=US&min_score=5")
        assert response.status_code == 200

    def test_policy_stats(self, client):
        response = client.get("/api/policies/stats")
        assert response.status_code == 200
        data = response.json()
        assert "total" in data

    @pytest.mark.medium
    def test_policy_name_en_present_in_stored_record(self, client, mock_store):
        """Policies flow through as raw dicts (PolicyStore.search) - a stored
        record's policy_name_en must serialize untouched (WP-35)."""
        mock_store.search.return_value = [
            {
                "url": "https://a.gov/p1", "policy_name": "Energiewendegesetz",
                "policy_name_en": "Energy Transition Act", "jurisdiction": "Germany",
            },
        ]
        response = client.get("/api/policies")
        data = response.json()
        assert data["policies"][0]["policy_name_en"] == "Energy Transition Act"

    @pytest.mark.medium
    def test_policy_name_en_present_in_in_memory_record(self, client, mock_manager):
        """In-memory scan results serialize via Policy.model_dump - the new
        field must appear there too (WP-35)."""
        mock_manager.get_all_policies.return_value = [
            Policy(
                url="https://b.gov/p2",
                policy_name="Energiewendegesetz",
                policy_name_en="Energy Transition Act",
                jurisdiction="DE",
                policy_type=PolicyType.LAW,
                summary="Test",
                relevance_score=7,
            )
        ]
        response = client.get("/api/policies")
        data = response.json()
        assert data["policies"][0]["policy_name_en"] == "Energy Transition Act"

    @pytest.mark.medium
    def test_policy_name_en_absent_when_not_set(self, client, mock_manager):
        mock_manager.get_all_policies.return_value = [
            Policy(
                url="https://c.gov/p3",
                policy_name="Untranslated Act",
                jurisdiction="DE",
                policy_type=PolicyType.LAW,
                summary="Test",
                relevance_score=7,
            )
        ]
        response = client.get("/api/policies")
        data = response.json()
        assert data["policies"][0]["policy_name_en"] is None


# --- Analysis ---

class TestAnalysisRoutes:
    def test_analyze_route_is_registered(self, client):
        # An invalid body must fail validation (422), not routing (404).
        response = client.post("/api/analyze", json={})
        assert response.status_code == 422

    def test_analyze_url_fetch_failure_returns_status(self, client):
        # Literal public IP exercises the real SSRF guard without DNS.
        with patch("src.api.routes.analysis.AsyncCrawler") as crawler_cls:
            crawler = crawler_cls.return_value
            crawler.crawl_domain = AsyncMock(return_value=[])
            crawler.close = AsyncMock()
            response = client.post(
                "/api/analyze", json={"url": "https://8.8.8.8/policy"}
            )
        assert response.status_code == 200
        data = response.json()
        assert data["crawl_status"] == "fetch_failed"

    def test_analyze_rejects_private_url_without_crawling(self, client):
        """SSRF guard: /api/analyze is admin-gated already, but a rejected
        URL must never reach AsyncCrawler - defense in depth."""
        with patch("src.api.routes.analysis.AsyncCrawler") as crawler_cls:
            response = client.post(
                "/api/analyze", json={"url": "http://127.0.0.1/admin"}
            )
        assert response.status_code == 400
        assert "public http" in response.json()["detail"]
        crawler_cls.assert_not_called()

    def test_analyze_rejects_non_http_scheme_without_crawling(self, client):
        with patch("src.api.routes.analysis.AsyncCrawler") as crawler_cls:
            response = client.post(
                "/api/analyze", json={"url": "javascript:alert(1)"}
            )
        assert response.status_code == 400
        crawler_cls.assert_not_called()

    def test_analyze_allows_public_url_and_crawls(self, client):
        with patch("src.api.routes.analysis.AsyncCrawler") as crawler_cls:
            crawler = crawler_cls.return_value
            crawler.crawl_domain = AsyncMock(return_value=[])
            crawler.close = AsyncMock()
            response = client.post(
                "/api/analyze", json={"url": "https://8.8.8.8/policy"}
            )
        assert response.status_code == 200
        crawler_cls.assert_called_once()

    def test_root_listing_matches_registered_routes(self, client):
        """Every endpoint advertised at / must actually be registered."""
        from src.api.app import app

        registered = set(app.openapi()["paths"])
        advertised = client.get("/").json()["endpoints"]
        for name, path in advertised.items():
            base = path.split("?")[0]
            assert any(
                r == base or r.startswith(base) for r in registered
            ), f"advertised endpoint '{name}' ({base}) is not registered"


class TestConfigSettingsSecurity:
    """Regression: /api/config/settings must never leak the Google
    service-account credentials, and the diagnostic GETs must be admin-only.
    """

    def _remote_client(self, mock_config, mock_store, mock_manager, mock_broadcaster):
        from src.api.app import app
        from src.api import deps
        app.dependency_overrides[deps.get_config] = lambda: mock_config
        app.dependency_overrides[deps.get_policy_store] = lambda: mock_store
        app.dependency_overrides[deps.get_scan_manager] = lambda: mock_manager
        app.dependency_overrides[deps.get_broadcaster] = lambda: mock_broadcaster
        return TestClient(app, client=("203.0.113.7", 40000))

    def _settings_with_secret(self, mock_config):
        settings = MagicMock()
        settings.model_dump.return_value = {
            "crawl": {"max_pages_per_domain": 200},
            "output": {
                "spreadsheet_id": "sheet-abc",
                "google_credentials_b64": "SUPER_SECRET_BASE64_BLOB",
                "staging_sheet_name": "Staging",
            },
        }
        mock_config.settings = settings

    def test_admin_gets_settings_without_the_credentials_blob(self, client, mock_config):
        self._settings_with_secret(mock_config)
        resp = client.get("/api/config/settings")  # testclient host == admin
        assert resp.status_code == 200
        output = resp.json()["output"]
        assert "google_credentials_b64" not in output
        assert output["spreadsheet_id"] == "sheet-abc"  # non-secret field kept

    def test_non_admin_cannot_read_settings(
        self, mock_config, mock_store, mock_manager, mock_broadcaster
    ):
        from src.api.app import app
        self._settings_with_secret(mock_config)
        client = self._remote_client(mock_config, mock_store, mock_manager, mock_broadcaster)
        try:
            resp = client.get("/api/config/settings")
            assert resp.status_code == 403
            assert "SUPER_SECRET" not in resp.text
        finally:
            app.dependency_overrides.clear()

    def test_non_admin_cannot_read_keywords_or_logs_or_api_key(
        self, mock_config, mock_store, mock_manager, mock_broadcaster
    ):
        from src.api.app import app
        client = self._remote_client(mock_config, mock_store, mock_manager, mock_broadcaster)
        try:
            for path in (
                "/api/config/keywords",
                "/api/logs",
                "/api/logs/audit",
                "/api/logs/info",
                "/api/settings/api-key",
            ):
                assert client.get(path).status_code == 403, path
        finally:
            app.dependency_overrides.clear()


# --- Scans ---

class TestScanRoutes:
    def test_start_scan_passes_deep_flag(self, client, mock_manager):
        job = ScanJob(
            scan_id="s1",
            status=ScanStatus.RUNNING,
            domain_count=1,
            options={"deep": True},
        )
        mock_manager.start_scan = AsyncMock(return_value=job)

        response = client.post(
            "/api/scans", json={"domains": "quick", "deep": True, "skip_llm": True}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["scan_id"] == "s1"
        assert data["options"]["deep"] is True
        mock_manager.start_scan.assert_awaited_once()
        assert mock_manager.start_scan.await_args.kwargs["deep"] is True

    def test_start_scan_discover_runs_agent_prompt(self, client, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        with patch("src.api.routes.scans.PolicyAgent") as agent_cls:
            agent = agent_cls.return_value
            agent.run = AsyncMock(return_value="discovery complete")
            agent.close = AsyncMock()

            response = client.post(
                "/api/scans",
                json={"domains": "Poland", "discover": True},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["scan_id"] is None
        assert data["discover"] is True
        assert data["deep"] is False
        assert data["response"] == "discovery complete"
        agent.run.assert_awaited_once()
        prompt = agent.run.await_args.args[0]
        assert "Discover new coverage for Poland" in prompt

    def test_start_scan_rejects_multiple_modes(self, client):
        response = client.post(
            "/api/scans",
            json={"domains": "Poland", "discover": True, "deep": True},
        )

        assert response.status_code == 422

    def test_start_scan_rejects_invalid_channel(self, client):
        response = client.post(
            "/api/scans",
            json={"domains": "quick", "channels": ["bogus"]},
        )
        assert response.status_code == 422
        data = response.json()
        assert "bogus" in str(data["detail"])

    def test_start_scan_defaults_to_crawl_channel(self, client, mock_manager):
        job = ScanJob(
            scan_id="s1",
            status=ScanStatus.RUNNING,
            domain_count=1,
            options={"channels": ["crawl"]},
        )
        mock_manager.start_scan = AsyncMock(return_value=job)

        response = client.post("/api/scans", json={"domains": "quick", "skip_llm": True})

        assert response.status_code == 200
        mock_manager.start_scan.assert_awaited_once()
        assert mock_manager.start_scan.await_args.kwargs["channels"] == ["crawl"]

    def test_start_scan_passes_requested_channels(self, client, mock_manager):
        job = ScanJob(
            scan_id="s1",
            status=ScanStatus.RUNNING,
            domain_count=0,
            options={"channels": ["law_apis"]},
        )
        mock_manager.start_scan = AsyncMock(return_value=job)

        response = client.post(
            "/api/scans",
            json={"domains": "quick", "channels": ["law_apis"], "skip_llm": True},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["options"]["channels"] == ["law_apis"]
        assert mock_manager.start_scan.await_args.kwargs["channels"] == ["law_apis"]

    @pytest.mark.medium
    def test_start_scan_passes_budget_usd(self, client, mock_manager):
        job = ScanJob(scan_id="s1", status=ScanStatus.RUNNING, domain_count=1)
        mock_manager.start_scan = AsyncMock(return_value=job)

        response = client.post(
            "/api/scans",
            json={"domains": "quick", "skip_llm": True, "budget_usd": 5.0},
        )

        assert response.status_code == 200
        assert mock_manager.start_scan.await_args.kwargs["budget_usd"] == 5.0

    @pytest.mark.medium
    def test_start_scan_negative_budget_usd_rejected(self, client):
        response = client.post(
            "/api/scans",
            json={"domains": "quick", "skip_llm": True, "budget_usd": -1.0},
        )
        assert response.status_code == 422

    @pytest.mark.medium
    def test_start_scan_omitted_budget_usd_defaults_to_none(self, client, mock_manager):
        job = ScanJob(scan_id="s1", status=ScanStatus.RUNNING, domain_count=1)
        mock_manager.start_scan = AsyncMock(return_value=job)

        response = client.post(
            "/api/scans", json={"domains": "quick", "skip_llm": True},
        )

        assert response.status_code == 200
        assert mock_manager.start_scan.await_args.kwargs["budget_usd"] is None

    def test_list_scans_empty(self, client):
        response = client.get("/api/scans")
        assert response.status_code == 200
        assert response.json() == []

    def test_cost_estimate_unknown_scope_returns_400(self, client, mock_manager):
        from src.core.config import ConfigurationError

        mock_manager.estimate_cost.side_effect = ConfigurationError(
            "Unknown group/region/domain: 'bogus'"
        )
        response = client.post("/api/cost-estimate?domains=bogus")
        assert response.status_code == 400
        assert "bogus" in response.json()["detail"]

    def test_cost_estimate_valid_scope_returns_shape(self, client, mock_manager):
        mock_manager.estimate_cost.return_value = {
            "domain_count": 3,
            "estimated_pages": 300,
            "estimated_keyword_passes": 30,
            "estimated_screening_calls": 30,
            "estimated_analysis_calls": 15,
            "estimated_cost_usd": 1.23,
        }
        response = client.post("/api/cost-estimate?domains=quick")
        assert response.status_code == 200
        data = response.json()
        assert data["domain_count"] == 3
        assert data["estimated_cost_usd"] == 1.23
        mock_manager.estimate_cost.assert_called_once_with("quick", deep=False)

    def test_cost_estimate_deep_flag_passed_through(self, client, mock_manager):
        mock_manager.estimate_cost.return_value = {
            "domain_count": 3,
            "estimated_pages": 300,
            "estimated_keyword_passes": 30,
            "estimated_screening_calls": 30,
            "estimated_analysis_calls": 15,
            "estimated_cost_usd": 2.5,
        }
        response = client.post("/api/cost-estimate?domains=quick&deep=true")
        assert response.status_code == 200
        mock_manager.estimate_cost.assert_called_once_with("quick", deep=True)

    def test_list_scans_with_job(self, client, mock_manager):
        job = ScanJob(scan_id="s1", status=ScanStatus.COMPLETED, domain_count=2, policy_count=1)
        mock_manager.jobs = {"s1": job}
        response = client.get("/api/scans")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["scan_id"] == "s1"

    def test_get_scan_not_found(self, client):
        response = client.get("/api/scans/nonexistent")
        assert response.status_code == 404
        data = response.json()
        assert "detail" in data

    def test_get_scan_detail(self, client, mock_manager):
        job = ScanJob(
            scan_id="s1",
            status=ScanStatus.COMPLETED,
            domain_count=2,
            policy_count=1,
            progress=ScanProgress(total_domains=2, completed_domains=2),
        )
        mock_manager.jobs = {"s1": job}
        mock_manager.get_policies.return_value = []
        response = client.get("/api/scans/s1")
        assert response.status_code == 200
        data = response.json()
        assert data["scan_id"] == "s1"
        assert data["progress"]["total"] == 2
        assert data["budget_reached"] is False

    @pytest.mark.medium
    def test_get_scan_detail_exposes_budget_reached(self, client, mock_manager):
        """WP-22b: the mid-scan budget-stop flag must be visible via the
        route, not just on the in-memory job object."""
        job = ScanJob(
            scan_id="s1", status=ScanStatus.COMPLETED, domain_count=4,
            budget_reached=True,
        )
        mock_manager.jobs = {"s1": job}
        mock_manager.get_policies.return_value = []

        response = client.get("/api/scans/s1")

        assert response.status_code == 200
        assert response.json()["budget_reached"] is True

    def test_cancel_scan_not_found(self, client, mock_manager):
        mock_manager.stop_scan = AsyncMock(return_value=False)
        response = client.delete("/api/scans/nonexistent")
        assert response.status_code == 404
        data = response.json()
        assert "detail" in data
