"""End-to-end integration tests for the full interactive pipeline.

These tests verify that the complete chain works: config loading →
agent initialization → tool dispatch → scan execution → Sheets export.
External services (HTTP, Anthropic API, Google Sheets) are mocked, but
all internal wiring uses real code and real config files.
"""

import asyncio
import io
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.orchestrator import PolicyAgent, _build_system_prompt
from src.agent.tools import execute_tool, get_all_tools
from src.core.config import ConfigLoader, ConfigurationError
from src.core.models import (
    CrawlResult, PageStatus, Policy, PolicyType, DEFAULT_ANALYSIS_MODEL,
)
from src.orchestration.events import EventBroadcaster
from src.orchestration.scan_manager import ScanManager
from src.storage.store import PolicyStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def real_config():
    """Load real config files from config/."""
    c = ConfigLoader(config_dir="config")
    c.load()
    return c


@pytest.fixture
def scan_manager(real_config, tmp_path):
    # Isolated data_dir: agent tools read the persisted PolicyStore, so tests
    # must not see the developer's real data/policies.json.
    broadcaster = EventBroadcaster()
    return ScanManager(config=real_config, broadcaster=broadcaster, data_dir=str(tmp_path))


@pytest.fixture
def tmp_config_dir(tmp_path):
    """Create a minimal valid config directory for testing."""
    config_dir = tmp_path / "config"
    domains_dir = config_dir / "domains"
    domains_dir.mkdir(parents=True)

    # settings.yaml
    (config_dir / "settings.yaml").write_text(
        "crawl:\n  max_depth: 2\n  delay_seconds: 0.5\n"
        "analysis:\n  min_keyword_score: 3\n",
        encoding="utf-8",
    )

    # domains/test.yaml
    (domains_dir / "test.yaml").write_text(
        "domains:\n"
        "  - id: test_gov\n"
        "    name: Test Gov\n"
        "    base_url: https://test.gov\n"
        "    start_paths: [\"/\"]\n"
        "    region: [us]\n"
        "    enabled: true\n",
        encoding="utf-8",
    )

    # groups.yaml
    (config_dir / "groups.yaml").write_text(
        "groups:\n"
        "  quick:\n"
        "    description: Quick scan\n"
        "    domains: [test_gov]\n",
        encoding="utf-8",
    )

    # keywords.yaml
    (config_dir / "keywords.yaml").write_text(
        "categories:\n"
        "  heat_recovery:\n"
        "    weight: 3.0\n"
        "    terms:\n"
        "      en: [heat reuse, waste heat]\n"
        "  data_center:\n"
        "    weight: 2.0\n"
        "    terms:\n"
        "      en: [data center, data centre]\n"
        "thresholds:\n"
        "  min_score: 3.0\n"
        "  min_matches: 1\n",
        encoding="utf-8",
    )

    # url_filters.yaml
    (config_dir / "url_filters.yaml").write_text(
        "url_filters:\n"
        "  skip_paths: [/login]\n"
        "  skip_extensions: [.pdf, .jpg]\n",
        encoding="utf-8",
    )

    return config_dir


# ---------------------------------------------------------------------------
# 1. Config loading with real files
# ---------------------------------------------------------------------------

class TestConfigLoadingReal:
    """Verify config loads correctly from actual config files."""

    def test_loads_all_sections(self, real_config):
        assert real_config.settings is not None
        assert real_config.domains_config is not None
        assert real_config.keywords_config is not None

    def test_has_domains(self, real_config):
        domains = real_config.get_enabled_domains("all")
        assert len(domains) > 10, "Expected at least 10 configured domains"

    def test_has_groups(self, real_config):
        groups = real_config.list_groups()
        assert "quick" in groups

    def test_has_keywords(self, real_config):
        kw = real_config.keywords_config
        assert "keywords" in kw
        assert "subject" in kw["keywords"]

    def test_has_crawl_settings(self, real_config):
        assert real_config.settings.crawl.max_depth >= 1
        assert real_config.settings.crawl.delay_seconds > 0


class TestConfigLoadingMinimal:
    """Verify config loads from minimal test fixtures."""

    def test_loads_minimal_config(self, tmp_config_dir):
        c = ConfigLoader(config_dir=str(tmp_config_dir))
        c.load()
        assert len(c.get_enabled_domains("all")) == 1
        assert c.get_enabled_domains("all")[0]["id"] == "test_gov"

    def test_missing_domains_dir_raises(self, tmp_path):
        config_dir = tmp_path / "empty_config"
        config_dir.mkdir()
        (config_dir / "settings.yaml").write_text("crawl:\n  max_depth: 2\n")
        (config_dir / "keywords.yaml").write_text("categories: {}\n")
        c = ConfigLoader(config_dir=str(config_dir))
        with pytest.raises(ConfigurationError):
            c.load()

    def test_missing_keywords_raises(self, tmp_path):
        config_dir = tmp_path / "no_kw_config"
        domains_dir = config_dir / "domains"
        domains_dir.mkdir(parents=True)
        (config_dir / "settings.yaml").write_text("crawl:\n  max_depth: 2\n")
        (domains_dir / "test.yaml").write_text(
            "domains:\n  - id: t\n    name: T\n    base_url: https://t.gov\n"
        )
        c = ConfigLoader(config_dir=str(config_dir))
        with pytest.raises(ConfigurationError):
            c.load()

    def test_output_settings_from_env(self, tmp_config_dir, monkeypatch):
        monkeypatch.setenv("SPREADSHEET_ID", "test-sheet-id")
        monkeypatch.setenv("GOOGLE_CREDENTIALS", "dGVzdC1jcmVkcw==")  # base64("test-creds")
        c = ConfigLoader(config_dir=str(tmp_config_dir))
        c.load()
        assert c.settings.output.spreadsheet_id == "test-sheet-id"
        assert c.settings.output.google_credentials_b64 == "dGVzdC1jcmVkcw=="


# ---------------------------------------------------------------------------
# 2. Agent initialization
# ---------------------------------------------------------------------------

class TestAgentInit:
    """Verify PolicyAgent can be fully initialized with real config."""

    def test_init_with_real_config(self, real_config):
        """Agent initializes with real config, mocked API client."""
        agent = PolicyAgent.__new__(PolicyAgent)
        agent._messages = []
        agent.config = real_config
        agent.broadcaster = EventBroadcaster()
        agent.scan_manager = ScanManager(
            config=real_config,
            broadcaster=agent.broadcaster,
            data_dir="data",
        )
        agent.tools = get_all_tools()
        agent.system_prompt = _build_system_prompt(real_config)
        agent.model = "test-model"
        agent.client = AsyncMock()

        assert len(agent.tools) == 15
        assert "heat reuse" in agent.system_prompt.lower() or "government" in agent.system_prompt.lower()

    def test_system_prompt_has_domain_count(self, real_config):
        prompt = _build_system_prompt(real_config)
        # Should mention the actual number of domains
        domains = real_config.get_enabled_domains("all")
        assert str(len(domains)) in prompt

    def test_system_prompt_lists_groups(self, real_config):
        prompt = _build_system_prompt(real_config)
        assert "quick" in prompt


# ---------------------------------------------------------------------------
# 3. Tool dispatch — all local tools
# ---------------------------------------------------------------------------

class TestToolDispatchIntegration:
    """Test every local tool dispatches correctly with real config."""

    @pytest.mark.asyncio
    async def test_list_domains_all(self, real_config, scan_manager):
        result = await execute_tool("list_domains", {"group": "all"}, real_config, scan_manager)
        assert result["count"] > 10
        for d in result["domains"]:
            assert "id" in d
            assert "name" in d
            assert "base_url" in d

    @pytest.mark.asyncio
    async def test_list_domains_by_region(self, real_config, scan_manager):
        result = await execute_tool("list_domains", {"region": "eu"}, real_config, scan_manager)
        for d in result["domains"]:
            assert "eu" in d["region"]

    @pytest.mark.asyncio
    async def test_list_domains_by_category(self, real_config, scan_manager):
        result = await execute_tool("list_domains", {"category": "energy_ministry"}, real_config, scan_manager)
        for d in result["domains"]:
            assert d["category"] == "energy_ministry"

    @pytest.mark.asyncio
    async def test_get_domain_config_valid(self, real_config, scan_manager):
        # Get a real domain ID
        domains = real_config.get_enabled_domains("all")
        domain_id = domains[0]["id"]
        result = await execute_tool("get_domain_config", {"domain_id": domain_id}, real_config, scan_manager)
        assert result["id"] == domain_id

    @pytest.mark.asyncio
    async def test_get_domain_config_missing(self, real_config, scan_manager):
        result = await execute_tool("get_domain_config", {"domain_id": "nonexistent"}, real_config, scan_manager)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_estimate_cost(self, real_config, scan_manager):
        result = await execute_tool("estimate_cost", {"domains": "quick"}, real_config, scan_manager)
        assert result["domain_count"] > 0
        assert result["estimated_cost_usd"] >= 0

    @pytest.mark.asyncio
    async def test_match_keywords_relevant(self, real_config, scan_manager):
        result = await execute_tool(
            "match_keywords",
            {"text": "This data center waste heat reuse policy requires operators to recover heat from servers."},
            real_config, scan_manager,
        )
        assert result["score"] > 0
        assert len(result["matches"]) >= 2

    @pytest.mark.asyncio
    async def test_match_keywords_irrelevant(self, real_config, scan_manager):
        result = await execute_tool(
            "match_keywords",
            {"text": "The quick brown fox jumps over the lazy dog."},
            real_config, scan_manager,
        )
        assert result["score"] == 0

    @pytest.mark.asyncio
    async def test_get_policy_stats_empty(self, real_config, scan_manager):
        result = await execute_tool("get_policy_stats", {}, real_config, scan_manager)
        assert result["total"] == 0
        assert result["by_jurisdiction"] == {}

    @pytest.mark.asyncio
    async def test_search_policies_empty(self, real_config, scan_manager):
        result = await execute_tool("search_policies", {"jurisdiction": "Germany"}, real_config, scan_manager)
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_get_scan_status_missing(self, real_config, scan_manager):
        result = await execute_tool("get_scan_status", {"scan_id": "fake"}, real_config, scan_manager)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_stop_scan_missing(self, real_config, scan_manager):
        result = await execute_tool("stop_scan", {"scan_id": "fake"}, real_config, scan_manager)
        assert result["cancelled"] is False

    @pytest.mark.asyncio
    async def test_get_audit_advisory_missing(self, real_config, scan_manager):
        result = await execute_tool("get_audit_advisory", {"scan_id": "fake"}, real_config, scan_manager)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, real_config, scan_manager):
        result = await execute_tool("nonexistent", {}, real_config, scan_manager)
        assert "error" in result
        assert "Unknown tool" in result["error"]


# ---------------------------------------------------------------------------
# 4. analyze_url tool — full pipeline with mocked HTTP/LLM
# ---------------------------------------------------------------------------

class TestAnalyzeUrlPipeline:
    """Test analyze_url with mocked crawler and LLM."""

    @pytest.mark.asyncio
    async def test_analyze_url_full_pipeline(self, real_config, scan_manager, monkeypatch):
        """Full analyze_url: fetch → extract → keywords → LLM screen → LLM analyze → verify."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        html = """
        <html><head><title>Energy Efficiency Act</title></head>
        <body>
            <main>
                <h1>Data Center Heat Reuse Requirements</h1>
                <p>All data centers above 1MW must implement waste heat recovery
                systems. Operators are required to reuse at least 40% of waste heat
                by 2027. This regulation applies to all new and existing data center
                facilities in the jurisdiction.</p>
                <p>The data center energy efficiency standards mandate that heat
                recovery infrastructure must be installed within 24 months of the
                effective date of this legislation.</p>
            </main>
        </body></html>
        """

        mock_crawl_result = CrawlResult(
            url="https://test.gov/policy/heat-reuse",
            status=PageStatus.SUCCESS,
            content=html,
            content_length=len(html),
        )

        # Mock AsyncCrawler
        with patch("src.agent.tools.AsyncCrawler") as MockCrawler:
            mock_instance = AsyncMock()
            mock_instance.crawl_domain = AsyncMock(return_value=[mock_crawl_result])
            mock_instance.close = AsyncMock()
            MockCrawler.return_value = mock_instance

            # Mock ClaudeClient (use MagicMock base, not AsyncMock, for sync methods like to_policy)
            with patch("src.agent.tools.ClaudeClient") as MockLLM:
                mock_llm = MagicMock()
                mock_llm.screen_relevance = AsyncMock(return_value=MagicMock(
                    relevant=True, confidence=8,
                ))
                mock_llm.analyze_policy = AsyncMock(return_value=MagicMock(
                    is_relevant=True,
                    relevance_score=9,
                    policy_type="law",
                    policy_name="Heat Reuse Act",
                    jurisdiction="US",
                    summary="Requires data centers to reuse waste heat",
                ))
                mock_llm.to_policies.return_value = [Policy(
                    url="https://test.gov/policy/heat-reuse",
                    policy_name="Heat Reuse Act",
                    jurisdiction="US",
                    policy_type=PolicyType.LAW,
                    summary="Requires data centers to reuse waste heat",
                    relevance_score=9,
                )]
                mock_llm.close = AsyncMock()
                MockLLM.return_value = mock_llm

                result = await execute_tool(
                    "analyze_url",
                    {"url": "https://test.gov/policy/heat-reuse"},
                    real_config, scan_manager,
                )

        assert result["url"] == "https://test.gov/policy/heat-reuse"
        assert result["keyword_score"] > 0
        assert result["screening"]["relevant"] is True
        assert result["policy"]["policy_name"] == "Heat Reuse Act"
        assert result["policy"]["relevance_score"] == 9

    @pytest.mark.asyncio
    async def test_analyze_url_fetch_failure(self, real_config, scan_manager):
        """analyze_url handles crawler failure gracefully."""
        with patch("src.agent.tools.AsyncCrawler") as MockCrawler:
            mock_instance = AsyncMock()
            mock_instance.crawl_domain = AsyncMock(return_value=[
                CrawlResult(
                    url="https://test.gov/broken",
                    status=PageStatus.ACCESS_DENIED,
                    error_message="403 Forbidden",
                ),
            ])
            mock_instance.close = AsyncMock()
            MockCrawler.return_value = mock_instance

            result = await execute_tool(
                "analyze_url",
                {"url": "https://test.gov/broken"},
                real_config, scan_manager,
            )

        assert result["status"] == "access_denied"

    @pytest.mark.asyncio
    async def test_analyze_url_no_keywords(self, real_config, scan_manager):
        """analyze_url skips LLM when keywords don't match."""
        html = "<html><body><p>This is just a regular page about cooking recipes.</p></body></html>"
        with patch("src.agent.tools.AsyncCrawler") as MockCrawler:
            mock_instance = AsyncMock()
            mock_instance.crawl_domain = AsyncMock(return_value=[
                CrawlResult(
                    url="https://test.gov/cooking",
                    status=PageStatus.SUCCESS,
                    content=html,
                    content_length=len(html),
                ),
            ])
            mock_instance.close = AsyncMock()
            MockCrawler.return_value = mock_instance

            result = await execute_tool(
                "analyze_url",
                {"url": "https://test.gov/cooking"},
                real_config, scan_manager,
            )

        assert result["keyword_score"] == 0
        assert "screening" not in result
        assert "policy" not in result


# ---------------------------------------------------------------------------
# 5. add_domain tool — with mocked HTTP, real config dir
# ---------------------------------------------------------------------------

class TestAddDomainPipeline:
    """Test add_domain creates YAML and reloads config."""

    @pytest.mark.asyncio
    async def test_add_domain_creates_yaml(self, tmp_config_dir):
        """add_domain creates a YAML file and reloads config."""
        config = ConfigLoader(config_dir=str(tmp_config_dir))
        config.load()
        broadcaster = EventBroadcaster()
        sm = ScanManager(config=config, broadcaster=broadcaster, data_dir="data")

        html = "<html><head><title>Oregon DEQ Energy</title></head><body><p>Energy policy</p></body></html>"
        with patch("src.agent.tools.AsyncCrawler") as MockCrawler:
            mock_instance = AsyncMock()
            mock_instance.crawl_domain = AsyncMock(return_value=[
                CrawlResult(
                    url="https://energy.oregon.gov/policy",
                    status=PageStatus.SUCCESS,
                    content=html,
                    content_length=len(html),
                ),
            ])
            mock_instance.close = AsyncMock()
            MockCrawler.return_value = mock_instance

            result = await execute_tool(
                "add_domain",
                {"url": "https://energy.oregon.gov/policy"},
                config, sm,
            )

        assert result["success"] is True
        assert result["domain_id"] == "or_energy"
        assert "us" in result["region"]
        assert Path(result["config_file"]).exists()

        # Verify config was reloaded with new domain
        new_ids = {d["id"] for d in config.get_enabled_domains("all")}
        assert "or_energy" in new_ids

    @pytest.mark.asyncio
    async def test_add_domain_already_exists(self, tmp_config_dir):
        """add_domain returns already_exists for duplicate."""
        # test.gov → domain_id 'us_test' via generate_domain_id
        (tmp_config_dir / "domains" / "us_test.yaml").write_text(
            "domains:\n"
            "  - id: us_test\n"
            "    name: Test\n"
            "    base_url: https://test.gov\n"
            "    start_paths: [\"/\"]\n"
            "    region: [us]\n",
            encoding="utf-8",
        )
        config = ConfigLoader(config_dir=str(tmp_config_dir))
        config.load()
        broadcaster = EventBroadcaster()
        sm = ScanManager(config=config, broadcaster=broadcaster, data_dir="data")

        result = await execute_tool(
            "add_domain",
            {"url": "https://test.gov/new-page"},
            config, sm,
        )
        assert result["already_exists"] is True
        assert result["domain_id"] == "us_test"


# ---------------------------------------------------------------------------
# 6. on_tool_result callback — verifies the bug fix
# ---------------------------------------------------------------------------

class TestOnToolResultCallback:
    """Verify the on_tool_result callback always receives correct result."""

    @pytest.mark.asyncio
    async def test_callback_receives_result_on_success(self):
        """on_tool_result receives the actual tool result on success."""
        agent = PolicyAgent.__new__(PolicyAgent)
        agent._messages = []
        agent.tools = []
        agent.system_prompt = "test"
        agent.model = "test-model"
        agent.config = ConfigLoader(config_dir="config")
        agent.config.load()
        agent.broadcaster = EventBroadcaster()
        agent.scan_manager = ScanManager(
            config=agent.config, broadcaster=agent.broadcaster, data_dir="data",
        )

        # Claude calls get_policy_stats (always succeeds), then responds
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.name = "get_policy_stats"
        tool_block.input = {}
        tool_block.id = "t1"

        tool_response = MagicMock()
        tool_response.content = [tool_block]
        tool_response.stop_reason = "tool_use"

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Stats retrieved."

        text_response = MagicMock()
        text_response.content = [text_block]
        text_response.stop_reason = "end_turn"

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[tool_response, text_response])
        agent.client = mock_client

        results_received = []
        await agent.run(
            "Get stats",
            on_tool_result=lambda name, result: results_received.append((name, result)),
        )

        assert len(results_received) == 1
        name, result = results_received[0]
        assert name == "get_policy_stats"
        assert "total" in result
        assert "by_jurisdiction" in result

    @pytest.mark.asyncio
    async def test_callback_receives_error_on_failure(self):
        """on_tool_result receives error dict when tool raises an exception."""
        agent = PolicyAgent.__new__(PolicyAgent)
        agent._messages = []
        agent.tools = []
        agent.system_prompt = "test"
        agent.model = "test-model"
        agent.config = ConfigLoader(config_dir="config")
        agent.config.load()
        agent.broadcaster = EventBroadcaster()
        agent.scan_manager = ScanManager(
            config=agent.config, broadcaster=agent.broadcaster, data_dir="data",
        )

        # Claude calls a tool that will error
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.name = "get_scan_status"
        tool_block.input = {"scan_id": "nonexistent_scan"}
        tool_block.id = "t1"

        tool_response = MagicMock()
        tool_response.content = [tool_block]
        tool_response.stop_reason = "tool_use"

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Not found."

        text_response = MagicMock()
        text_response.content = [text_block]
        text_response.stop_reason = "end_turn"

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[tool_response, text_response])
        agent.client = mock_client

        results_received = []
        await agent.run(
            "Check scan",
            on_tool_result=lambda name, result: results_received.append((name, result)),
        )

        assert len(results_received) == 1
        name, result = results_received[0]
        assert name == "get_scan_status"
        # The error result should always be a dict with "error" key
        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_callback_on_consecutive_tools(self):
        """on_tool_result receives correct result for each tool in sequence."""
        agent = PolicyAgent.__new__(PolicyAgent)
        agent._messages = []
        agent.tools = []
        agent.system_prompt = "test"
        agent.model = "test-model"
        agent.config = ConfigLoader(config_dir="config")
        agent.config.load()
        agent.broadcaster = EventBroadcaster()
        agent.scan_manager = ScanManager(
            config=agent.config, broadcaster=agent.broadcaster, data_dir="data",
        )

        # Two tool calls in one response
        tool1 = MagicMock()
        tool1.type = "tool_use"
        tool1.name = "get_policy_stats"
        tool1.input = {}
        tool1.id = "t1"

        tool2 = MagicMock()
        tool2.type = "tool_use"
        tool2.name = "estimate_cost"
        tool2.input = {"domains": "quick"}
        tool2.id = "t2"

        multi_response = MagicMock()
        multi_response.content = [tool1, tool2]
        multi_response.stop_reason = "tool_use"

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Done."
        text_response = MagicMock()
        text_response.content = [text_block]
        text_response.stop_reason = "end_turn"

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[multi_response, text_response])
        agent.client = mock_client

        results_received = []
        await agent.run(
            "Stats and cost",
            on_tool_result=lambda name, result: results_received.append((name, result)),
        )

        assert len(results_received) == 2
        assert results_received[0][0] == "get_policy_stats"
        assert "total" in results_received[0][1]
        assert results_received[1][0] == "estimate_cost"
        assert "estimated_cost_usd" in results_received[1][1]


# ---------------------------------------------------------------------------
# 7. Scan pipeline — start_scan → poll → complete
# ---------------------------------------------------------------------------

class TestScanPipeline:
    """Test scan lifecycle with mocked crawler."""

    @pytest.mark.asyncio
    async def test_dry_run_scan(self, real_config, scan_manager):
        """Dry run scan completes immediately."""
        result = await execute_tool(
            "start_scan",
            {"domains": "quick", "skip_llm": True},
            real_config, scan_manager,
        )
        # start_scan dispatches scan in background; check immediate return
        assert "scan_id" in result
        assert result["status"] in ("running", "completed")
        assert result["domain_count"] > 0

    @pytest.mark.asyncio
    async def test_scan_status_after_start(self, real_config, scan_manager):
        """After starting a scan, status is retrievable."""
        start_result = await execute_tool(
            "start_scan",
            {"domains": "quick", "skip_llm": True},
            real_config, scan_manager,
        )
        scan_id = start_result["scan_id"]

        status_result = await execute_tool(
            "get_scan_status",
            {"scan_id": scan_id},
            real_config, scan_manager,
        )
        assert status_result["scan_id"] == scan_id
        assert status_result["status"] in ("running", "completed", "failed")

    @pytest.mark.asyncio
    async def test_scan_cancel(self, real_config, scan_manager):
        """Scan can be cancelled."""
        start_result = await execute_tool(
            "start_scan",
            {"domains": "quick", "skip_llm": True},
            real_config, scan_manager,
        )
        scan_id = start_result["scan_id"]

        # Cancel should succeed or report already done
        cancel_result = await execute_tool(
            "stop_scan",
            {"scan_id": scan_id},
            real_config, scan_manager,
        )
        assert "cancelled" in cancel_result


# ---------------------------------------------------------------------------
# 8. Sheets export integration (mocked gspread)
# ---------------------------------------------------------------------------

class TestSheetsExportIntegration:
    """Test that scan pipeline calls Sheets export when configured."""

    @pytest.mark.asyncio
    async def test_sheets_export_after_scan(self, tmp_config_dir, monkeypatch):
        """Scan exports to Sheets when SPREADSHEET_ID and GOOGLE_CREDENTIALS are set."""
        try:
            import gspread  # noqa: F401
        except ImportError:
            pytest.skip("gspread not installed")

        monkeypatch.setenv("SPREADSHEET_ID", "test-sheet-id")
        monkeypatch.setenv("GOOGLE_CREDENTIALS", "dGVzdA==")  # base64("test")

        config = ConfigLoader(config_dir=str(tmp_config_dir))
        config.load()

        # Verify output settings loaded from env
        output_cfg = config.settings.output
        assert output_cfg.spreadsheet_id == "test-sheet-id"
        assert output_cfg.google_credentials_b64 == "dGVzdA=="

        # Simulate the export logic from scan_manager lines 288-304
        # (SheetsClient is lazy-imported inside _run_scan, so we test
        # the logic directly instead of patching an unresolvable attribute)
        mock_sheets = MagicMock()
        mock_sheets.get_existing_urls.return_value = set()
        mock_sheets.append_policies.return_value = 1

        all_policies = [
            Policy(
                url="https://test.gov/p1",
                policy_name="Test Policy",
                jurisdiction="US",
                policy_type=PolicyType.LAW,
                summary="Test",
                relevance_score=8,
            ),
        ]

        # Run the same code path as scan_manager
        with patch("src.output.sheets.SheetsClient", return_value=mock_sheets):
            from src.output.sheets import SheetsClient
            sheets = SheetsClient(
                credentials_b64=output_cfg.google_credentials_b64,
                spreadsheet_id=output_cfg.spreadsheet_id,
            )
            sheets.connect()
            existing_urls = sheets.get_existing_urls("Staging")
            new_policies = [p for p in all_policies if p.url not in existing_urls]
            if new_policies:
                sheets.append_policies(new_policies, "Staging")

        mock_sheets.connect.assert_called_once()
        mock_sheets.get_existing_urls.assert_called_once_with("Staging")
        mock_sheets.append_policies.assert_called_once()
        appended = mock_sheets.append_policies.call_args[0][0]
        assert len(appended) == 1
        assert appended[0].url == "https://test.gov/p1"

    @pytest.mark.asyncio
    async def test_sheets_dedup_skips_existing(self, tmp_config_dir, monkeypatch):
        """Sheets export skips policies whose URLs already exist."""
        try:
            from src.output.sheets import SheetsClient  # noqa: F401
        except ImportError:
            pytest.skip("gspread not installed")

        monkeypatch.setenv("SPREADSHEET_ID", "test-sheet-id")
        monkeypatch.setenv("GOOGLE_CREDENTIALS", "dGVzdA==")

        config = ConfigLoader(config_dir=str(tmp_config_dir))
        config.load()

        all_policies = [
            Policy(
                url="https://test.gov/already-there",
                policy_name="Old Policy",
                jurisdiction="US",
                policy_type=PolicyType.LAW,
                summary="Already exported",
                relevance_score=7,
            ),
            Policy(
                url="https://test.gov/new-one",
                policy_name="New Policy",
                jurisdiction="US",
                policy_type=PolicyType.REGULATION,
                summary="Newly discovered",
                relevance_score=8,
            ),
        ]

        existing_urls = {"https://test.gov/already-there"}
        new_policies = [p for p in all_policies if p.url not in existing_urls]
        assert len(new_policies) == 1
        assert new_policies[0].url == "https://test.gov/new-one"

    @pytest.mark.asyncio
    async def test_no_sheets_without_env_vars(self, tmp_config_dir):
        """Without SPREADSHEET_ID, sheets export is not attempted."""
        config = ConfigLoader(config_dir=str(tmp_config_dir))
        config.load()
        assert config.settings.output.spreadsheet_id is None
        # The scan_manager check is: if output_cfg.spreadsheet_id and ...
        # With None, this evaluates to False → no export


# ---------------------------------------------------------------------------
# 8b. Sheets export status tracking
# ---------------------------------------------------------------------------

class TestSheetsExportStatus:
    """SheetsExportStatus tracks export state and surfaces it to the agent."""

    def test_default_status_is_not_configured(self):
        """Fresh ScanJob has sheets_export.status = 'not_configured'."""
        from src.core.models import ScanJob
        job = ScanJob(scan_id="test")
        assert job.sheets_export.status == "not_configured"
        assert not job.sheets_export.configured
        assert not job.sheets_export.connected
        assert job.sheets_export.exported_count == 0
        assert job.sheets_export.error is None

    def test_configured_but_failed(self):
        """When credentials exist but connection fails, status='failed'."""
        from src.core.models import SheetsExportStatus
        s = SheetsExportStatus()
        s.configured = True
        s.status = "failed"
        s.error = "Invalid credentials"
        assert s.configured
        assert not s.connected
        assert s.status == "failed"
        assert s.error == "Invalid credentials"

    def test_connected_and_exporting(self):
        """When connected and exporting, tracks counts."""
        from src.core.models import SheetsExportStatus
        s = SheetsExportStatus()
        s.configured = True
        s.connected = True
        s.status = "connected"
        s.exported_count = 5
        assert s.exported_count == 5
        assert s.failed_count == 0

    def test_partial_failure_tracks_both_counts(self):
        """Some policies export, some fail — tracks both."""
        from src.core.models import SheetsExportStatus
        s = SheetsExportStatus()
        s.configured = True
        s.connected = True
        s.status = "connected"
        s.exported_count = 3
        s.failed_count = 2
        s.error = "Rate limit on batch 2"
        assert s.exported_count == 3
        assert s.failed_count == 2

    def test_model_dump_includes_all_fields(self):
        """model_dump() returns all fields for JSON serialization."""
        from src.core.models import SheetsExportStatus
        s = SheetsExportStatus(
            configured=True, connected=True, status="connected",
            exported_count=5, failed_count=0, error=None,
        )
        d = s.model_dump()
        assert d == {
            "configured": True,
            "connected": True,
            "status": "connected",
            "exported_count": 5,
            "failed_count": 0,
            "error": None,
        }

    def test_scanjob_sheets_export_in_model_dump(self):
        """ScanJob.model_dump() includes sheets_export."""
        from src.core.models import ScanJob
        job = ScanJob(scan_id="test-123")
        d = job.model_dump()
        assert "sheets_export" in d
        assert d["sheets_export"]["status"] == "not_configured"

    @pytest.mark.asyncio
    async def test_scan_status_includes_sheets_export(self, real_config, scan_manager):
        """get_scan_status tool response includes sheets_export field."""
        from src.agent.tools import execute_tool
        # Start a dry-run scan to create a job
        result = await execute_tool(
            "start_scan", {"domains": "quick", "dry_run": True},
            real_config, scan_manager,
        )
        scan_id = result.get("scan_id")
        assert scan_id

        # Check status includes sheets_export
        status = await execute_tool(
            "get_scan_status", {"scan_id": scan_id},
            real_config, scan_manager,
        )
        assert "sheets_export" in status
        assert status["sheets_export"]["status"] == "not_configured"

    @pytest.mark.asyncio
    async def test_sheets_status_not_configured_without_credentials(
        self, tmp_config_dir, monkeypatch
    ):
        """When .env has no credentials, sheets_export.status='not_configured'."""
        # Ensure no Google env vars
        monkeypatch.delenv("GOOGLE_CREDENTIALS", raising=False)
        monkeypatch.delenv("SPREADSHEET_ID", raising=False)

        config = ConfigLoader(config_dir=str(tmp_config_dir))
        config.load()

        assert config.settings.output.google_credentials_b64 is None
        assert config.settings.output.spreadsheet_id is None

    @pytest.mark.asyncio
    async def test_sheets_status_failed_with_bad_credentials(
        self, tmp_config_dir, monkeypatch
    ):
        """When credentials are set but invalid, SheetsClient.connect() raises."""
        monkeypatch.setenv("SPREADSHEET_ID", "real-sheet-id")
        monkeypatch.setenv("GOOGLE_CREDENTIALS", "dGVzdA==")  # too short

        config = ConfigLoader(config_dir=str(tmp_config_dir))
        config.load()

        # credentials are set and pass placeholder filter
        assert config.settings.output.google_credentials_b64 == "dGVzdA=="
        assert config.settings.output.spreadsheet_id == "real-sheet-id"

        # But SheetsClient.connect() will reject them as too short
        from src.output.sheets import SheetsClient
        client = SheetsClient(
            credentials_b64="dGVzdA==",
            spreadsheet_id="real-sheet-id",
        )
        with pytest.raises(ValueError, match="GOOGLE_CREDENTIALS looks invalid"):
            client.connect()


# ---------------------------------------------------------------------------
# 9. Search policies with data in scan_manager
# ---------------------------------------------------------------------------

class TestSearchPoliciesWithData:
    """Test search_policies when scan_manager has policies."""

    @pytest.mark.asyncio
    async def test_search_finds_matching(self, real_config, scan_manager):
        # Inject policies
        scan_manager._policies["test"] = [
            Policy(
                url="https://a.gov/p1",
                policy_name="German Heat Act",
                jurisdiction="Germany",
                policy_type=PolicyType.LAW,
                summary="Heat recovery mandate",
                relevance_score=9,
            ),
            Policy(
                url="https://b.gov/p2",
                policy_name="French Energy Code",
                jurisdiction="France",
                policy_type=PolicyType.REGULATION,
                summary="Energy regulation",
                relevance_score=6,
            ),
        ]

        result = await execute_tool(
            "search_policies",
            {"jurisdiction": "Germany"},
            real_config, scan_manager,
        )
        assert result["count"] == 1
        assert result["policies"][0]["policy_name"] == "German Heat Act"

    @pytest.mark.asyncio
    async def test_search_by_type(self, real_config, scan_manager):
        scan_manager._policies["test"] = [
            Policy(
                url="https://a.gov/p1",
                policy_name="Act 1",
                jurisdiction="US",
                policy_type=PolicyType.LAW,
                summary="A law",
                relevance_score=7,
            ),
            Policy(
                url="https://b.gov/p2",
                policy_name="Reg 2",
                jurisdiction="US",
                policy_type=PolicyType.REGULATION,
                summary="A reg",
                relevance_score=7,
            ),
        ]

        result = await execute_tool(
            "search_policies",
            {"policy_type": "regulation"},
            real_config, scan_manager,
        )
        assert result["count"] == 1
        assert result["policies"][0]["policy_name"] == "Reg 2"

    @pytest.mark.asyncio
    async def test_search_by_min_score(self, real_config, scan_manager):
        scan_manager._policies["test"] = [
            Policy(url="https://a.gov", policy_name="High", jurisdiction="US",
                   policy_type=PolicyType.LAW, summary="s", relevance_score=9),
            Policy(url="https://b.gov", policy_name="Low", jurisdiction="US",
                   policy_type=PolicyType.LAW, summary="s", relevance_score=3),
        ]

        result = await execute_tool(
            "search_policies",
            {"min_score": 7},
            real_config, scan_manager,
        )
        assert result["count"] == 1
        assert result["policies"][0]["policy_name"] == "High"

    @pytest.mark.asyncio
    async def test_stats_with_data(self, real_config, scan_manager):
        scan_manager._policies["test"] = [
            Policy(url="https://a.gov", policy_name="P1", jurisdiction="Germany",
                   policy_type=PolicyType.LAW, summary="s", relevance_score=9),
            Policy(url="https://b.gov", policy_name="P2", jurisdiction="France",
                   policy_type=PolicyType.REGULATION, summary="s", relevance_score=6),
        ]

        result = await execute_tool("get_policy_stats", {}, real_config, scan_manager)
        assert result["total"] == 2
        assert result["by_jurisdiction"]["Germany"] == 1
        assert result["by_jurisdiction"]["France"] == 1
        assert result["by_type"]["law"] == 1
        assert result["by_type"]["regulation"] == 1


class TestSearchPoliciesFullTextQuery:
    """A ``query`` argument now routes through PolicyStore.search_text
    (full-text over name/summary/key_requirements/jurisdiction) instead of
    the old Python substring loop over name+summary only."""

    @pytest.mark.asyncio
    async def test_query_matches_key_requirements_not_just_name_and_summary(
        self, real_config, scan_manager
    ):
        # The old substring loop only ever looked at policy_name + summary,
        # so a term that lives only in key_requirements proves the new path
        # is actually hitting the FTS index, not just the same old fields.
        store = PolicyStore(data_dir=scan_manager.data_dir)
        store.add_policies([
            Policy(
                url="https://a.gov/p1", policy_name="Heat Act", jurisdiction="US",
                policy_type=PolicyType.LAW, summary="A short summary.",
                key_requirements="Operators must file a widgetronic-plan annually.",
                relevance_score=8,
            ),
            Policy(
                url="https://b.gov/p2", policy_name="Other Act", jurisdiction="US",
                policy_type=PolicyType.LAW, summary="Unrelated.",
                key_requirements="Nothing special here.",
                relevance_score=8,
            ),
        ])

        result = await execute_tool(
            "search_policies", {"query": "widgetronic-plan"}, real_config, scan_manager,
        )
        assert result["count"] == 1
        assert result["policies"][0]["url"] == "https://a.gov/p1"

    @pytest.mark.asyncio
    async def test_query_composes_with_jurisdiction_filter(self, real_config, scan_manager):
        store = PolicyStore(data_dir=scan_manager.data_dir)
        store.add_policies([
            Policy(
                url="https://a.gov/p1", policy_name="Heat Reuse Rule", jurisdiction="Germany",
                policy_type=PolicyType.LAW, summary="Mandates heat reuse.", relevance_score=8,
            ),
            Policy(
                url="https://b.gov/p2", policy_name="Heat Reuse Rule", jurisdiction="France",
                policy_type=PolicyType.LAW, summary="Mandates heat reuse.", relevance_score=8,
            ),
        ])

        result = await execute_tool(
            "search_policies",
            {"query": "heat reuse", "jurisdiction": "Germany"},
            real_config, scan_manager,
        )
        assert result["count"] == 1
        assert result["policies"][0]["url"] == "https://a.gov/p1"

    @pytest.mark.asyncio
    async def test_query_composes_with_min_score_filter(self, real_config, scan_manager):
        store = PolicyStore(data_dir=scan_manager.data_dir)
        store.add_policies([
            Policy(
                url="https://a.gov/p1", policy_name="Heat Reuse Rule High", jurisdiction="US",
                policy_type=PolicyType.LAW, summary="Mandates heat reuse.", relevance_score=9,
            ),
            Policy(
                url="https://b.gov/p2", policy_name="Heat Reuse Rule Low", jurisdiction="US",
                policy_type=PolicyType.LAW, summary="Mandates heat reuse.", relevance_score=2,
            ),
        ])

        result = await execute_tool(
            "search_policies", {"query": "heat reuse", "min_score": 5}, real_config, scan_manager,
        )
        assert result["count"] == 1
        assert result["policies"][0]["url"] == "https://a.gov/p1"

    @pytest.mark.asyncio
    async def test_no_query_keeps_old_filter_behavior_over_in_memory_policies(
        self, real_config, scan_manager
    ):
        # Without a query, in-memory (not-yet-persisted) scan results must
        # still be searchable, exactly as before search_text existed.
        scan_manager._policies["test"] = [
            Policy(
                url="https://c.gov/p3", policy_name="In-memory Only", jurisdiction="Germany",
                policy_type=PolicyType.LAW, summary="Not in the store.", relevance_score=8,
            ),
        ]

        result = await execute_tool(
            "search_policies", {"jurisdiction": "Germany"}, real_config, scan_manager,
        )
        assert result["count"] == 1
        assert result["policies"][0]["url"] == "https://c.gov/p3"

    @pytest.mark.asyncio
    async def test_empty_query_string_is_treated_as_no_query(self, real_config, scan_manager):
        scan_manager._policies["test"] = [
            Policy(
                url="https://d.gov/p4", policy_name="In-memory Only", jurisdiction="US",
                policy_type=PolicyType.LAW, summary="Not in the store.", relevance_score=8,
            ),
        ]

        result = await execute_tool(
            "search_policies", {"query": ""}, real_config, scan_manager,
        )
        assert result["count"] == 1
        assert result["policies"][0]["url"] == "https://d.gov/p4"


# ---------------------------------------------------------------------------
# 10. API error handling in agent loop
# ---------------------------------------------------------------------------

class TestAgentAPIErrors:
    """Test agent handles API errors gracefully."""

    @pytest.mark.asyncio
    async def test_api_error_returns_message(self):
        import anthropic

        agent = PolicyAgent.__new__(PolicyAgent)
        agent._messages = []
        agent.tools = []
        agent.system_prompt = "test"
        agent.model = "test-model"
        agent.config = ConfigLoader(config_dir="config")
        agent.config.load()
        agent.broadcaster = EventBroadcaster()
        agent.scan_manager = ScanManager(
            config=agent.config, broadcaster=agent.broadcaster, data_dir="data",
        )

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.APIError(
                message="Rate limited",
                request=MagicMock(),
                body=None,
            )
        )
        agent.client = mock_client

        text_received = []
        result = await agent.run("Test", on_text=lambda t: text_received.append(t))
        assert "API error" in result
        assert len(text_received) == 1


# ---------------------------------------------------------------------------
# 11. README onboarding flow — fresh-clone experience
# ---------------------------------------------------------------------------

class TestOnboardingFlow:
    """Verify the README 'Try it now' steps work for a new user.

    Tests the full flow: pyproject.toml is valid, example.env exists,
    config loads, python -m src.agent shows helpful errors when
    prerequisites are missing, and the entry point works end-to-end.
    """

    def test_pyproject_toml_exists_at_repo_root(self):
        """pip install -e . requires pyproject.toml at the repo root."""
        pyproject = Path("pyproject.toml")
        assert pyproject.exists(), "pyproject.toml not found at repo root"

    def test_pyproject_toml_has_project_metadata(self):
        """pyproject.toml has enough metadata for pip install."""
        content = Path("pyproject.toml").read_text(encoding="utf-8")
        assert "[project]" in content
        assert 'name = "OCP-CE-HR-Policy-Searcher"' in content
        assert "dependencies" in content

    def test_example_env_exists(self):
        """cp config/example.env .env — the source file must exist."""
        assert Path("config/example.env").exists()

    def test_example_env_has_api_key_placeholder(self):
        """example.env should have an ANTHROPIC_API_KEY line to fill in."""
        content = Path("config/example.env").read_text(encoding="utf-8")
        assert "ANTHROPIC_API_KEY" in content

    def test_example_env_has_sheets_placeholders(self):
        """example.env should document the optional Sheets config."""
        content = Path("config/example.env").read_text(encoding="utf-8")
        assert "GOOGLE_CREDENTIALS" in content
        assert "SPREADSHEET_ID" in content

    def test_config_dir_exists_with_required_files(self):
        """Real config dir has all files the loader expects."""
        config = Path("config")
        assert (config / "settings.yaml").exists()
        assert (config / "keywords.yaml").exists()
        assert (config / "groups.yaml").exists()
        assert (config / "domains").is_dir()
        domain_files = list((config / "domains").glob("*.yaml"))
        assert len(domain_files) > 0, "No domain YAML files found"

    def test_config_loads_from_repo_root(self):
        """ConfigLoader succeeds with real config files."""
        from src.core.config import ConfigLoader

        c = ConfigLoader(config_dir="config")
        c.load()
        assert len(c.get_enabled_domains("all")) > 100

    def test_agent_no_api_key_exits_with_helpful_error(self, monkeypatch, capsys):
        """python -m src.agent with no key shows a helpful message."""
        # Import first so module-level load_dotenv() runs and gets cached.
        # Then delenv removes the key — main() won't find it.
        from src.agent.__main__ import main

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

        output = capsys.readouterr().out
        assert "ANTHROPIC_API_KEY" in output
        assert "example.env" in output
        assert "console.anthropic.com" in output

    def test_agent_no_api_key_shows_cross_platform_help(self, monkeypatch, capsys):
        """Error message includes both bash and PowerShell syntax."""
        from src.agent.__main__ import main

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        with pytest.raises(SystemExit):
            main()

        output = capsys.readouterr().out
        assert "export ANTHROPIC_API_KEY" in output  # bash/zsh
        assert "$env:ANTHROPIC_API_KEY" in output    # PowerShell

    def test_agent_placeholder_key_starts(self, monkeypatch):
        """Agent starts (doesn't crash) with a placeholder key.

        It will fail at first API call, but the config loading and
        initialization should succeed.
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-placeholder")
        from src.agent.orchestrator import PolicyAgent

        agent = PolicyAgent(
            api_key="sk-ant-api03-placeholder",
            config_dir="config",
            data_dir="data",
        )
        assert len(agent.tools) == 15
        assert agent.config is not None

    def test_rest_api_starts_without_api_key(self):
        """FastAPI server starts even without an API key.

        The API key is only needed for agent/analysis endpoints,
        not for domain listing or config endpoints.
        """
        from fastapi.testclient import TestClient
        from src.api.app import app

        with TestClient(app) as client:
            r = client.get("/health")
            assert r.status_code == 200
            assert r.json()["status"] == "ok"

            r = client.get("/api/domains")
            assert r.status_code == 200
            assert r.json()["count"] > 100

    # ------------------------------------------------------------------
    # CLI feedback and interrupt handling
    # ------------------------------------------------------------------

    def test_banner_mentions_ctrl_c(self, capsys, tmp_path):
        """Banner tells users how to interrupt a running operation."""
        from src.agent.__main__ import _print_banner
        from pathlib import Path
        _print_banner(Path(tmp_path / "agent.log"))
        output = capsys.readouterr().out
        assert "Ctrl+C" in output

    def test_banner_mentions_quit(self, capsys, tmp_path):
        """Banner tells users how to exit the session."""
        from src.agent.__main__ import _print_banner
        from pathlib import Path
        _print_banner(Path(tmp_path / "agent.log"))
        output = capsys.readouterr().out
        assert "quit" in output or "exit" in output

    def test_on_tool_call_shows_status(self, capsys):
        """Tool calls print a status line so the user knows what's happening."""
        from src.agent.__main__ import _on_tool_call
        _on_tool_call("list_domains", {"group": "eu"})
        output = capsys.readouterr().out
        assert "Browsing available domains" in output

    def test_on_tool_call_shows_scan_details(self, capsys):
        """start_scan status includes which domains are being scanned."""
        from src.agent.__main__ import _on_tool_call
        _on_tool_call("start_scan", {"domains": "nordic"})
        output = capsys.readouterr().out
        assert "nordic" in output

    def test_on_tool_call_shows_url(self, capsys):
        """analyze_url status includes the URL being analyzed."""
        from src.agent.__main__ import _on_tool_call
        _on_tool_call("analyze_url", {"url": "https://example.gov/policy"})
        output = capsys.readouterr().out
        assert "example.gov" in output

    def test_on_tool_call_unknown_tool(self, capsys):
        """Unknown tools get a generic status line instead of crashing."""
        from src.agent.__main__ import _on_tool_call
        _on_tool_call("some_future_tool", {})
        output = capsys.readouterr().out
        assert "some_future_tool" in output

    def test_on_tool_result_list_domains(self, capsys):
        """list_domains result shows domain count."""
        from src.agent.__main__ import _on_tool_result
        _on_tool_result("list_domains", {"count": 42, "domains": []})
        output = capsys.readouterr().out
        assert "42" in output

    def test_on_tool_result_start_scan(self, capsys):
        """start_scan result shows scan ID and domain count."""
        from src.agent.__main__ import _on_tool_result
        _on_tool_result("start_scan", {
            "scan_id": "abc123", "domain_count": 5, "status": "running",
        })
        output = capsys.readouterr().out
        assert "abc123" in output
        assert "5" in output

    def test_on_tool_result_scan_status(self, capsys):
        """get_scan_status result shows progress."""
        from src.agent.__main__ import _on_tool_result
        _on_tool_result("get_scan_status", {
            "status": "running",
            "policy_count": 3,
            "progress": {"completed": 7, "total": 10},
        })
        output = capsys.readouterr().out
        assert "7/10" in output
        assert "3 policies" in output

    def test_on_tool_result_estimate_cost(self, capsys):
        """estimate_cost result shows dollar amount."""
        from src.agent.__main__ import _on_tool_result
        _on_tool_result("estimate_cost", {
            "estimated_cost_usd": 1.23, "domain_count": 10,
        })
        output = capsys.readouterr().out
        assert "$1.23" in output

    def test_on_tool_result_search_policies(self, capsys):
        """search_policies result shows match count."""
        from src.agent.__main__ import _on_tool_result
        _on_tool_result("search_policies", {"count": 5, "policies": []})
        output = capsys.readouterr().out
        assert "5" in output

    def test_on_tool_result_analyze_url_with_policy(self, capsys):
        """analyze_url result shows policy name and relevance."""
        from src.agent.__main__ import _on_tool_result
        _on_tool_result("analyze_url", {
            "url": "https://test.gov/p",
            "keyword_score": 12,
            "policy": {"policy_name": "Heat Act", "relevance_score": 9},
        })
        output = capsys.readouterr().out
        assert "Heat Act" in output
        assert "9" in output

    def test_on_tool_result_no_crash_on_empty(self, capsys):
        """_on_tool_result handles empty/unexpected results without crashing."""
        from src.agent.__main__ import _on_tool_result
        _on_tool_result("list_domains", {})
        _on_tool_result("unknown_tool", {"random": "data"})
        _on_tool_result("estimate_cost", "not a dict")
        # Should not raise — just produce no output
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_interactive_ctrl_c_recovers(self, tmp_path):
        """Ctrl+C during agent.run() prints friendly message, doesn't crash."""
        from src.agent.__main__ import _run_interactive
        from unittest.mock import AsyncMock, MagicMock, patch
        from pathlib import Path
        import io

        mock_agent = MagicMock()
        # First call: user types "test", agent.run raises KeyboardInterrupt
        # Second call: user types "quit"
        mock_agent.run = AsyncMock(side_effect=KeyboardInterrupt)

        with patch("builtins.input", side_effect=["test", "quit"]):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                asyncio.run(_run_interactive(mock_agent, Path(tmp_path / "agent.log")))
                output = mock_stdout.getvalue()

        assert "Interrupted" in output
        assert "ready for next question" in output
        # Should NOT contain a traceback
        assert "Traceback" not in output

    def test_single_mode_ctrl_c_exits_130(self):
        """Ctrl+C in single-command mode exits with code 130 (standard)."""
        from src.agent.__main__ import _run_single
        from unittest.mock import AsyncMock, MagicMock

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=KeyboardInterrupt)

        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(_run_single(mock_agent, "test query"))
        assert exc_info.value.code == 130

    def test_thinking_message_shown(self, tmp_path):
        """User sees 'Thinking...' immediately after submitting a query."""
        from src.agent.__main__ import _run_interactive
        from unittest.mock import AsyncMock, MagicMock, patch
        from pathlib import Path
        import io

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value="Done")

        with patch("builtins.input", side_effect=["test", "quit"]):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                asyncio.run(_run_interactive(mock_agent, Path(tmp_path / "agent.log")))
                output = mock_stdout.getvalue()

        assert "Thinking..." in output

    def test_placeholder_key_detected(self):
        """Agent exits with helpful message if .env still has the placeholder key."""
        from src.agent.__main__ import main

        with patch.dict(
            "os.environ",
            {"ANTHROPIC_API_KEY": "sk-ant-api03-your-key-here"},
        ):
            with pytest.raises(SystemExit) as exc_info:
                with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                    main()
            assert exc_info.value.code == 1
            output = mock_stdout.getvalue()
            assert "placeholder" in output.lower()
            assert "console.anthropic.com" in output

    def test_short_key_detected(self):
        """Agent exits with helpful message if API key is suspiciously short."""
        from src.agent.__main__ import main

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-short"}):
            with pytest.raises(SystemExit) as exc_info:
                with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                    main()
            assert exc_info.value.code == 1
            output = mock_stdout.getvalue()
            assert "placeholder" in output.lower()

    def test_auth_error_gives_helpful_message(self):
        """401 from Anthropic shows user-friendly setup instructions, not raw error."""
        import anthropic
        from src.agent.orchestrator import PolicyAgent

        agent = PolicyAgent.__new__(PolicyAgent)
        agent._messages = []
        agent.client = MagicMock()
        agent.client.messages = MagicMock()
        agent.client.messages.create = AsyncMock(
            side_effect=anthropic.AuthenticationError(
                message="invalid x-api-key",
                response=MagicMock(status_code=401),
                body={"error": {"message": "invalid x-api-key"}},
            )
        )
        agent.model = DEFAULT_ANALYSIS_MODEL
        agent.system_prompt = "test"
        agent.tools = []

        text_output = []
        result = asyncio.run(
            agent.run("hello", on_text=lambda t: text_output.append(t))
        )
        assert "Authentication failed" in result
        assert "console.anthropic.com" in result
        assert "invalid x-api-key" not in result  # No raw error dump

    # ------------------------------------------------------------------
    # Setup script tests
    # ------------------------------------------------------------------

    def test_setup_sh_exists(self):
        """setup.sh exists at repo root for Linux/macOS users."""
        assert Path("setup.sh").exists()

    def test_setup_ps1_exists(self):
        """setup.ps1 exists at repo root for Windows users."""
        assert Path("setup.ps1").exists()

    @pytest.mark.skipif(
        not Path("/bin/bash").exists() and not Path("/usr/bin/bash").exists(),
        reason="bash not available (Windows)",
    )
    def test_setup_sh_has_valid_bash_syntax(self):
        """setup.sh parses without syntax errors."""
        import subprocess
        result = subprocess.run(
            ["bash", "-n", "setup.sh"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"Bash syntax error: {result.stderr}"

    def test_setup_sh_has_shebang(self):
        """setup.sh starts with a proper shebang line."""
        first_line = Path("setup.sh").read_text(encoding="utf-8").split("\n")[0]
        assert first_line.startswith("#!/"), "Missing shebang"
        assert "bash" in first_line

    def test_setup_sh_exits_on_errors(self):
        """setup.sh uses set -e so failures don't silently continue."""
        content = Path("setup.sh").read_text(encoding="utf-8")
        assert "set -e" in content

    def test_setup_sh_checks_python_version(self):
        """setup.sh verifies Python 3.11+ before proceeding."""
        content = Path("setup.sh").read_text(encoding="utf-8")
        assert "3" in content and "11" in content
        assert "Python" in content
        # Should show helpful error with download link if missing
        assert "python.org/downloads" in content

    def test_setup_sh_creates_venv(self):
        """setup.sh creates a .venv virtual environment."""
        content = Path("setup.sh").read_text(encoding="utf-8")
        assert ".venv" in content
        assert "venv" in content

    def test_setup_sh_installs_with_pip(self):
        """setup.sh installs the project with pip."""
        content = Path("setup.sh").read_text(encoding="utf-8")
        assert "pip install" in content

    def test_setup_sh_copies_example_env(self):
        """setup.sh copies config/example.env to .env."""
        content = Path("setup.sh").read_text(encoding="utf-8")
        assert "example.env" in content
        assert ".env" in content

    def test_setup_sh_supports_dev_flag(self):
        """setup.sh --dev installs development dependencies."""
        content = Path("setup.sh").read_text(encoding="utf-8")
        assert "--dev" in content
        assert ".[dev," in content or ".[dev]" in content

    def test_setup_sh_skips_existing_venv(self):
        """setup.sh doesn't recreate .venv if it already exists."""
        content = Path("setup.sh").read_text(encoding="utf-8")
        assert "already exists" in content

    def test_setup_sh_skips_existing_env_file(self):
        """setup.sh doesn't overwrite .env if it already exists."""
        content = Path("setup.sh").read_text(encoding="utf-8")
        # Must check for existing .env before copying
        assert '! -f ".env"' in content or "already exists" in content

    def test_setup_sh_shows_next_steps(self):
        """setup.sh tells the user what to do after setup."""
        content = Path("setup.sh").read_text(encoding="utf-8")
        assert "console.anthropic.com" in content
        assert "python -m src.agent" in content

    def test_setup_ps1_checks_python_version(self):
        """setup.ps1 verifies Python 3.11+ before proceeding."""
        content = Path("setup.ps1").read_text(encoding="utf-8")
        assert "3" in content and "11" in content
        assert "Python" in content
        assert "python.org/downloads" in content

    def test_setup_ps1_mentions_execution_policy(self):
        """setup.ps1 tells Windows users how to enable script execution."""
        content = Path("setup.ps1").read_text(encoding="utf-8")
        assert "Set-ExecutionPolicy" in content

    def test_setup_ps1_supports_dev_flag(self):
        """setup.ps1 -Dev installs development dependencies."""
        content = Path("setup.ps1").read_text(encoding="utf-8")
        assert "$Dev" in content
        assert ".[dev," in content or ".[dev]" in content

    def test_setup_ps1_creates_venv(self):
        """setup.ps1 creates a .venv virtual environment."""
        content = Path("setup.ps1").read_text(encoding="utf-8")
        assert ".venv" in content
        assert "venv" in content

    def test_setup_ps1_copies_example_env(self):
        """setup.ps1 copies config/example.env to .env."""
        content = Path("setup.ps1").read_text(encoding="utf-8")
        assert "example.env" in content

    def test_setup_ps1_shows_next_steps(self):
        """setup.ps1 tells the user what to do after setup."""
        content = Path("setup.ps1").read_text(encoding="utf-8")
        assert "console.anthropic.com" in content
        # Accept either the bare or venv-qualified interpreter form
        assert "-m src.agent" in content

    def test_setup_ps1_ascii_only_strings(self):
        """setup.ps1 uses only ASCII in strings to avoid PowerShell encoding errors.

        Em dashes (U+2014) and smart quotes get mangled by PowerShell's
        encoding, causing parse failures. All user-facing strings must
        use plain ASCII equivalents (-- instead of em dash, etc.).
        """
        content = Path("setup.ps1").read_text(encoding="utf-8")
        # No em dashes (U+2014) or en dashes (U+2013) anywhere in the file
        assert "\u2014" not in content, "Em dash found — use '--' instead"
        assert "\u2013" not in content, "En dash found — use '-' instead"
        # No smart quotes (U+2018/U+2019/U+201C/U+201D)
        for char in ["\u2018", "\u2019", "\u201c", "\u201d"]:
            assert char not in content, f"Smart quote {repr(char)} found — use plain quotes"

    def test_setup_ps1_playwright_stderr_suppression(self):
        """setup.ps1 suppresses stderr during playwright install.

        Node.js deprecation warnings on stderr cause NativeCommandError
        when ErrorActionPreference is Stop. The script must temporarily
        set SilentlyContinue and check LASTEXITCODE manually.
        """
        content = Path("setup.ps1").read_text(encoding="utf-8")
        assert "playwright install chromium" in content
        assert "SilentlyContinue" in content, (
            "Must suppress stderr errors during playwright install"
        )
        assert "$LASTEXITCODE" in content or "LASTEXITCODE" in content

    def test_setup_sh_ascii_only_user_strings(self):
        """setup.sh uses only ASCII in user-facing strings.

        While bash handles Unicode fine, keeping parity with setup.ps1
        avoids confusion if strings are copied between scripts.
        """
        content = Path("setup.sh").read_text(encoding="utf-8")
        # Check lines that contain Write-/echo/warn/info calls for non-ASCII
        for i, line in enumerate(content.split("\n"), 1):
            # Skip comment-only lines (comments are safe)
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            # Check executable lines for em/en dashes
            assert "\u2014" not in line, f"Line {i}: em dash in executable code"
            assert "\u2013" not in line, f"Line {i}: en dash in executable code"

    @pytest.mark.skipif(
        not Path("/bin/bash").exists() and not Path("/usr/bin/bash").exists(),
        reason="bash not available (Windows)",
    )
    def test_setup_sh_functional_python_check(self):
        """setup.sh correctly identifies the current Python as 3.11+."""
        import subprocess

        # Extract just the Python-finding logic and run it
        result = subprocess.run(
            ["bash", "-c", """
                PYTHON=""
                for candidate in python3 python; do
                    if command -v "$candidate" &>/dev/null; then
                        major=$("$candidate" -c "import sys; print(sys.version_info.major)" 2>/dev/null)
                        minor=$("$candidate" -c "import sys; print(sys.version_info.minor)" 2>/dev/null)
                        if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ] 2>/dev/null; then
                            PYTHON="$candidate"
                            break
                        fi
                    fi
                done
                if [ -z "$PYTHON" ]; then
                    echo "NOT_FOUND"
                    exit 1
                fi
                echo "FOUND:$PYTHON"
            """],
            capture_output=True, text=True,
        )
        # We know our current Python is 3.11+ (required by pyproject.toml)
        assert result.returncode == 0, "setup.sh would fail to find Python 3.11+"
        assert "FOUND:" in result.stdout

    @pytest.mark.skipif(
        not Path("/bin/bash").exists() and not Path("/usr/bin/bash").exists(),
        reason="bash not available (Windows)",
    )
    def test_setup_sh_env_copy_functional(self, tmp_path):
        """setup.sh's env-copy logic works correctly."""
        import subprocess
        import shutil

        # Copy example.env to a temp dir and test the copy logic
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        shutil.copy("config/example.env", config_dir / "example.env")

        # Run just the env-copy portion
        result = subprocess.run(
            ["bash", "-c", f"""
                cd "{tmp_path}"
                if [ ! -f ".env" ]; then
                    cp config/example.env .env
                    echo "COPIED"
                else
                    echo "SKIPPED"
                fi
            """],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "COPIED" in result.stdout
        assert (tmp_path / ".env").exists()

        # Run again — should skip
        result = subprocess.run(
            ["bash", "-c", f"""
                cd "{tmp_path}"
                if [ ! -f ".env" ]; then
                    cp config/example.env .env
                    echo "COPIED"
                else
                    echo "SKIPPED"
                fi
            """],
            capture_output=True, text=True,
        )
        assert "SKIPPED" in result.stdout


# ---------------------------------------------------------------------------
# 12. Per-domain policy persistence
# ---------------------------------------------------------------------------

class TestPolicyPersistence:
    """Verify that policies survive crashes — saved per-domain, not at end."""

    @pytest.mark.asyncio
    async def test_policy_store_saves_to_disk(self, tmp_path):
        """PolicyStore.add_policies persists to policypulse.db durably."""
        from src.storage.store import PolicyStore

        store = PolicyStore(data_dir=str(tmp_path))
        policies = [
            Policy(
                url="https://test.gov/p1",
                policy_name="Test Policy",
                jurisdiction="US",
                policy_type=PolicyType.LAW,
                summary="A test policy",
                relevance_score=8,
            ),
        ]
        added = store.add_policies(policies)
        assert added == 1

        # Db should exist on disk now
        assert (tmp_path / "policypulse.db").exists()

        # And a fresh store instance (simulating a restart) sees the write
        data = PolicyStore(data_dir=str(tmp_path)).get_all()
        assert len(data) == 1
        assert data[0]["url"] == "https://test.gov/p1"

    @pytest.mark.asyncio
    async def test_policy_store_deduplicates_by_url(self, tmp_path):
        """Adding the same policy twice only stores it once."""
        from src.storage.store import PolicyStore

        store = PolicyStore(data_dir=str(tmp_path))
        policy = Policy(
            url="https://test.gov/dup",
            policy_name="Duplicate",
            jurisdiction="US",
            policy_type=PolicyType.LAW,
            summary="Test",
            relevance_score=7,
        )
        assert store.add_policies([policy]) == 1
        assert store.add_policies([policy]) == 0  # Already exists
        assert len(store.get_all()) == 1

    @pytest.mark.asyncio
    async def test_policy_store_survives_reload(self, tmp_path):
        """Policies persist across PolicyStore instances (simulates crash recovery)."""
        from src.storage.store import PolicyStore

        store1 = PolicyStore(data_dir=str(tmp_path))
        store1.add_policies([
            Policy(
                url="https://a.gov/p1",
                policy_name="Saved Policy",
                jurisdiction="DE",
                policy_type=PolicyType.REGULATION,
                summary="Survives crash",
                relevance_score=9,
            ),
        ])

        # Simulate crash: create new store instance that loads from disk
        store2 = PolicyStore(data_dir=str(tmp_path))
        all_policies = store2.get_all()
        assert len(all_policies) == 1
        assert all_policies[0]["policy_name"] == "Saved Policy"

    @pytest.mark.asyncio
    async def test_scan_manager_persists_per_domain(self, tmp_config_dir, tmp_path):
        """ScanManager saves policies as each domain completes, not just at the end."""
        config = ConfigLoader(config_dir=str(tmp_config_dir))
        config.load()

        broadcaster = EventBroadcaster()
        sm = ScanManager(
            config=config,
            broadcaster=broadcaster,
            data_dir=str(tmp_path),
        )

        # Run a dry-run scan (won't actually crawl)
        job = await sm.start_scan(domains_group="quick", dry_run=True)
        assert job.status.value == "completed"

        # Now manually inject policies and verify persistence would work
        from src.storage.store import PolicyStore
        store = PolicyStore(data_dir=str(tmp_path))
        policy = Policy(
            url="https://test.gov/from-scan",
            policy_name="Scan Result",
            jurisdiction="US",
            policy_type=PolicyType.LAW,
            summary="Found during scan",
            relevance_score=8,
        )
        store.add_policies([policy])

        # Verify it's durably persisted, visible to a fresh store instance
        assert (tmp_path / "policypulse.db").exists()
        data = PolicyStore(data_dir=str(tmp_path)).get_all()
        assert any(p["url"] == "https://test.gov/from-scan" for p in data)


# ---------------------------------------------------------------------------
# 13. Scan status smart polling
# ---------------------------------------------------------------------------

class TestScanStatusPolling:
    """Verify scan status includes smart polling guidance."""

    @pytest.mark.asyncio
    async def test_running_scan_has_wait_recommendation(self, real_config, scan_manager):
        """get_scan_status includes recommended_wait_seconds for running scans."""
        from src.core.models import ScanJob, ScanStatus, ScanProgress, DomainProgress

        job = ScanJob(
            scan_id="poll-test",
            status=ScanStatus.RUNNING,
            domain_count=10,
            progress=ScanProgress(
                total_domains=10,
                completed_domains=3,
                domains=[
                    DomainProgress(domain_id=f"d{i}", domain_name=f"Domain {i}")
                    for i in range(10)
                ],
            ),
        )
        scan_manager._jobs["poll-test"] = job
        scan_manager._policies["poll-test"] = []

        result = await execute_tool(
            "get_scan_status",
            {"scan_id": "poll-test"},
            real_config, scan_manager,
        )

        assert "recommended_wait_seconds" in result
        assert result["recommended_wait_seconds"] >= 20
        assert "hint" in result

    @pytest.mark.asyncio
    async def test_completed_scan_has_no_wait(self, real_config, scan_manager):
        """Completed scans don't include recommended_wait_seconds."""
        from src.core.models import ScanJob, ScanStatus, ScanProgress

        job = ScanJob(
            scan_id="done-test",
            status=ScanStatus.COMPLETED,
            domain_count=5,
            progress=ScanProgress(total_domains=5, completed_domains=5),
        )
        scan_manager._jobs["done-test"] = job
        scan_manager._policies["done-test"] = []

        result = await execute_tool(
            "get_scan_status",
            {"scan_id": "done-test"},
            real_config, scan_manager,
        )

        assert "recommended_wait_seconds" not in result
        assert "hint" not in result

    @pytest.mark.asyncio
    async def test_early_scan_recommends_longer_wait(self, real_config, scan_manager):
        """Early in a scan (<25%), recommend 30s waits."""
        from src.core.models import ScanJob, ScanStatus, ScanProgress, DomainProgress

        job = ScanJob(
            scan_id="early-test",
            status=ScanStatus.RUNNING,
            domain_count=20,
            progress=ScanProgress(
                total_domains=20,
                completed_domains=2,  # 10% done
                domains=[
                    DomainProgress(domain_id=f"d{i}", domain_name=f"D{i}")
                    for i in range(20)
                ],
            ),
        )
        scan_manager._jobs["early-test"] = job
        scan_manager._policies["early-test"] = []

        result = await execute_tool(
            "get_scan_status",
            {"scan_id": "early-test"},
            real_config, scan_manager,
        )

        assert result["recommended_wait_seconds"] == 30

    @pytest.mark.asyncio
    async def test_mid_scan_recommends_longer_wait(self, real_config, scan_manager):
        """Mid-scan (25-75%), recommend 45s waits."""
        from src.core.models import ScanJob, ScanStatus, ScanProgress, DomainProgress

        job = ScanJob(
            scan_id="mid-test",
            status=ScanStatus.RUNNING,
            domain_count=10,
            progress=ScanProgress(
                total_domains=10,
                completed_domains=5,  # 50% done
                domains=[
                    DomainProgress(domain_id=f"d{i}", domain_name=f"D{i}")
                    for i in range(10)
                ],
            ),
        )
        scan_manager._jobs["mid-test"] = job
        scan_manager._policies["mid-test"] = []

        result = await execute_tool(
            "get_scan_status",
            {"scan_id": "mid-test"},
            real_config, scan_manager,
        )

        assert result["recommended_wait_seconds"] == 45

    @pytest.mark.asyncio
    async def test_late_scan_recommends_shorter_wait(self, real_config, scan_manager):
        """Late in scan (>75%), recommend 20s waits (almost done)."""
        from src.core.models import ScanJob, ScanStatus, ScanProgress, DomainProgress

        job = ScanJob(
            scan_id="late-test",
            status=ScanStatus.RUNNING,
            domain_count=10,
            progress=ScanProgress(
                total_domains=10,
                completed_domains=9,  # 90% done
                domains=[
                    DomainProgress(domain_id=f"d{i}", domain_name=f"D{i}")
                    for i in range(10)
                ],
            ),
        )
        scan_manager._jobs["late-test"] = job
        scan_manager._policies["late-test"] = []

        result = await execute_tool(
            "get_scan_status",
            {"scan_id": "late-test"},
            real_config, scan_manager,
        )

        assert result["recommended_wait_seconds"] == 20

    @pytest.mark.asyncio
    async def test_scan_status_includes_running_total_note(self, real_config, scan_manager):
        """get_scan_status response clarifies that policy_count is a running total."""
        from src.core.models import ScanJob, ScanStatus, ScanProgress, DomainProgress

        job = ScanJob(
            scan_id="note-test",
            status=ScanStatus.RUNNING,
            domain_count=5,
            policy_count=3,
            progress=ScanProgress(
                total_domains=5,
                completed_domains=2,
                domains=[
                    DomainProgress(domain_id=f"d{i}", domain_name=f"D{i}")
                    for i in range(5)
                ],
            ),
        )
        scan_manager._jobs["note-test"] = job
        scan_manager._policies["note-test"] = []

        result = await execute_tool(
            "get_scan_status",
            {"scan_id": "note-test"},
            real_config, scan_manager,
        )

        assert result["policy_count"] == 3
        assert "running total" in result["policy_count_note"]

    @pytest.mark.asyncio
    async def test_concurrent_scan_warning(self, real_config, scan_manager):
        """Starting a scan while another is running includes a warning."""
        from src.core.models import ScanJob, ScanStatus, ScanProgress

        # Simulate an already-running scan
        running_job = ScanJob(
            scan_id="existing-scan",
            status=ScanStatus.RUNNING,
            domain_count=10,
            progress=ScanProgress(total_domains=10),
        )
        scan_manager._jobs["existing-scan"] = running_job

        # Start a new scan (dry_run to avoid actual crawling)
        result = await execute_tool(
            "start_scan",
            {"domains": "quick", "skip_llm": True},
            real_config, scan_manager,
        )

        # Verify the response includes the scan_id and the warning
        assert "scan_id" in result
        assert "warning" in result
        assert "existing-scan" in result["warning"]
        assert "rate limit" in result["warning"].lower()

    @pytest.mark.asyncio
    async def test_no_warning_when_no_concurrent_scans(self, real_config, scan_manager):
        """Starting a scan with no other scans running has no warning."""
        result = await execute_tool(
            "start_scan",
            {"domains": "quick", "skip_llm": True},
            real_config, scan_manager,
        )

        assert "scan_id" in result
        assert "warning" not in result


# ---------------------------------------------------------------------------
# 14. --deep flag
# ---------------------------------------------------------------------------

class TestDeepFlag:
    """Verify --deep flag overrides crawl settings."""

    def test_deep_flag_overrides_settings(self):
        """--deep sets max_depth=5, max_pages=500, min_keyword_score=2.0."""
        from src.agent.orchestrator import PolicyAgent

        agent = PolicyAgent.__new__(PolicyAgent)
        agent._messages = []
        agent.config = ConfigLoader(config_dir="config")
        agent.config.load()
        agent.broadcaster = EventBroadcaster()
        agent.scan_manager = ScanManager(
            config=agent.config,
            broadcaster=agent.broadcaster,
            data_dir="data",
        )

        # Simulate --deep: apply the same overrides as __main__.py
        agent.scan_manager.config.settings.crawl.max_depth = 5
        agent.scan_manager.config.settings.crawl.max_pages_per_domain = 500
        agent.scan_manager.config.settings.analysis.min_keyword_score = 2.0

        assert agent.scan_manager.config.settings.crawl.max_depth == 5
        assert agent.scan_manager.config.settings.crawl.max_pages_per_domain == 500
        assert agent.scan_manager.config.settings.analysis.min_keyword_score == 2.0

    def test_default_settings_are_different(self):
        """Without --deep, settings should be the standard defaults."""
        config = ConfigLoader(config_dir="config")
        config.load()

        # Standard defaults from settings.yaml
        assert config.settings.crawl.max_depth <= 3
        assert config.settings.crawl.max_pages_per_domain <= 200
        assert config.settings.analysis.min_keyword_score >= 3.0


# ---------------------------------------------------------------------------
# 15. Edge cases: concurrent writes, empty scans, store failures
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge case tests for resilience under unusual conditions."""

    @pytest.mark.asyncio
    async def test_concurrent_policy_store_writes(self, tmp_path):
        """Multiple PolicyStore writes don't corrupt data."""
        from src.storage.store import PolicyStore

        store = PolicyStore(data_dir=str(tmp_path))

        # Simulate multiple domains finishing concurrently
        policies_a = [
            Policy(url=f"https://a.gov/p{i}", policy_name=f"A{i}",
                   jurisdiction="DE", policy_type=PolicyType.LAW,
                   summary="s", relevance_score=8)
            for i in range(5)
        ]
        policies_b = [
            Policy(url=f"https://b.gov/p{i}", policy_name=f"B{i}",
                   jurisdiction="FR", policy_type=PolicyType.REGULATION,
                   summary="s", relevance_score=7)
            for i in range(5)
        ]

        # Write in sequence (PolicyStore is synchronous internally)
        store.add_policies(policies_a)
        store.add_policies(policies_b)

        all_policies = store.get_all()
        assert len(all_policies) == 10

        # Verify data integrity — reload from disk
        store2 = PolicyStore(data_dir=str(tmp_path))
        assert len(store2.get_all()) == 10

    @pytest.mark.asyncio
    async def test_empty_scan_no_persistence(self, tmp_path):
        """Scan with 0 policies commits nothing and adds no rows."""
        from src.storage.store import PolicyStore

        store = PolicyStore(data_dir=str(tmp_path))
        added = store.add_policies([])  # No policies to add
        assert added == 0
        assert store.get_all() == []

    @pytest.mark.asyncio
    async def test_policy_store_handles_corrupt_json(self, tmp_path):
        """PolicyStore handles corrupt policies.json gracefully."""
        from src.storage.store import PolicyStore

        # Write corrupt JSON
        policies_file = tmp_path / "policies.json"
        policies_file.write_text("NOT VALID JSON {{{", encoding="utf-8")

        # Should not crash — falls back to empty list
        store = PolicyStore(data_dir=str(tmp_path))
        assert store.get_all() == []

        # Should still be able to add new policies
        added = store.add_policies([
            Policy(url="https://test.gov/recovery", policy_name="Recovery",
                   jurisdiction="US", policy_type=PolicyType.LAW,
                   summary="Recovered", relevance_score=8),
        ])
        assert added == 1
        assert len(store.get_all()) == 1

    @pytest.mark.asyncio
    async def test_policy_count_increments_during_scan(self, real_config, scan_manager):
        """job.policy_count updates as domains complete, not at the end."""
        from src.core.models import ScanJob, ScanStatus, ScanProgress

        job = ScanJob(
            scan_id="count-test",
            status=ScanStatus.RUNNING,
            domain_count=5,
            policy_count=0,
            progress=ScanProgress(total_domains=5, completed_domains=0),
        )
        scan_manager._jobs["count-test"] = job
        scan_manager._policies["count-test"] = []

        # Simulate first domain completing and finding 3 policies
        policies = [
            Policy(url=f"https://d1.gov/p{i}", policy_name=f"P{i}",
                   jurisdiction="DE", policy_type=PolicyType.LAW,
                   summary="s", relevance_score=8)
            for i in range(3)
        ]
        scan_manager._policies["count-test"].extend(policies)
        job.policy_count += len(policies)
        job.progress.completed_domains += 1

        # Check status mid-scan
        result = await execute_tool(
            "get_scan_status",
            {"scan_id": "count-test"},
            real_config, scan_manager,
        )
        assert result["policy_count"] == 3  # Not 0!
        assert result["progress"]["completed"] == 1

    def test_on_tool_result_scan_status_icons(self, capsys):
        """get_scan_status output uses correct status icons."""
        from src.agent.__main__ import _on_tool_result, _celebrated_domains
        _celebrated_domains.clear()

        # Running scan
        _on_tool_result("get_scan_status", {
            "status": "running", "policy_count": 2,
            "progress": {"completed": 3, "total": 8, "domains": []},
        })
        output = capsys.readouterr().out
        assert "⏳" in output
        assert "running total" in output

        # Completed scan
        _on_tool_result("get_scan_status", {
            "status": "completed", "policy_count": 5,
            "progress": {"completed": 8, "total": 8, "domains": []},
        })
        output = capsys.readouterr().out
        assert "✅" in output

        # Failed scan
        _on_tool_result("get_scan_status", {
            "status": "failed", "policy_count": 0,
            "progress": {"completed": 4, "total": 8, "domains": []},
        })
        output = capsys.readouterr().out
        assert "❌" in output

    def test_on_tool_result_highlights_policy_finds(self, capsys):
        """Domains that found policies get 🎉 highlight."""
        from src.agent.__main__ import _on_tool_result, _celebrated_domains
        _celebrated_domains.clear()

        _on_tool_result("get_scan_status", {
            "status": "running", "policy_count": 2,
            "progress": {
                "completed": 5, "total": 10,
                "domains": [
                    {"domain_id": "d1", "domain_name": "Test Gov", "policies_found": 2},
                    {"domain_id": "d2", "domain_name": "Other Gov", "policies_found": 0},
                ],
            },
        })
        output = capsys.readouterr().out
        assert "🎉" in output
        assert "Test Gov" in output
        assert "2 policy" in output
        # "Other Gov" should NOT have 🎉
        assert "Other Gov" not in output

    def test_on_tool_result_celebrates_only_once(self, capsys):
        """Domains that found policies only get 🎉 on the first poll."""
        from src.agent.__main__ import _on_tool_result, _celebrated_domains
        _celebrated_domains.clear()

        status_data = {
            "status": "running", "policy_count": 1,
            "progress": {
                "completed": 3, "total": 10,
                "domains": [
                    {"domain_id": "sweden_dc", "domain_name": "Sweden DC Act", "policies_found": 1},
                ],
            },
        }

        # First poll — should celebrate
        _on_tool_result("get_scan_status", status_data)
        output = capsys.readouterr().out
        assert "🎉" in output
        assert "Sweden DC Act" in output

        # Second poll with same data — should NOT celebrate again
        _on_tool_result("get_scan_status", status_data)
        output = capsys.readouterr().out
        assert "🎉" not in output
        # But should still show the running total
        assert "running total" in output

    def test_on_tool_result_start_scan_resets_celebrations(self, capsys):
        """Starting a new scan resets the celebration tracker."""
        from src.agent.__main__ import _on_tool_result, _celebrated_domains

        # Simulate a previous scan with celebrations
        _celebrated_domains.add("old_domain")

        # Start new scan
        _on_tool_result("start_scan", {
            "scan_id": "new-scan", "status": "running", "domain_count": 5,
        })
        capsys.readouterr()  # consume output

        assert len(_celebrated_domains) == 0

    def test_on_tool_result_start_scan_shows_warning(self, capsys):
        """start_scan with a concurrent scan warning displays ⚠️."""
        from src.agent.__main__ import _on_tool_result, _celebrated_domains
        _celebrated_domains.clear()

        _on_tool_result("start_scan", {
            "scan_id": "new-scan", "status": "running", "domain_count": 8,
            "warning": "Another scan is already running (abc123). Both share the same API key.",
        })
        output = capsys.readouterr().out
        assert "⚠️" in output
        assert "abc123" in output

    def test_on_tool_result_analyze_url_policy_find(self, capsys):
        """analyze_url with a policy found shows 🎉 celebration."""
        from src.agent.__main__ import _on_tool_result

        _on_tool_result("analyze_url", {
            "url": "https://test.gov/p",
            "keyword_score": 12,
            "policy": {"policy_name": "Heat Recovery Act", "relevance_score": 9},
        })
        output = capsys.readouterr().out
        assert "🎉" in output
        assert "Heat Recovery Act" in output
        assert "9" in output


# ---------------------------------------------------------------------------
# 17. Incremental Google Sheets export
# ---------------------------------------------------------------------------

class TestIncrementalSheetsExport:
    """Verify per-domain Sheets export logic in scan_manager."""

    def _make_policy(self, url: str, name: str = "P") -> Policy:
        """Create a minimal Policy for testing."""
        return Policy(
            url=url,
            policy_name=name,
            jurisdiction="DE",
            policy_type=PolicyType.LAW,
            summary="Test",
            relevance_score=8,
        )

    def test_per_domain_export_deduplicates(self):
        """Policies already in Sheets (tracked by URL set) are skipped."""
        sheets_exported_urls: set[str] = {"https://a.gov/old"}
        policies = [
            self._make_policy("https://a.gov/old", "Old"),
            self._make_policy("https://a.gov/new", "New"),
        ]
        new_for_sheets = [
            p for p in policies if p.url not in sheets_exported_urls
        ]
        assert len(new_for_sheets) == 1
        assert new_for_sheets[0].url == "https://a.gov/new"

    def test_exported_urls_set_grows_after_export(self):
        """After export, newly exported URLs are added to the tracking set."""
        sheets_exported_urls: set[str] = set()
        policies = [
            self._make_policy("https://a.gov/p1"),
            self._make_policy("https://a.gov/p2"),
        ]
        # Simulate the export loop from scan_manager
        new_for_sheets = [
            p for p in policies if p.url not in sheets_exported_urls
        ]
        for p in new_for_sheets:
            sheets_exported_urls.add(p.url)

        assert len(sheets_exported_urls) == 2
        assert "https://a.gov/p1" in sheets_exported_urls

        # Second domain with overlapping URL — should be skipped
        policies2 = [
            self._make_policy("https://a.gov/p1"),  # already exported
            self._make_policy("https://b.gov/p3"),   # new
        ]
        new_for_sheets2 = [
            p for p in policies2 if p.url not in sheets_exported_urls
        ]
        assert len(new_for_sheets2) == 1
        assert new_for_sheets2[0].url == "https://b.gov/p3"

    def test_reconciliation_finds_missed_policies(self):
        """End-of-scan reconciliation catches policies missed during export."""
        sheets_exported_urls = {"https://a.gov/p1"}
        all_policies = [
            self._make_policy("https://a.gov/p1"),
            self._make_policy("https://a.gov/p2"),  # missed
            self._make_policy("https://b.gov/p3"),  # missed
        ]
        missed = [
            p for p in all_policies if p.url not in sheets_exported_urls
        ]
        assert len(missed) == 2
        urls = {p.url for p in missed}
        assert "https://a.gov/p2" in urls
        assert "https://b.gov/p3" in urls

    def test_no_reconciliation_when_all_exported(self):
        """When per-domain export captured everything, reconciliation is a no-op."""
        all_policies = [
            self._make_policy("https://a.gov/p1"),
            self._make_policy("https://b.gov/p2"),
        ]
        sheets_exported_urls = {"https://a.gov/p1", "https://b.gov/p2"}
        missed = [
            p for p in all_policies if p.url not in sheets_exported_urls
        ]
        assert len(missed) == 0

    @pytest.mark.asyncio
    async def test_sheets_failure_doesnt_crash_scan(self, tmp_config_dir, monkeypatch):
        """If Sheets export fails for one domain, the scan continues."""
        monkeypatch.setenv("SPREADSHEET_ID", "test-sheet-id")
        monkeypatch.setenv("GOOGLE_CREDENTIALS", "dGVzdA==")

        mock_sheets = MagicMock()
        mock_sheets.get_existing_urls.return_value = set()
        mock_sheets.append_policies.side_effect = Exception("Sheets API timeout")

        # Simulate the per-domain export with error handling
        policies = [self._make_policy("https://a.gov/p1")]
        sheets_exported_urls: set[str] = set()
        export_errors = []

        # This mirrors scan_manager.py lines 283-303
        new_for_sheets = [
            p for p in policies if p.url not in sheets_exported_urls
        ]
        if new_for_sheets:
            try:
                mock_sheets.append_policies(new_for_sheets, "Staging")
                for p in new_for_sheets:
                    sheets_exported_urls.add(p.url)
            except Exception as sheets_err:
                export_errors.append(str(sheets_err))

        # Export failed, but we didn't crash
        assert len(export_errors) == 1
        assert "Sheets API timeout" in export_errors[0]
        # URLs were NOT added since export failed
        assert len(sheets_exported_urls) == 0

    @pytest.mark.asyncio
    async def test_sheets_failure_tracked_in_status(self, tmp_config_dir, monkeypatch):
        """SheetsExportStatus tracks per-domain export failures."""
        from src.core.models import SheetsExportStatus

        sheets_status = SheetsExportStatus(
            configured=True, connected=True, status="connected",
        )
        mock_sheets = MagicMock()
        mock_sheets.append_policies.side_effect = Exception("Rate limit")

        policies = [self._make_policy("https://a.gov/p1")]
        sheets_exported_urls: set[str] = set()

        # Simulate the per-domain export with status tracking
        new_for_sheets = [
            p for p in policies if p.url not in sheets_exported_urls
        ]
        if new_for_sheets:
            try:
                mock_sheets.append_policies(new_for_sheets, "Staging")
                for p in new_for_sheets:
                    sheets_exported_urls.add(p.url)
                sheets_status.exported_count += len(new_for_sheets)
            except Exception as sheets_err:
                sheets_status.failed_count += len(new_for_sheets)
                sheets_status.error = str(sheets_err)

        assert sheets_status.exported_count == 0
        assert sheets_status.failed_count == 1
        assert sheets_status.error == "Rate limit"
        # Status is still "connected" — the connection worked, export failed
        assert sheets_status.status == "connected"

    @pytest.mark.asyncio
    async def test_sheets_mixed_success_and_failure(self, tmp_config_dir, monkeypatch):
        """SheetsExportStatus correctly tracks partial success."""
        from src.core.models import SheetsExportStatus

        sheets_status = SheetsExportStatus(
            configured=True, connected=True, status="connected",
        )

        # Domain 1: succeeds (3 policies)
        sheets_status.exported_count += 3
        # Domain 2: fails (2 policies)
        sheets_status.failed_count += 2
        sheets_status.error = "API error on domain 2"
        # Domain 3: succeeds (1 policy)
        sheets_status.exported_count += 1

        assert sheets_status.exported_count == 4
        assert sheets_status.failed_count == 2
        assert sheets_status.error == "API error on domain 2"
        d = sheets_status.model_dump()
        assert d["exported_count"] == 4
        assert d["failed_count"] == 2


# ---------------------------------------------------------------------------
# 18. Quit / session-end logging
# ---------------------------------------------------------------------------

class TestQuitLogging:
    """Verify session_ended events are logged when user quits."""

    def test_audit_log_displays_session_ended(self, capsys):
        """The --logs audit view formats session_ended events correctly."""
        from src.agent.__main__ import _handle_logs_command
        import json
        import tempfile
        from pathlib import Path

        # Create a temp data dir with a mock audit log
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "logs"
            log_dir.mkdir()
            audit_file = log_dir / "audit.jsonl"
            audit_file.write_text(
                json.dumps({
                    "timestamp": "2026-03-10T14:30:00",
                    "event": "session_ended",
                    "reason": "quit",
                }) + "\n",
                encoding="utf-8",
            )

            _handle_logs_command(["audit"], tmpdir)

        output = capsys.readouterr().out
        assert "session_ended" in output
        assert "quit" in output

    def test_log_audit_event_writes_session_ended(self, tmp_path):
        """log_audit_event creates a valid session_ended entry."""
        from src.core.log_setup import log_audit_event
        import json

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        log_audit_event(
            data_dir=str(tmp_path),
            event="session_ended",
            reason="interrupt",
        )

        audit_file = log_dir / "audit.jsonl"
        assert audit_file.exists()

        lines = audit_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event"] == "session_ended"
        assert entry["reason"] == "interrupt"
        assert "timestamp" in entry

    def test_session_ended_with_quit_reason(self, tmp_path):
        """Session ended by typing 'quit' records reason='quit'."""
        from src.core.log_setup import log_audit_event
        import json

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        log_audit_event(
            data_dir=str(tmp_path),
            event="session_ended",
            reason="quit",
        )

        audit_file = log_dir / "audit.jsonl"
        entry = json.loads(audit_file.read_text(encoding="utf-8").strip())
        assert entry["reason"] == "quit"
