"""Property-style sanity tests for ScanManager.estimate_cost() (WP-39).

No hypothesis - each "property" is expressed as ordinary parametrized
pytest cases over constructed scopes (crawl-only / structured-only / mixed
channel mixes, at scope sizes 0/1/5/50, with deep on/off), the same
config-mocking pattern ``_manager_with_config`` in test_scan_manager.py
already uses. Each test asserts an invariant that must hold for every
combination rather than one specific example.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.core.models import DEFAULT_ANALYSIS_MODEL, DEFAULT_SCREENING_MODEL, DomainProgress
from src.core.pricing import PricingLoader
from src.orchestration.scan_manager import ScanManager
from src.storage.scan_history import ScanHistoryStore


def _manager_with_config(
    get_enabled_domains_return=None, get_enabled_domains_side_effect=None,
    screening_model=None, analysis_model=None,
):
    """Mirrors test_scan_manager.py's helper of the same name."""
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


def _dp(domain_id: str, **overrides) -> DomainProgress:
    defaults = dict(domain_id=domain_id, domain_name=domain_id)
    defaults.update(overrides)
    return DomainProgress(**defaults)


def _crawl_domains(n: int) -> list[dict]:
    return [{"id": f"c{i}", "name": f"C{i}"} for i in range(n)]


def _api_domains(n: int) -> list[dict]:
    return [
        {"id": f"a{i}", "name": f"A{i}", "source_type": "legiscan"} for i in range(n)
    ]


def _mixed_domains(n: int) -> list[dict]:
    """Interleaved crawl/law_apis/transposition domains, so any prefix
    slice of this list is itself a valid mixed-channel scope."""
    out = []
    for i in range(n):
        m = i % 3
        if m == 0:
            out.append({"id": f"m{i}", "name": f"M{i}"})
        elif m == 1:
            out.append({"id": f"m{i}", "name": f"M{i}", "source_type": "legiscan"})
        else:
            out.append({"id": f"m{i}", "name": f"M{i}", "source_type": "eurlex_nim"})
    return out


_BUILDERS = [_crawl_domains, _api_domains, _mixed_domains]
_BUILDER_IDS = ["crawl_only", "structured_only", "mixed"]


@pytest.mark.small
class TestScopeMonotonicity:
    """1: estimated_cost_usd never decreases as the domain scope grows, and
    a prefix-subset scope never costs more than the superset that contains
    it (same channel mix throughout)."""

    @pytest.mark.parametrize("build", _BUILDERS, ids=_BUILDER_IDS)
    def test_prefix_subset_costs_are_non_decreasing(self, build):
        full = build(50)
        costs = [
            _manager_with_config(get_enabled_domains_return=full[:n]).estimate_cost(
                "quick"
            )["estimated_cost_usd"]
            for n in (0, 1, 5, 50)
        ]
        assert costs == sorted(costs)


@pytest.mark.small
class TestDeepNeverCheaperThanStandard:
    """2: deep=True is always >= deep=False for the same scope, at every
    cost level of the estimate (typical, low, high)."""

    @pytest.mark.parametrize("build", _BUILDERS, ids=_BUILDER_IDS)
    @pytest.mark.parametrize("n", [0, 1, 5, 50])
    def test_deep_at_least_standard(self, build, n):
        manager = _manager_with_config(get_enabled_domains_return=build(n))
        standard = manager.estimate_cost("quick", deep=False)
        deep = manager.estimate_cost("quick", deep=True)

        assert deep["estimated_cost_usd"] >= standard["estimated_cost_usd"]
        assert deep["estimated_cost_low_usd"] >= standard["estimated_cost_low_usd"]
        assert deep["estimated_cost_high_usd"] >= standard["estimated_cost_high_usd"]


@pytest.mark.small
class TestCostLevelOrdering:
    """3: with the cost-level machinery mocked the way
    TestEstimateCostRespectsCostLevel does (screening/analysis model pairs
    per level), low <= standard <= high totals for every channel mix."""

    LEVELS = {
        "low": (DEFAULT_SCREENING_MODEL, DEFAULT_SCREENING_MODEL),
        "standard": (DEFAULT_SCREENING_MODEL, DEFAULT_ANALYSIS_MODEL),
        "high": (DEFAULT_ANALYSIS_MODEL, DEFAULT_ANALYSIS_MODEL),
    }

    @pytest.mark.parametrize("build", _BUILDERS, ids=_BUILDER_IDS)
    def test_low_le_standard_le_high(self, build):
        domains = build(10)

        def _cost(level: str) -> float:
            screening_model, analysis_model = self.LEVELS[level]
            manager = _manager_with_config(
                get_enabled_domains_return=domains,
                screening_model=screening_model, analysis_model=analysis_model,
            )
            return manager.estimate_cost("quick")["estimated_cost_usd"]

        assert _cost("low") <= _cost("standard") <= _cost("high")


@pytest.mark.small
class TestChannelSumReconciliation:
    """4: sum(channels[*].cost_usd) + auditor_cost_usd == estimated_cost_usd
    within $0.05 rounding tolerance - and the same for low/high."""

    @pytest.mark.parametrize("build", _BUILDERS, ids=_BUILDER_IDS)
    @pytest.mark.parametrize("n", [0, 1, 5, 50])
    def test_channel_totals_reconcile(self, build, n):
        manager = _manager_with_config(get_enabled_domains_return=build(n))
        result = manager.estimate_cost("quick")

        for total_key, channel_key in (
            ("estimated_cost_usd", "cost_usd"),
            ("estimated_cost_low_usd", "cost_low_usd"),
            ("estimated_cost_high_usd", "cost_high_usd"),
        ):
            channel_sum = sum(c[channel_key] for c in result["channels"].values())
            assert result[total_key] == pytest.approx(
                channel_sum + result["auditor_cost_usd"], abs=0.05,
            )


@pytest.mark.small
class TestOrderingInvariantGrid:
    """5: low <= typical <= high, per channel AND overall, across a grid of
    scope sizes and deep on/off."""

    @pytest.mark.parametrize("build", _BUILDERS, ids=_BUILDER_IDS)
    @pytest.mark.parametrize("n", [0, 1, 5, 50])
    @pytest.mark.parametrize("deep", [False, True])
    def test_low_le_typical_le_high_everywhere(self, build, n, deep):
        manager = _manager_with_config(get_enabled_domains_return=build(n))
        result = manager.estimate_cost("quick", deep=deep)

        assert (
            result["estimated_cost_low_usd"]
            <= result["estimated_cost_usd"]
            <= result["estimated_cost_high_usd"]
        )
        for channel in result["channels"].values():
            assert channel["cost_low_usd"] <= channel["cost_usd"] <= channel["cost_high_usd"]


@pytest.mark.small
class TestFunnelConsistency:
    """6: estimated_analysis_calls <= estimated_screening_calls <=
    estimated_pages, per channel and total, without calibration data."""

    @pytest.mark.parametrize("build", _BUILDERS, ids=_BUILDER_IDS)
    @pytest.mark.parametrize("n", [0, 1, 5, 50])
    def test_funnel_narrows_at_every_stage(self, build, n):
        manager = _manager_with_config(get_enabled_domains_return=build(n))
        result = manager.estimate_cost("quick")

        assert result["estimated_analysis_calls"] <= result["estimated_screening_calls"]
        assert result["estimated_screening_calls"] <= result["estimated_pages"]
        for channel in result["channels"].values():
            assert channel["analysis_calls"] <= channel["screening_calls"]
            assert channel["screening_calls"] <= channel["estimated_items_or_pages"]


def _seed_crawl_history(history: ScanHistoryStore) -> None:
    """Same 3-scan crawl fixture as TestEstimateCostMeasuredRates in
    test_scan_manager.py."""
    base = datetime(2026, 1, 1)
    rows = [("s1", 100, 10, 2, 1), ("s2", 200, 30, 5, 0), ("s3", 50, 2, 0, 0)]
    for scan_id, pages, kw, filtered_screening, llm_skipped in rows:
        history.record_start(
            scan_id=scan_id, domain_group="quick", mode="standard",
            channels=["crawl"], started_at=base,
        )
        history.record_completion(scan_id=scan_id, status="completed", completed_at=base)
        history.record_domains(
            scan_id,
            [(
                _dp(
                    scan_id, pages_crawled=pages, keywords_matched=kw,
                    filtered_screening=filtered_screening, llm_skipped=llm_skipped,
                ),
                "crawl",
            )],
            completed_at=base,
        )


def _seed_structured_history(history: ScanHistoryStore) -> None:
    """2 scans / 3 rows across law_apis + transposition - meets the
    measured-rates threshold for the "structured" bucket."""
    base = datetime(2026, 1, 1)
    for scan_id in ("s1", "s2"):
        history.record_start(
            scan_id=scan_id, domain_group="quick", mode="standard",
            channels=["law_apis"], started_at=base,
        )
        history.record_completion(scan_id=scan_id, status="completed", completed_at=base)
    history.record_domains(
        "s1",
        [
            (_dp("d1", pages_crawled=40, keywords_matched=40, filtered_screening=5), "law_apis"),
            (_dp("d2", pages_crawled=20, keywords_matched=20, filtered_screening=2), "transposition"),
        ],
        completed_at=base,
    )
    history.record_domains(
        "s2",
        [(_dp("d3", pages_crawled=60, keywords_matched=60, filtered_screening=10), "law_apis")],
        completed_at=base,
    )


@pytest.mark.medium
class TestFunnelConsistencyWithMeasuredRates:
    """6 (calibrated variant): the same funnel-narrowing invariant holds
    once ScanHistoryStore.measured_rates() clears its 2-scans/3-rows gate,
    for both the crawl and the structured calibration buckets."""

    def test_crawl_funnel_holds_with_measured_rates(self, tmp_path):
        history = ScanHistoryStore(data_dir=str(tmp_path))
        _seed_crawl_history(history)
        manager = _manager_with_config(get_enabled_domains_return=_crawl_domains(5))
        manager.scan_history_store = history

        result = manager.estimate_cost("quick")

        assert any("measured across" in a for a in result["assumptions"])
        assert result["estimated_analysis_calls"] <= result["estimated_screening_calls"]
        assert result["estimated_screening_calls"] <= result["estimated_pages"]

    def test_structured_funnel_holds_with_measured_rates(self, tmp_path):
        history = ScanHistoryStore(data_dir=str(tmp_path))
        _seed_structured_history(history)
        manager = _manager_with_config(get_enabled_domains_return=_api_domains(5))
        manager.scan_history_store = history

        result = manager.estimate_cost("quick")

        assert any("measured across" in a for a in result["assumptions"])
        assert result["estimated_analysis_calls"] <= result["estimated_screening_calls"]
        assert result["estimated_screening_calls"] <= result["estimated_pages"]


@pytest.mark.medium
class TestCalibrationStaysInUnitInterval:
    """7: measured rates always land in [0, 1] even from absurd seeded
    funnel rows (keywords_matched > pages_crawled, etc.), and the
    "measured" provenance string only appears once the 2-scans/3-rows gate
    passes."""

    def test_keywords_exceeding_pages_stays_clamped(self, tmp_path):
        history = ScanHistoryStore(data_dir=str(tmp_path))
        base = datetime(2026, 1, 1)
        for scan_id in ("s1", "s2"):
            history.record_start(
                scan_id=scan_id, domain_group="quick", mode="standard",
                channels=["crawl"], started_at=base,
            )
            history.record_completion(scan_id=scan_id, status="completed", completed_at=base)
        history.record_domains(
            "s1",
            [
                (_dp("d1", pages_crawled=5, keywords_matched=500,
                     filtered_screening=1000, llm_skipped=1000), "crawl"),
                (_dp("d2", pages_crawled=5, keywords_matched=500,
                     filtered_screening=0, llm_skipped=0), "crawl"),
            ],
            completed_at=base,
        )
        history.record_domains(
            "s2",
            [(_dp("d3", pages_crawled=5, keywords_matched=500,
                  filtered_screening=0, llm_skipped=0), "crawl")],
            completed_at=base,
        )

        rates = history.measured_rates()["crawl"]

        assert 0.0 <= rates["keyword_rate"] <= 1.0
        assert 0.0 <= rates["screening_pass_rate"] <= 1.0
        for metric in ("keyword_rate", "screening_pass_rate"):
            assert 0.0 <= rates["spread"][metric]["p25"] <= 1.0
            assert 0.0 <= rates["spread"][metric]["p75"] <= 1.0

    def test_below_threshold_never_claims_measured(self, tmp_path):
        history = ScanHistoryStore(data_dir=str(tmp_path))
        domains = [{"id": "d1", "name": "D1"}]
        manager = _manager_with_config(get_enabled_domains_return=domains)
        manager.scan_history_store = history

        result = manager.estimate_cost("quick")

        assert not any("measured across" in a for a in result["assumptions"])

    def test_above_threshold_claims_measured_even_with_absurd_values(self, tmp_path):
        history = ScanHistoryStore(data_dir=str(tmp_path))
        base = datetime(2026, 1, 1)
        for scan_id in ("s1", "s2"):
            history.record_start(
                scan_id=scan_id, domain_group="quick", mode="standard",
                channels=["crawl"], started_at=base,
            )
            history.record_completion(scan_id=scan_id, status="completed", completed_at=base)
        history.record_domains(
            "s1",
            [
                (_dp("d1", pages_crawled=2, keywords_matched=999), "crawl"),
                (_dp("d2", pages_crawled=2, keywords_matched=999), "crawl"),
            ],
            completed_at=base,
        )
        history.record_domains(
            "s2",
            [(_dp("d3", pages_crawled=2, keywords_matched=999), "crawl")],
            completed_at=base,
        )

        domains = [{"id": "d1", "name": "D1"}]
        manager = _manager_with_config(get_enabled_domains_return=domains)
        manager.scan_history_store = history

        result = manager.estimate_cost("quick")

        assert any("measured across" in a for a in result["assumptions"])
        assert result["estimated_cost_usd"] >= 0


@pytest.mark.small
class TestRealPricingSanity:
    """8: the real config/pricing.yaml loads, every price is positive, and
    Haiku is strictly cheaper than Sonnet on both input and output."""

    def test_every_price_is_positive(self):
        loader = PricingLoader(config_dir="config")

        assert loader.models, "config/pricing.yaml must define at least one model"
        for model_id, price in loader.models.items():
            assert price.input_per_mtok > 0, model_id
            assert price.output_per_mtok > 0, model_id

    def test_haiku_strictly_cheaper_than_sonnet(self):
        loader = PricingLoader(config_dir="config")
        haiku = loader.pricing_for(DEFAULT_SCREENING_MODEL)
        sonnet = loader.pricing_for(DEFAULT_ANALYSIS_MODEL)

        assert haiku.input_per_mtok < sonnet.input_per_mtok
        assert haiku.output_per_mtok < sonnet.output_per_mtok
