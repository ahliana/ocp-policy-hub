"""Tests for ScanManager domain-default handling."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.config import ConfigLoader, ConfigurationError
from src.core.models import (
    CostInfo, DomainProgress, DomainScanStatus, Policy, PolicyType,
    DEFAULT_ANALYSIS_MODEL, DEFAULT_SCREENING_MODEL,
)
from src.core.pricing import PricingLoader
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


def _manager_with_config(
    get_enabled_domains_return=None, get_enabled_domains_side_effect=None,
    screening_model=None, analysis_model=None,
):
    from src.core.models import DEFAULT_ANALYSIS_MODEL, DEFAULT_SCREENING_MODEL

    config = MagicMock()
    if get_enabled_domains_side_effect is not None:
        config.get_enabled_domains.side_effect = get_enabled_domains_side_effect
    else:
        config.get_enabled_domains.return_value = get_enabled_domains_return
    settings = MagicMock()
    settings.crawl.max_pages_per_domain = 200
    settings.analysis.min_keyword_score = 3.0
    settings.analysis.screening_model = screening_model or DEFAULT_SCREENING_MODEL
    settings.analysis.analysis_model = analysis_model or DEFAULT_ANALYSIS_MODEL
    config.settings = settings
    return ScanManager(config=config, broadcaster=MagicMock())


class TestEstimateCost:
    """ScanManager.estimate_cost() - WP-1 estimator repair.

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
        """WP-21: every pre-existing key is kept (frontend depends on them)
        and three new ones are added: channels, auditor_cost_usd,
        assumptions."""
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
            "channels",
            "auditor_cost_usd",
            "assumptions",
        }
        assert result["estimated_cost_usd"] > 0
        assert result["auditor_cost_usd"] > 0
        assert isinstance(result["assumptions"], list)
        assert all(isinstance(a, str) for a in result["assumptions"])

    def test_deep_estimate_is_strictly_higher_than_standard(self):
        domains = [{"id": f"d{i}", "name": f"D{i}"} for i in range(5)]
        manager = _manager_with_config(get_enabled_domains_return=domains)

        standard = manager.estimate_cost("quick", deep=False)
        deep = manager.estimate_cost("quick", deep=True)

        assert deep["estimated_cost_usd"] > standard["estimated_cost_usd"]

    def test_channels_filter_narrows_the_domain_count(self):
        # A schedule scoped to only law databases must not be costed as if it
        # also crawled every website (review finding). crawl=3, law_apis=2.
        domains = (
            [{"id": f"c{i}", "name": f"C{i}"} for i in range(3)]
            + [{"id": f"a{i}", "name": f"A{i}", "source_type": "legiscan"} for i in range(2)]
        )
        manager = _manager_with_config(get_enabled_domains_return=domains)

        all_channels = manager.estimate_cost("quick")
        apis_only = manager.estimate_cost("quick", channels=["law_apis"])

        assert all_channels["domain_count"] == 5
        assert apis_only["domain_count"] == 2
        assert apis_only["estimated_cost_usd"] < all_channels["estimated_cost_usd"]

    def test_channels_none_counts_all_domains(self):
        # Callers that don't pass channels (e.g. cost_projection) are unchanged.
        domains = [{"id": f"d{i}", "name": f"D{i}"} for i in range(4)]
        manager = _manager_with_config(get_enabled_domains_return=domains)
        assert manager.estimate_cost("quick")["domain_count"] == 4

    @pytest.mark.medium
    def test_reacts_to_monkeypatched_pricing_table(self, tmp_path):
        """WP-19: estimate_cost() must actually consult the pricing table,
        not a constant baked into the function."""
        domains = [{"id": "d1", "name": "D1"}]
        manager = _manager_with_config(get_enabled_domains_return=domains)
        baseline = manager.estimate_cost("quick")["estimated_cost_usd"]

        (tmp_path / "pricing.yaml").write_text(
            "models:\n"
            f"  {DEFAULT_SCREENING_MODEL}:\n"
            "    input_per_mtok: 1000.0\n"
            "    output_per_mtok: 1000.0\n"
            f"  {DEFAULT_ANALYSIS_MODEL}:\n"
            "    input_per_mtok: 1000.0\n"
            "    output_per_mtok: 1000.0\n"
            "estimator:\n"
            "  screening_input: 2000\n"
            "  screening_output: 50\n"
            "  analysis_input: 20000\n"
            "  analysis_output: 1000\n"
            "  auditor_input: 5000\n"
            "  auditor_output: 2000\n"
            "  structured_items_per_source: 40\n",
            encoding="utf-8",
        )
        manager._pricing = PricingLoader(config_dir=str(tmp_path))

        inflated = manager.estimate_cost("quick")["estimated_cost_usd"]

        assert inflated > baseline * 100


@pytest.mark.small
class TestEstimateCostRespectsCostLevel:
    """WP-20: estimate_cost() reads settings.analysis.{screening,analysis}
    _model - the exact attributes CostSettingsStore.apply_to_config()
    mutates - so an admin's cost level (low/standard/high) changes the
    estimate, matching what a real scan would actually spend.
    """

    LEVELS = {
        "low": (DEFAULT_SCREENING_MODEL, DEFAULT_SCREENING_MODEL),
        "standard": (DEFAULT_SCREENING_MODEL, DEFAULT_ANALYSIS_MODEL),
        "high": (DEFAULT_ANALYSIS_MODEL, DEFAULT_ANALYSIS_MODEL),
    }

    @staticmethod
    def _expected_cost_usd(screening_model: str, analysis_model: str, domain_count: int = 5) -> float:
        """Independently derives the expected dollar figure straight from
        the real pricing.yaml table, so this is an exact-math check, not
        just a greater-than comparison."""
        pricing = PricingLoader()
        est = pricing.estimator
        screening_price = pricing.pricing_for(screening_model)
        analysis_price = pricing.pricing_for(analysis_model)

        est_pages_per_domain = 200 // 2
        total_pages = domain_count * est_pages_per_domain
        keyword_passes = int(total_pages * 0.10)
        screening_calls = keyword_passes
        analysis_calls = int(screening_calls * 0.50)

        raw = (
            screening_calls * screening_price.cost_usd(
                est["screening_input"], est["screening_output"]
            )
            + analysis_calls * analysis_price.cost_usd(
                est["analysis_input"], est["analysis_output"]
            )
        )
        auditor_price = pricing.pricing_for(DEFAULT_ANALYSIS_MODEL)
        auditor_raw = auditor_price.cost_usd(est["auditor_input"], est["auditor_output"])
        return round(raw + auditor_raw, 2)

    @pytest.mark.parametrize("level", ["low", "standard", "high"])
    def test_matches_expected_dollars_for_level(self, level):
        screening_model, analysis_model = self.LEVELS[level]
        domains = [{"id": f"d{i}", "name": f"D{i}"} for i in range(5)]
        manager = _manager_with_config(
            get_enabled_domains_return=domains,
            screening_model=screening_model, analysis_model=analysis_model,
        )

        result = manager.estimate_cost("quick")

        assert result["estimated_cost_usd"] == self._expected_cost_usd(
            screening_model, analysis_model,
        )

    def test_low_cheaper_than_standard_cheaper_than_high(self):
        domains = [{"id": f"d{i}", "name": f"D{i}"} for i in range(5)]

        def _cost(level):
            screening_model, analysis_model = self.LEVELS[level]
            manager = _manager_with_config(
                get_enabled_domains_return=domains,
                screening_model=screening_model, analysis_model=analysis_model,
            )
            return manager.estimate_cost("quick")["estimated_cost_usd"]

        assert _cost("low") < _cost("standard") < _cost("high")


@pytest.mark.small
class TestEstimateCostChannels:
    """WP-21: crawl vs structured domains get different cost models
    (structured sources skip the crawl page model and the keyword gate
    entirely, mirroring scanner.py's real behavior), and the response
    exposes a per-channel breakdown alongside the pre-existing aggregate
    keys.
    """

    def test_crawl_only_channel_breakdown(self):
        domains = [{"id": f"c{i}", "name": f"C{i}"} for i in range(3)]
        manager = _manager_with_config(get_enabled_domains_return=domains)

        result = manager.estimate_cost("quick")

        assert set(result["channels"].keys()) == {"crawl"}
        crawl = result["channels"]["crawl"]
        assert crawl["domain_count"] == 3
        assert crawl["estimated_items_or_pages"] == result["estimated_pages"]
        assert crawl["cost_usd"] > 0

    def test_structured_only_channel_uses_flat_items_no_keyword_gate(self):
        domains = [
            {"id": f"a{i}", "name": f"A{i}", "source_type": "legiscan"}
            for i in range(2)
        ]
        manager = _manager_with_config(get_enabled_domains_return=domains)

        result = manager.estimate_cost("quick")

        assert set(result["channels"].keys()) == {"law_apis"}
        law_apis = result["channels"]["law_apis"]
        assert law_apis["domain_count"] == 2
        # 2 sources * 40 assumed items/source (pricing.yaml estimator default)
        assert law_apis["estimated_items_or_pages"] == 80
        # No keyword gate for structured sources: every assumed item reaches
        # screening (scanner.py sets is_relevant=True unconditionally).
        assert law_apis["screening_calls"] == 80
        assert result["estimated_keyword_passes"] == 80

    def test_mixed_scopes_split_by_channel(self):
        domains = (
            [{"id": f"c{i}", "name": f"C{i}"} for i in range(2)]
            + [{"id": "eu1", "name": "EU1", "source_type": "eurlex_nim"}]
            + [{"id": "a1", "name": "A1", "source_type": "legiscan"}]
        )
        manager = _manager_with_config(get_enabled_domains_return=domains)

        result = manager.estimate_cost("quick")

        assert set(result["channels"].keys()) == {"crawl", "transposition", "law_apis"}
        assert result["channels"]["crawl"]["domain_count"] == 2
        assert result["channels"]["transposition"]["domain_count"] == 1
        assert result["channels"]["law_apis"]["domain_count"] == 1

        channel_sum = sum(c["cost_usd"] for c in result["channels"].values())
        assert result["estimated_cost_usd"] == pytest.approx(
            channel_sum + result["auditor_cost_usd"], abs=0.05,
        )

    def test_channels_param_still_filters_the_breakdown(self):
        domains = (
            [{"id": f"c{i}", "name": f"C{i}"} for i in range(3)]
            + [{"id": f"a{i}", "name": f"A{i}", "source_type": "legiscan"} for i in range(2)]
        )
        manager = _manager_with_config(get_enabled_domains_return=domains)

        apis_only = manager.estimate_cost("quick", channels=["law_apis"])

        assert set(apis_only["channels"].keys()) == {"law_apis"}

    def test_response_keeps_every_pre_existing_key(self):
        """Backward-compat: the frontend reads these top-level keys directly."""
        domains = [{"id": f"d{i}", "name": f"D{i}"} for i in range(3)]
        manager = _manager_with_config(get_enabled_domains_return=domains)

        result = manager.estimate_cost("quick")

        for key in (
            "domain_count", "estimated_pages", "estimated_keyword_passes",
            "estimated_screening_calls", "estimated_analysis_calls",
            "estimated_cost_usd",
        ):
            assert key in result

    def test_assumptions_mentions_structured_items_assumption(self):
        domains = [{"id": "d1", "name": "D1"}]
        manager = _manager_with_config(get_enabled_domains_return=domains)

        result = manager.estimate_cost("quick")

        assert any("structured sources" in a for a in result["assumptions"])


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
    async def test_crawl_filters_snapshotted_once_per_scan(self, tmp_path, monkeypatch):
        """POST /api/config/reload reassigns manager.config on the live
        instance. The url-filter set must be read once at scan start, not per
        domain, or one run would crawl early domains under the old filters
        and later domains under the new ones (review finding on WP-8)."""
        config = _minimal_config(tmp_path / "config")
        base_domain = dict(config.get_enabled_domains("quick")[0])
        second = dict(base_domain, id="test_gov_2", name="Test Gov 2")
        monkeypatch.setattr(
            config, "get_enabled_domains", lambda group: [dict(base_domain), second],
        )
        skip_mock = MagicMock(side_effect=[[".one"], [".two"]])
        monkeypatch.setattr(config, "get_skip_extensions", skip_mock)

        manager = ScanManager(
            config=config, broadcaster=EventBroadcaster(), data_dir=str(tmp_path / "data"),
        )
        mock_scanner = MagicMock()
        mock_scanner.scan = AsyncMock(return_value=[])
        mock_scanner.progress = DomainProgress(
            domain_id="test_gov", domain_name="Test Gov",
            status=DomainScanStatus.COMPLETED,
        )
        monkeypatch.setattr(
            "src.orchestration.scan_manager.DomainScanner",
            lambda **kwargs: mock_scanner,
        )
        crawler_kwargs = []

        def record_crawler(**kwargs):
            crawler_kwargs.append(kwargs)
            return MagicMock(close=AsyncMock())

        monkeypatch.setattr("src.orchestration.scan_manager.AsyncCrawler", record_crawler)

        job = await manager.start_scan(
            domains_group="quick", skip_llm=True, max_concurrent=1,
        )
        await manager._tasks[job.scan_id]

        assert len(crawler_kwargs) == 2
        assert [k["skip_extensions"] for k in crawler_kwargs] == [[".one"], [".one"]]
        assert skip_mock.call_count == 1

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
        (see scan_manager.py) and still yields an overall "completed" scan -
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


@pytest.mark.medium
class TestAuditorCostIntegration:
    """WP-22: the auditor's own Sonnet call must land in job.cost when it
    fires (folded in at scan_manager.py's post-scan auditor block), and
    must not fabricate cost when the auditor never runs.
    """

    def _manager(
        self, tmp_path, monkeypatch, *,
        policies=None, auditor_usage=(4500, 300), auditor_advisory="ok",
    ):
        config = _minimal_config(tmp_path / "config")
        data_dir = tmp_path / "data"
        manager = ScanManager(
            config=config, broadcaster=EventBroadcaster(), data_dir=str(data_dir),
            api_key="sk-ant-test",
        )

        mock_scanner = MagicMock()
        mock_scanner.scan = AsyncMock(return_value=policies or [])
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

        fake_llm = MagicMock()
        fake_llm.cost = CostInfo()
        fake_llm.close = AsyncMock()
        fake_llm.update_cost_estimate = MagicMock()
        monkeypatch.setattr(
            "src.orchestration.scan_manager.ClaudeClient", lambda **kwargs: fake_llm,
        )

        class _FakeAuditor:
            def __init__(self, api_key, **kwargs):
                self.model = DEFAULT_ANALYSIS_MODEL
                self.last_input_tokens = None
                self.last_output_tokens = None
                self.close = AsyncMock()

            async def generate_advisory(self, **kwargs):
                if auditor_usage is not None:
                    self.last_input_tokens, self.last_output_tokens = auditor_usage
                return auditor_advisory

        monkeypatch.setattr("src.orchestration.scan_manager.Auditor", _FakeAuditor)

        return manager, data_dir

    def _policy(self) -> Policy:
        return Policy(
            url="https://test.gov/p1", policy_name="P", jurisdiction="US",
            policy_type=PolicyType.LAW, summary="s", relevance_score=7,
        )

    @pytest.mark.asyncio
    async def test_auditor_cost_included_when_it_fires(self, tmp_path, monkeypatch):
        manager, data_dir = self._manager(
            tmp_path, monkeypatch, policies=[self._policy()],
        )

        job = await manager.start_scan(domains_group="quick", skip_llm=False)
        await manager._tasks[job.scan_id]

        assert job.cost.input_tokens >= 4500
        assert job.cost.output_tokens >= 300
        assert job.cost.total_usd > 0

    @pytest.mark.asyncio
    async def test_auditor_cost_matches_pricing_table(self, tmp_path, monkeypatch):
        manager, data_dir = self._manager(
            tmp_path, monkeypatch, policies=[self._policy()], auditor_usage=(5000, 2000),
        )

        job = await manager.start_scan(domains_group="quick", skip_llm=False)
        await manager._tasks[job.scan_id]

        sonnet = manager._pricing.pricing_for(DEFAULT_ANALYSIS_MODEL)
        expected = round(sonnet.cost_usd(5000, 2000), 4)
        assert job.cost.total_usd == expected

    @pytest.mark.asyncio
    async def test_auditor_cost_absent_when_no_policies_found(self, tmp_path, monkeypatch):
        # Auditor only fires when all_policies is non-empty (see the guard
        # in scan_manager.py) - no policies means no auditor call at all.
        manager, data_dir = self._manager(tmp_path, monkeypatch, policies=[])

        job = await manager.start_scan(domains_group="quick", skip_llm=False)
        await manager._tasks[job.scan_id]

        assert job.cost.total_usd == 0
        assert job.cost.input_tokens == 0
        assert job.cost.output_tokens == 0

    @pytest.mark.asyncio
    async def test_auditor_cost_absent_when_skip_llm(self, tmp_path, monkeypatch):
        manager, data_dir = self._manager(
            tmp_path, monkeypatch, policies=[self._policy()],
        )

        job = await manager.start_scan(domains_group="quick", skip_llm=True)
        await manager._tasks[job.scan_id]

        assert job.cost.total_usd == 0


@pytest.mark.medium
class TestBudgetStop:
    """WP-22b: a scan stops launching further domains once running cost
    reaches budget_usd. max_concurrent=1 makes domain processing order
    deterministic, so "stops within one domain of the cap" is directly
    observable.
    """

    def _manager(self, tmp_path, monkeypatch, *, domain_count=4, cost_per_domain=5.0):
        config = _minimal_config(tmp_path / "config")
        # get_enabled_domains() resolves a group through a set() internally
        # (src/core/config.py), so real multi-domain group order isn't
        # guaranteed - override it directly so domain processing order
        # (with max_concurrent=1) is deterministic for this test.
        ordered_domains = [
            {"id": f"d{i}", "name": f"D{i}", "base_url": f"https://d{i}.gov",
             "start_paths": ["/"]}
            for i in range(domain_count)
        ]
        monkeypatch.setattr(
            config, "get_enabled_domains",
            lambda group: [dict(d) for d in ordered_domains],
        )
        data_dir = tmp_path / "data"
        manager = ScanManager(
            config=config, broadcaster=EventBroadcaster(), data_dir=str(data_dir),
            api_key="sk-ant-test",
        )

        calls = {"n": 0}

        async def _fake_scan():
            calls["n"] += 1
            return []

        mock_scanner = MagicMock()
        mock_scanner.scan = AsyncMock(side_effect=_fake_scan)
        mock_scanner.progress = DomainProgress(
            domain_id="d0", domain_name="D0", status=DomainScanStatus.COMPLETED,
        )
        monkeypatch.setattr(
            "src.orchestration.scan_manager.DomainScanner",
            lambda **kwargs: mock_scanner,
        )
        monkeypatch.setattr(
            "src.orchestration.scan_manager.AsyncCrawler",
            lambda **kwargs: MagicMock(close=AsyncMock()),
        )

        fake_llm = MagicMock()
        fake_llm.cost = CostInfo()
        fake_llm.close = AsyncMock()
        # Idempotent recompute (like the real ClaudeClient.update_cost_estimate)
        # keyed off how many domains actually called scan() so far - a
        # skipped domain (never scans) must not add to cost, and a repeat
        # call (the unconditional one at scan end) must not double-count.
        fake_llm.update_cost_estimate = MagicMock(
            side_effect=lambda: setattr(
                fake_llm.cost, "total_usd", round(calls["n"] * cost_per_domain, 4),
            )
        )
        monkeypatch.setattr(
            "src.orchestration.scan_manager.ClaudeClient", lambda **kwargs: fake_llm,
        )

        return manager, data_dir

    @pytest.mark.asyncio
    async def test_stops_within_one_domain_of_the_cap(self, tmp_path, monkeypatch):
        manager, data_dir = self._manager(tmp_path, monkeypatch)

        job = await manager.start_scan(
            domains_group="quick", skip_llm=False, max_concurrent=1, budget_usd=12.0,
        )
        await manager._tasks[job.scan_id]

        assert job.budget_reached is True
        statuses = {dp.domain_id: dp.status for dp in job.progress.domains}
        # $5/domain, cap $12: d0 ($5) and d1 ($10) run, d2 ($15) crosses the
        # cap and still finishes (in-flight), d3 is skipped.
        assert statuses["d0"] == DomainScanStatus.COMPLETED
        assert statuses["d1"] == DomainScanStatus.COMPLETED
        assert statuses["d2"] == DomainScanStatus.COMPLETED
        assert statuses["d3"] == DomainScanStatus.SKIPPED
        assert job.cost.total_usd == 15.0

    @pytest.mark.asyncio
    async def test_budget_reached_recorded_in_history_and_route_shape(self, tmp_path, monkeypatch):
        manager, data_dir = self._manager(tmp_path, monkeypatch)

        job = await manager.start_scan(
            domains_group="quick", skip_llm=False, max_concurrent=1, budget_usd=12.0,
        )
        await manager._tasks[job.scan_id]

        row = ScanHistoryStore(data_dir=str(data_dir)).list()[0]
        assert row["status"] == "completed_budget_reached"
        # job.status itself stays the enum-constrained COMPLETED.
        assert manager.jobs[job.scan_id].status.value == "completed"
        assert manager.jobs[job.scan_id].budget_reached is True

    @pytest.mark.asyncio
    async def test_no_budget_behaves_exactly_as_before(self, tmp_path, monkeypatch):
        manager, data_dir = self._manager(tmp_path, monkeypatch)

        job = await manager.start_scan(
            domains_group="quick", skip_llm=False, max_concurrent=1,
        )
        await manager._tasks[job.scan_id]

        assert job.budget_reached is False
        assert all(
            dp.status != DomainScanStatus.SKIPPED for dp in job.progress.domains
        )
        assert job.cost.total_usd == 20.0
        row = ScanHistoryStore(data_dir=str(data_dir)).list()[0]
        assert row["status"] == "completed"
