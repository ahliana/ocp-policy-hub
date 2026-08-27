"""Adversarial / degenerate-input tests for the estimator + budget-stop +
API boundary (WP-39). Reuses TestEstimateCost*'s config-mocking helpers
(test_scan_manager.py) and TestBudgetStop's harness (also
test_scan_manager.py) rather than inventing new fixtures.
"""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.models import (
    CostInfo, DEFAULT_ANALYSIS_MODEL, DEFAULT_SCREENING_MODEL, DomainProgress,
    DomainScanStatus,
)
from src.core.config import ConfigLoader
from src.core.pricing import PricingLoader
from src.orchestration.events import EventBroadcaster
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


def _assert_no_nan_inf_or_negative(value, path: str = "root") -> None:
    """Recursively walk an estimate_cost() response: no float may be NaN or
    inf, and no number may be negative anywhere in the tree."""
    if isinstance(value, dict):
        for key, sub in value.items():
            _assert_no_nan_inf_or_negative(sub, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for i, sub in enumerate(value):
            _assert_no_nan_inf_or_negative(sub, f"{path}[{i}]")
    elif isinstance(value, bool):
        pass
    elif isinstance(value, (int, float)):
        if isinstance(value, float):
            assert not math.isnan(value), f"{path} is NaN"
            assert not math.isinf(value), f"{path} is inf"
        assert value >= 0, f"{path} is negative: {value}"


@pytest.mark.small
class TestEmptyScope:
    """1: a zero-domain scope returns a well-formed shape, zero-ish costs,
    low <= typical <= high, and never raises."""

    def test_zero_domains_returns_well_formed_result(self):
        manager = _manager_with_config(get_enabled_domains_return=[])

        result = manager.estimate_cost("quick")

        assert result["domain_count"] == 0
        assert result["estimated_pages"] == 0
        assert result["estimated_keyword_passes"] == 0
        assert result["estimated_screening_calls"] == 0
        assert result["estimated_analysis_calls"] == 0
        assert result["channels"] == {}
        assert (
            result["estimated_cost_low_usd"]
            <= result["estimated_cost_usd"]
            <= result["estimated_cost_high_usd"]
        )
        # Only the flat, unconditional auditor cost remains.
        assert result["estimated_cost_usd"] == pytest.approx(result["auditor_cost_usd"])


_UNKNOWN_PRICING_YAML = """
models:
  cheap-model:
    input_per_mtok: 1.0
    output_per_mtok: 2.0
  expensive-model:
    input_per_mtok: 50.0
    output_per_mtok: 100.0
estimator:
  screening_input: 2000
  screening_output: 50
  analysis_input: 20000
  analysis_output: 1000
  auditor_input: 5000
  auditor_output: 2000
  structured_items_per_source: 40
"""


@pytest.mark.medium
class TestUnknownModelFallback:
    """2: an unrecognized model id falls back to the most expensive known
    model (never priced as if it were cheap) and logs a warning."""

    def _manager(self, tmp_path, model_id: str) -> ScanManager:
        (tmp_path / "pricing.yaml").write_text(_UNKNOWN_PRICING_YAML, encoding="utf-8")
        domains = [{"id": f"d{i}", "name": f"D{i}"} for i in range(5)]
        manager = _manager_with_config(
            get_enabled_domains_return=domains,
            screening_model=model_id, analysis_model=model_id,
        )
        manager._pricing = PricingLoader(config_dir=str(tmp_path))
        return manager

    def test_unknown_model_never_cheaper_than_the_cheapest_known_model(self, tmp_path):
        unknown_cost = self._manager(tmp_path, "totally-unknown-model").estimate_cost(
            "quick"
        )["estimated_cost_usd"]
        cheap_cost = self._manager(tmp_path, "cheap-model").estimate_cost("quick")[
            "estimated_cost_usd"
        ]

        assert unknown_cost >= cheap_cost

    def test_unknown_model_logs_a_warning(self, tmp_path, caplog):
        manager = self._manager(tmp_path, "totally-unknown-model")

        with caplog.at_level(logging.WARNING, logger="src.core.pricing"):
            manager.estimate_cost("quick")

        assert any(
            "totally-unknown-model" in r.message or "unknown" in r.message.lower()
            for r in caplog.records
        )


@pytest.mark.medium
class TestAbsurdMeasuredRatesStaySane:
    """3: absurd seeded measured rates (all-zero rows, keywords > pages,
    negative filtered/skipped counts) never make estimate_cost() return a
    negative number, NaN, or inf anywhere in its response."""

    def test_inverted_and_negative_funnel_rows(self, tmp_path):
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
                (_dp("d1", pages_crawled=3, keywords_matched=9000,
                     filtered_screening=-50, llm_skipped=-10), "crawl"),
                (_dp("d2", pages_crawled=0, keywords_matched=0), "crawl"),
            ],
            completed_at=base,
        )
        history.record_domains(
            "s2",
            [(_dp("d3", pages_crawled=3, keywords_matched=9000,
                  filtered_screening=-50, llm_skipped=-10), "crawl")],
            completed_at=base,
        )

        domains = [{"id": f"d{i}", "name": f"D{i}"} for i in range(5)]
        manager = _manager_with_config(get_enabled_domains_return=domains)
        manager.scan_history_store = history

        result = manager.estimate_cost("quick")

        _assert_no_nan_inf_or_negative(result)
        # Redundant with the recursive walk above; present because the
        # assert-quality AST gate cannot see asserts inside helpers.
        assert result["estimated_cost_usd"] >= 0

    def test_all_zero_rows(self, tmp_path):
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
                (_dp("d1", pages_crawled=0, keywords_matched=0), "crawl"),
                (_dp("d2", pages_crawled=0, keywords_matched=0), "crawl"),
            ],
            completed_at=base,
        )
        history.record_domains(
            "s2", [(_dp("d3", pages_crawled=0, keywords_matched=0), "crawl")],
            completed_at=base,
        )

        domains = [{"id": f"d{i}", "name": f"D{i}"} for i in range(5)]
        manager = _manager_with_config(get_enabled_domains_return=domains)
        manager.scan_history_store = history

        result = manager.estimate_cost("quick")

        _assert_no_nan_inf_or_negative(result)
        # Redundant with the recursive walk above; present because the
        # assert-quality AST gate cannot see asserts inside helpers.
        assert result["estimated_cost_usd"] >= 0


@pytest.mark.small
class TestFullScaleScope:
    """4: a 404-domain mixed-channel scope completes fast and every
    ordering/funnel invariant still holds at that scale."""

    def test_404_domains_completes_fast_and_stays_well_ordered(self):
        domains = []
        for i in range(404):
            m = i % 3
            if m == 0:
                domains.append({"id": f"d{i}", "name": f"D{i}"})
            elif m == 1:
                domains.append({"id": f"d{i}", "name": f"D{i}", "source_type": "legiscan"})
            else:
                domains.append({"id": f"d{i}", "name": f"D{i}", "source_type": "eurlex_nim"})
        manager = _manager_with_config(get_enabled_domains_return=domains)

        start = time.monotonic()
        result = manager.estimate_cost("quick")
        elapsed = time.monotonic() - start

        assert elapsed < 2.0
        assert result["domain_count"] == 404
        assert (
            result["estimated_cost_low_usd"]
            <= result["estimated_cost_usd"]
            <= result["estimated_cost_high_usd"]
        )
        for channel in result["channels"].values():
            assert channel["analysis_calls"] <= channel["screening_calls"]
            assert channel["screening_calls"] <= channel["estimated_items_or_pages"]
            assert channel["cost_low_usd"] <= channel["cost_usd"] <= channel["cost_high_usd"]


def _minimal_config(config_dir) -> ConfigLoader:
    """Same real, minimal config directory as test_scan_manager.py's helper
    of the same name."""
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


@pytest.mark.medium
class TestBudgetEdgeCases:
    """5 & 6: degenerate budget_usd values, reusing TestBudgetStop's harness
    (test_scan_manager.py) - a fake ClaudeClient whose cost is a function of
    how many domains have actually scanned so far."""

    def _manager(self, tmp_path, monkeypatch, *, domain_count=4, cost_per_domain=5.0):
        config = _minimal_config(tmp_path / "config")
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
    async def test_zero_budget_stops_after_first_domain_and_never_crashes(
        self, tmp_path, monkeypatch,
    ):
        manager, data_dir = self._manager(tmp_path, monkeypatch)

        job = await manager.start_scan(
            domains_group="quick", skip_llm=False, max_concurrent=1, budget_usd=0.0,
        )
        await manager._tasks[job.scan_id]

        assert job.budget_reached is True
        statuses = {dp.domain_id: dp.status for dp in job.progress.domains}
        # The first in-flight domain always finishes; a zero cap trips the
        # check the instant it completes, so nothing else launches.
        assert statuses["d0"] == DomainScanStatus.COMPLETED
        assert statuses["d1"] == DomainScanStatus.SKIPPED
        assert statuses["d2"] == DomainScanStatus.SKIPPED
        assert statuses["d3"] == DomainScanStatus.SKIPPED
        row = ScanHistoryStore(data_dir=str(data_dir)).list()[0]
        assert row["status"] == "completed_budget_reached"

    @pytest.mark.asyncio
    async def test_ceiling_smaller_than_one_domain_stops_after_first(
        self, tmp_path, monkeypatch,
    ):
        manager, data_dir = self._manager(tmp_path, monkeypatch, cost_per_domain=5.0)

        job = await manager.start_scan(
            domains_group="quick", skip_llm=False, max_concurrent=1, budget_usd=1.0,
        )
        await manager._tasks[job.scan_id]

        assert job.budget_reached is True
        statuses = {dp.domain_id: dp.status for dp in job.progress.domains}
        assert statuses["d0"] == DomainScanStatus.COMPLETED
        assert statuses["d1"] == DomainScanStatus.SKIPPED
        row = ScanHistoryStore(data_dir=str(data_dir)).list()[0]
        assert row["status"] == "completed_budget_reached"


# --- API boundary ---------------------------------------------------------


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.domains_config = {
        "domains": [
            {
                "id": "test_gov", "name": "Test Gov", "base_url": "https://test.gov",
                "enabled": True, "region": ["us"], "category": "government",
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
def client(mock_config, mock_store, mock_manager, mock_broadcaster, monkeypatch):
    from fastapi.testclient import TestClient
    from src.api.app import app
    from src.api import deps

    # src/api/app.py loads the project .env (override=True) the first time
    # it is imported in a process. Whichever test happens to import it
    # first pays that cost - clear it again here so this file's result
    # does not depend on import order across the suite (tests/conftest.py's
    # autouse _no_ambient_env fixture only strips it BEFORE this import).
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)

    app.dependency_overrides[deps.get_config] = lambda: mock_config
    app.dependency_overrides[deps.get_policy_store] = lambda: mock_store
    app.dependency_overrides[deps.get_scan_manager] = lambda: mock_manager
    app.dependency_overrides[deps.get_broadcaster] = lambda: mock_broadcaster

    # raise_server_exceptions=False: an adversarial payload that reaches an
    # unhandled exception must be observed as the real HTTP status a
    # deployed server would send (500), not re-raised into the test.
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.mark.medium
class TestBudgetUsdApiBoundary:
    """7: POST /api/scans with pathological budget_usd values never 500s.

    -1 and "abc" fail ScanRequest's Pydantic validation (ge=0 / float
    coercion) -> 422. 1e308 is a valid, merely huge, float with no
    configured upper bound, so it is accepted (200) - that is intentional,
    not a defect: a budget cap this large is never reached in practice.
    """

    def test_negative_budget_is_422(self, client):
        response = client.post(
            "/api/scans", json={"domains": "quick", "skip_llm": True, "budget_usd": -1.0},
        )
        assert response.status_code == 422

    def test_non_numeric_budget_is_422(self, client):
        response = client.post(
            "/api/scans", json={"domains": "quick", "skip_llm": True, "budget_usd": "abc"},
        )
        assert response.status_code == 422

    def test_huge_but_finite_budget_is_accepted_not_500(self, client, mock_manager):
        from src.core.models import ScanJob, ScanStatus

        job = ScanJob(scan_id="s1", status=ScanStatus.RUNNING, domain_count=1)
        mock_manager.start_scan = AsyncMock(return_value=job)

        response = client.post(
            "/api/scans",
            json={"domains": "quick", "skip_llm": True, "budget_usd": 1e308},
        )

        assert response.status_code != 500
        assert response.status_code == 200
        assert mock_manager.start_scan.await_args.kwargs["budget_usd"] == 1e308

    def test_nan_budget_never_500s(self, client):
        """WP-39 finding, fixed in src/api/app.py's _validation_error_handler:
        FastAPI's default 422 handler echoed the rejected NaN back through
        Starlette's allow_nan=False JSON encoder, crashing the error path
        itself into a 500 - for NaN/Infinity in ANY numeric field, not just
        budget_usd. The custom handler stringifies non-finite floats."""
        response = client.post(
            "/api/scans",
            content='{"domains": "quick", "skip_llm": true, "budget_usd": NaN}',
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422
        assert "nan" in response.text.lower()


@pytest.mark.small
class TestChannelsMatchNothing:
    """8: estimate_cost with channels that select no domain (["news"], or
    an empty list) is a well-formed zero-domain result, not an exception."""

    def test_news_channel_matches_no_domain(self):
        domains = [
            {"id": "c1", "name": "C1"},
            {"id": "a1", "name": "A1", "source_type": "legiscan"},
        ]
        manager = _manager_with_config(get_enabled_domains_return=domains)

        result = manager.estimate_cost("quick", channels=["news"])

        assert result["domain_count"] == 0
        assert result["channels"] == {}
        assert (
            result["estimated_cost_low_usd"]
            <= result["estimated_cost_usd"]
            <= result["estimated_cost_high_usd"]
        )

    def test_empty_channels_list_matches_no_domain(self):
        domains = [{"id": "c1", "name": "C1"}]
        manager = _manager_with_config(get_enabled_domains_return=domains)

        result = manager.estimate_cost("quick", channels=[])

        assert result["domain_count"] == 0
        assert result["channels"] == {}
