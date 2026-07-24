"""Tests for ScanManager domain-default handling."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.config import ConfigLoader, ConfigurationError
from src.core.models import DomainProgress, DomainScanStatus, Policy, PolicyType
from src.orchestration.events import EventBroadcaster
from src.orchestration.scan_manager import ScanManager
from src.storage.scan_history import ScanHistoryStore
from src.storage.store import PolicyStore


def _settings_with_min_score(value: float) -> MagicMock:
    settings = MagicMock()
    settings.analysis.min_keyword_score = value
    return settings


class TestKeywordScoreDefault:
    """settings.analysis.min_keyword_score must reach the keyword gate.

    Historically the settings value was loaded but never read: domains
    without an explicit min_keyword_score silently fell back to the
    stricter keywords.yaml threshold (5.0) instead of the documented 3.0.
    """

    def test_domain_without_score_gets_settings_default(self):
        domain = {"id": "d1", "base_url": "https://a.gov"}
        result = ScanManager._with_keyword_score_default(
            domain, _settings_with_min_score(3.0)
        )
        assert result["min_keyword_score"] == 3.0

    def test_domain_with_explicit_score_keeps_it(self):
        domain = {"id": "d1", "base_url": "https://a.gov", "min_keyword_score": 2.0}
        result = ScanManager._with_keyword_score_default(
            domain, _settings_with_min_score(3.0)
        )
        assert result["min_keyword_score"] == 2.0

    def test_original_domain_dict_not_mutated(self):
        domain = {"id": "d1", "base_url": "https://a.gov"}
        ScanManager._with_keyword_score_default(domain, _settings_with_min_score(3.0))
        assert "min_keyword_score" not in domain

    def test_deep_scan_default_wins_over_settings(self):
        # _with_deep_scan_defaults runs first (sets 2.0); settings must not override
        domain = ScanManager._with_deep_scan_defaults(
            {"id": "d1", "base_url": "https://a.gov"}
        )
        result = ScanManager._with_keyword_score_default(
            domain, _settings_with_min_score(3.0)
        )
        assert result["min_keyword_score"] == 2.0


class TestDomainChannel:
    """_domain_channel() classifies a domain by its source_type."""

    def test_absent_source_type_is_crawl(self):
        domain = {"id": "d1", "base_url": "https://a.gov"}
        assert ScanManager._domain_channel(domain) == "crawl"

    def test_explicit_crawl_source_type_is_crawl(self):
        domain = {"id": "d1", "source_type": "crawl"}
        assert ScanManager._domain_channel(domain) == "crawl"

    def test_eurlex_nim_is_transposition(self):
        domain = {"id": "d1", "source_type": "eurlex_nim"}
        assert ScanManager._domain_channel(domain) == "transposition"

    def test_other_source_type_is_law_apis(self):
        domain = {"id": "d1", "source_type": "riksdagen"}
        assert ScanManager._domain_channel(domain) == "law_apis"


def _manager_with_domains(domains: list[dict]) -> ScanManager:
    config = MagicMock()
    config.get_enabled_domains.return_value = domains
    return ScanManager(config=config, broadcaster=MagicMock())


class TestStartScanChannels:
    """start_scan() filters domains by channel and records the choice.

    dry_run=True is used throughout so start_scan returns synchronously
    (job already COMPLETED) without spawning the background scan task.
    """

    @pytest.mark.asyncio
    async def test_default_channel_is_crawl_only(self):
        domains = [
            {"id": "crawl1", "name": "Crawl 1"},
            {"id": "api1", "name": "Api 1", "source_type": "riksdagen"},
        ]
        manager = _manager_with_domains(domains)
        job = await manager.start_scan(dry_run=True)
        assert job.domain_count == 1
        assert [dp.domain_id for dp in job.progress.domains] == ["crawl1"]
        assert job.options["channels"] == ["crawl"]

    @pytest.mark.asyncio
    async def test_law_apis_channel_selects_only_source_type_domains(self):
        domains = [
            {"id": "crawl1", "name": "Crawl 1"},
            {"id": "api1", "name": "Api 1", "source_type": "riksdagen"},
            {"id": "api2", "name": "Api 2", "source_type": "govinfo"},
            {"id": "eurlex1", "name": "EurLex", "source_type": "eurlex_nim"},
        ]
        manager = _manager_with_domains(domains)
        job = await manager.start_scan(dry_run=True, channels=["law_apis"])
        assert job.domain_count == 2
        assert {dp.domain_id for dp in job.progress.domains} == {"api1", "api2"}

    @pytest.mark.asyncio
    async def test_transposition_channel_selects_eurlex_nim(self):
        domains = [
            {"id": "crawl1", "name": "Crawl 1"},
            {"id": "eurlex1", "name": "EurLex", "source_type": "eurlex_nim"},
        ]
        manager = _manager_with_domains(domains)
        job = await manager.start_scan(dry_run=True, channels=["transposition"])
        assert job.domain_count == 1
        assert job.progress.domains[0].domain_id == "eurlex1"

    @pytest.mark.asyncio
    async def test_options_records_requested_channels(self):
        manager = _manager_with_domains([])
        job = await manager.start_scan(dry_run=True, channels=["crawl", "law_apis"])
        assert job.options["channels"] == ["crawl", "law_apis"]

    @pytest.mark.asyncio
    async def test_news_only_channel_yields_zero_domains(self):
        domains = [
            {"id": "crawl1", "name": "Crawl 1"},
            {"id": "api1", "name": "Api 1", "source_type": "riksdagen"},
        ]
        manager = _manager_with_domains(domains)
        job = await manager.start_scan(dry_run=True, channels=["news"])
        assert job.domain_count == 0
        assert job.options["channels"] == ["news"]


class TestStructuredSourcesRunFirst:
    """Law APIs dispatch ahead of crawls.

    Regression: a 165-domain "United States" scan left the three law APIs
    at positions 40, 101 and 119, so the sources that produce most of the
    policies did not start until most of the scan's time and budget was
    already spent. Structured sources are fast, cheap and high-yield;
    crawls are the long tail.
    """

    ALL_CHANNELS = ["crawl", "law_apis", "transposition"]

    @pytest.mark.asyncio
    async def test_structured_sources_dispatch_before_crawls(self):
        domains = [
            {"id": "crawl1", "name": "Crawl 1"},
            {"id": "crawl2", "name": "Crawl 2"},
            {"id": "api1", "name": "Api 1", "source_type": "legiscan"},
            {"id": "crawl3", "name": "Crawl 3"},
            {"id": "nim1", "name": "NIM", "source_type": "eurlex_nim"},
        ]
        manager = _manager_with_domains(domains)
        job = await manager.start_scan(dry_run=True, channels=self.ALL_CHANNELS)

        ids = [dp.domain_id for dp in job.progress.domains]
        assert ids == ["api1", "nim1", "crawl1", "crawl2", "crawl3"]

    @pytest.mark.asyncio
    async def test_order_within_each_group_is_preserved(self):
        """Stable sort: config order still decides ties inside a group."""
        domains = [
            {"id": "b_api", "name": "B", "source_type": "govinfo"},
            {"id": "z_crawl", "name": "Z"},
            {"id": "a_api", "name": "A", "source_type": "legiscan"},
            {"id": "a_crawl", "name": "A crawl"},
        ]
        manager = _manager_with_domains(domains)
        job = await manager.start_scan(dry_run=True, channels=self.ALL_CHANNELS)

        ids = [dp.domain_id for dp in job.progress.domains]
        assert ids == ["b_api", "a_api", "z_crawl", "a_crawl"]

    @pytest.mark.asyncio
    async def test_all_domains_still_present(self):
        """Reordering must not drop or duplicate a domain."""
        domains = [
            {"id": f"crawl{i}", "name": f"Crawl {i}"} for i in range(5)
        ] + [{"id": "api1", "name": "Api", "source_type": "uk_bills"}]
        manager = _manager_with_domains(domains)
        job = await manager.start_scan(dry_run=True, channels=self.ALL_CHANNELS)

        ids = [dp.domain_id for dp in job.progress.domains]
        assert job.domain_count == 6
        assert sorted(ids) == sorted(d["id"] for d in domains)


class TestSourceParamsOverride:
    """Per-request source_params reach structured sources, never crawl."""

    def test_merges_into_structured_domain(self):
        domain = {
            "id": "legiscan_api", "source_type": "legiscan",
            "source_params": {"max_documents": 10},
        }
        result = ScanManager._with_source_params(domain, {"state": "CA"})
        assert result["source_params"] == {"max_documents": 10, "state": "CA"}

    def test_request_params_win_over_config(self):
        domain = {
            "id": "legiscan_api", "source_type": "legiscan",
            "source_params": {"terms": ["old"]},
        }
        result = ScanManager._with_source_params(domain, {"terms": ["new"]})
        assert result["source_params"]["terms"] == ["new"]

    def test_crawl_domain_untouched(self):
        domain = {"id": "site1", "base_url": "https://a.gov"}
        result = ScanManager._with_source_params(domain, {"state": "CA"})
        assert "source_params" not in result

    def test_original_not_mutated(self):
        domain = {"id": "legiscan_api", "source_type": "legiscan"}
        ScanManager._with_source_params(domain, {"state": "CA"})
        assert "source_params" not in domain

    def test_none_override_is_noop(self):
        domain = {"id": "legiscan_api", "source_type": "legiscan"}
        assert ScanManager._with_source_params(domain, None) is domain

    @pytest.mark.asyncio
    async def test_start_scan_applies_source_params(self, monkeypatch):
        from unittest.mock import AsyncMock
        domains = [
            {"id": "crawl1", "name": "Crawl 1"},
            {"id": "api1", "name": "Api 1", "source_type": "legiscan"},
        ]
        manager = _manager_with_domains(domains)
        run_mock = AsyncMock()
        monkeypatch.setattr(manager, "_run_scan", run_mock)

        await manager.start_scan(
            channels=["crawl", "law_apis"], source_params={"state": "CA"},
        )

        passed = run_mock.call_args[0][1]
        by_id = {d["id"]: d for d in passed}
        assert by_id["api1"]["source_params"] == {"state": "CA"}
        assert "source_params" not in by_id["crawl1"]


def _policy(url: str, review_status: str) -> Policy:
    return Policy(
        url=url,
        policy_name="P",
        jurisdiction="Sweden",
        policy_type=PolicyType.LAW,
        summary="s",
        relevance_score=7,
        review_status=review_status,
    )


class TestRejectedUrlStatuses:
    """ScanManager._rejected_url_statuses feeds the scan-end Sheets
    reconciliation pass (~src/orchestration/scan_manager.py's "Final Google
    Sheets reconciliation" block): every rejected policy's URL, mapped to
    the "rejected" status, ready for SheetsClient.update_review_statuses."""

    def test_returns_only_rejected_urls(self, tmp_path):
        store = PolicyStore(data_dir=str(tmp_path))
        store.add_policies([
            _policy("https://a.gov/new", "new"),
            _policy("https://a.gov/rejected", "rejected"),
            _policy("https://a.gov/promoted", "promoted"),
        ])

        result = ScanManager._rejected_url_statuses(store)

        assert result == {"https://a.gov/rejected": "rejected"}

    def test_empty_when_nothing_rejected(self, tmp_path):
        store = PolicyStore(data_dir=str(tmp_path))
        store.add_policies([_policy("https://a.gov/new", "new")])

        assert ScanManager._rejected_url_statuses(store) == {}

    def test_empty_store_yields_empty(self, tmp_path):
        store = PolicyStore(data_dir=str(tmp_path))
        assert ScanManager._rejected_url_statuses(store) == {}


def _manager_with_config(get_enabled_domains_return=None, get_enabled_domains_side_effect=None):
    config = MagicMock()
    if get_enabled_domains_side_effect is not None:
        config.get_enabled_domains.side_effect = get_enabled_domains_side_effect
    else:
        config.get_enabled_domains.return_value = get_enabled_domains_return
    settings = MagicMock()
    settings.crawl.max_pages_per_domain = 200
    settings.analysis.min_keyword_score = 3.0
    config.settings = settings
    return ScanManager(config=config, broadcaster=MagicMock())


class TestEstimateCost:
    """ScanManager.estimate_cost() — WP-1 estimator repair.

    Unknown scopes now raise ConfigurationError (caught by the API route and
    turned into a 400, mirroring domains.py) instead of a raw 500. deep=True
    applies the deep-scan page/keyword assumptions instead of the standard
    ones, so it must always estimate a strictly higher cost for the same
    scope.
    """

    def test_unknown_scope_raises_configuration_error(self):
        manager = _manager_with_config(
            get_enabled_domains_side_effect=ConfigurationError("Unknown group/region/domain: 'bogus'")
        )
        with pytest.raises(ConfigurationError):
            manager.estimate_cost("bogus")

    def test_valid_scope_returns_expected_shape(self):
        domains = [{"id": f"d{i}", "name": f"D{i}"} for i in range(5)]
        manager = _manager_with_config(get_enabled_domains_return=domains)

        result = manager.estimate_cost("quick")

        assert result["domain_count"] == 5
        assert set(result.keys()) == {
            "domain_count",
            "estimated_pages",
            "estimated_keyword_passes",
            "estimated_screening_calls",
            "estimated_analysis_calls",
            "estimated_cost_usd",
        }
        assert result["estimated_cost_usd"] > 0

    def test_deep_estimate_is_strictly_higher_than_standard(self):
        domains = [{"id": f"d{i}", "name": f"D{i}"} for i in range(5)]
        manager = _manager_with_config(get_enabled_domains_return=domains)

        standard = manager.estimate_cost("quick", deep=False)
        deep = manager.estimate_cost("quick", deep=True)

        assert deep["estimated_cost_usd"] > standard["estimated_cost_usd"]


def _minimal_config(config_dir) -> ConfigLoader:
    """A real, minimal config directory (same shape as the integration
    suite's tmp_config_dir), used so start_scan()'s real domain-resolution
    and settings code runs unmocked."""
    domains_dir = config_dir / "domains"
    domains_dir.mkdir(parents=True)
    (config_dir / "settings.yaml").write_text(
        "crawl:\n  max_depth: 2\n  delay_seconds: 0.5\n"
        "analysis:\n  min_keyword_score: 3\n",
        encoding="utf-8",
    )
    (domains_dir / "test.yaml").write_text(
        "domains:\n"
        "  - id: test_gov\n"
        "    name: Test Gov\n"
        "    base_url: https://test.gov\n"
        "    start_paths: [\"/\"]\n"
        "    enabled: true\n",
        encoding="utf-8",
    )
    (config_dir / "groups.yaml").write_text(
        "groups:\n"
        "  quick:\n"
        "    description: Quick scan\n"
        "    domains: [test_gov]\n",
        encoding="utf-8",
    )
    (config_dir / "keywords.yaml").write_text(
        "categories:\n"
        "  heat_recovery:\n"
        "    weight: 3.0\n"
        "    terms:\n"
        "      en: [heat reuse]\n"
        "thresholds:\n"
        "  min_score: 3.0\n"
        "  min_matches: 1\n",
        encoding="utf-8",
    )
    (config_dir / "url_filters.yaml").write_text(
        "url_filters:\n"
        "  skip_paths: []\n"
        "  skip_extensions: []\n",
        encoding="utf-8",
    )
    config = ConfigLoader(config_dir=str(config_dir))
    config.load()
    return config


class TestScanHistoryWiring:
    """A completed/failed/cancelled scan writes a row to the scans table
    (WP-5), next to the existing audit events. The crawler and
    DomainScanner are mocked (no network, no LLM) so this stays a fast unit
    test rather than a real crawl."""

    def _manager(self, tmp_path, monkeypatch, *, domain_scan_result=None, scanner_side_effect=None):
        config = _minimal_config(tmp_path / "config")
        data_dir = tmp_path / "data"
        manager = ScanManager(
            config=config, broadcaster=EventBroadcaster(), data_dir=str(data_dir),
        )

        mock_scanner = MagicMock()
        if scanner_side_effect is not None:
            mock_scanner.scan = AsyncMock(side_effect=scanner_side_effect)
        else:
            mock_scanner.scan = AsyncMock(return_value=domain_scan_result or [])
        mock_scanner.progress = DomainProgress(
            domain_id="test_gov", domain_name="Test Gov",
            status=DomainScanStatus.COMPLETED,
        )
        monkeypatch.setattr(
            "src.orchestration.scan_manager.DomainScanner",
            lambda **kwargs: mock_scanner,
        )
        monkeypatch.setattr(
            "src.orchestration.scan_manager.AsyncCrawler",
            lambda **kwargs: MagicMock(close=AsyncMock()),
        )
        return manager, data_dir

    @pytest.mark.asyncio
    async def test_completed_scan_writes_history_row(self, tmp_path, monkeypatch):
        manager, data_dir = self._manager(tmp_path, monkeypatch, domain_scan_result=[])

        job = await manager.start_scan(domains_group="quick", skip_llm=True)
        await manager._tasks[job.scan_id]

        rows = ScanHistoryStore(data_dir=str(data_dir)).list()
        assert len(rows) == 1
        row = rows[0]
        assert row["scan_id"] == job.scan_id
        assert row["status"] == "completed"
        assert row["domain_group"] == "quick"
        assert row["mode"] == "standard"
        assert row["channels"] == ["crawl"]
        assert row["domains_scanned"] == 1
        assert row["policies_found"] == 0
        assert row["started_at"] is not None
        assert row["completed_at"] is not None

    @pytest.mark.asyncio
    async def test_deep_scan_records_deep_mode(self, tmp_path, monkeypatch):
        manager, data_dir = self._manager(tmp_path, monkeypatch, domain_scan_result=[])

        job = await manager.start_scan(domains_group="quick", skip_llm=True, deep=True)
        await manager._tasks[job.scan_id]

        row = ScanHistoryStore(data_dir=str(data_dir)).list()[0]
        assert row["mode"] == "deep"

    @pytest.mark.asyncio
    async def test_failed_scan_records_failed_status(self, tmp_path, monkeypatch):
        """A domain-level exception is caught inside scan_domain() itself
        (see scan_manager.py) and still yields an overall "completed" scan —
        so to exercise the outer except-Exception branch (the "failed"
        status), the failure has to come from after the per-domain gather,
        where a real bug (a cache write failure) would land."""
        manager, data_dir = self._manager(tmp_path, monkeypatch, domain_scan_result=[])
        monkeypatch.setattr(
            "src.core.cache.URLCache.save",
            MagicMock(side_effect=RuntimeError("disk full")),
        )

        job = await manager.start_scan(domains_group="quick", skip_llm=True)
        await manager._tasks[job.scan_id]

        row = ScanHistoryStore(data_dir=str(data_dir)).list()[0]
        assert row["status"] == "failed"

    @pytest.mark.asyncio
    async def test_dry_run_writes_no_history_row(self, tmp_path, monkeypatch):
        manager, data_dir = self._manager(tmp_path, monkeypatch, domain_scan_result=[])

        await manager.start_scan(domains_group="quick", skip_llm=True, dry_run=True)

        assert ScanHistoryStore(data_dir=str(data_dir)).list() == []
