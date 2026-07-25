"""Tests for the agent tools and orchestrator."""

from unittest.mock import AsyncMock, MagicMock

import anthropic
import pytest

from src.agent.tools import get_all_tools, execute_tool, POLICY_TOOLS, WEB_SEARCH_TOOL
from src.agent.orchestrator import (
    PolicyAgent, _build_system_prompt, _get_retry_delay, _trim_conversation,
    MAX_API_RETRIES, BASE_RETRY_DELAY, MAX_RETRY_DELAY, MAX_CONVERSATION_TURNS,
)
from src.core.config import ConfigLoader
from src.orchestration.events import EventBroadcaster
from src.orchestration.scan_manager import ScanManager


@pytest.fixture
def config():
    c = ConfigLoader(config_dir="config")
    c.load()
    return c


@pytest.fixture
def scan_manager(config):
    broadcaster = EventBroadcaster()
    return ScanManager(config=config, broadcaster=broadcaster, data_dir="data")


class TestToolDefinitions:
    """Verify tool definitions match Anthropic API format."""

    def test_total_tool_count(self):
        tools = get_all_tools()
        # 12 policy tools + list_groups + add_domain + web_search = 15
        assert len(tools) == 15

    def test_policy_tools_have_required_fields(self):
        for tool in POLICY_TOOLS:
            assert "name" in tool, "Missing name in tool"
            assert "description" in tool, f"Missing description in {tool['name']}"
            assert "input_schema" in tool, f"Missing input_schema in {tool['name']}"
            schema = tool["input_schema"]
            assert schema["type"] == "object", f"Schema type not 'object' in {tool['name']}"
            assert "properties" in schema, f"Missing properties in {tool['name']}"

    def test_web_search_is_server_side(self):
        assert WEB_SEARCH_TOOL["type"] == "web_search_20250305"
        assert WEB_SEARCH_TOOL["name"] == "web_search"
        assert "input_schema" not in WEB_SEARCH_TOOL

    def test_add_domain_has_url_required(self):
        tools = get_all_tools()
        add_domain = next(t for t in tools if t.get("name") == "add_domain")
        assert "url" in add_domain["input_schema"]["required"]

    def test_no_duplicate_tool_names(self):
        tools = get_all_tools()
        names = [t["name"] for t in tools]
        assert len(names) == len(set(names))

    def test_search_policies_query_description_says_full_text(self):
        search_tool = next(t for t in POLICY_TOOLS if t["name"] == "search_policies")
        query_description = search_tool["input_schema"]["properties"]["query"]["description"]
        assert "full-text" in query_description.lower()


class TestToolDispatch:
    """Test that execute_tool dispatches correctly."""

    @pytest.mark.asyncio
    async def test_list_domains(self, config, scan_manager):
        result = await execute_tool("list_domains", {"group": "all"}, config, scan_manager)
        assert "count" in result
        assert "domains" in result
        assert result["count"] > 0

    @pytest.mark.asyncio
    async def test_list_domains_with_region(self, config, scan_manager):
        result = await execute_tool("list_domains", {"region": "eu"}, config, scan_manager)
        assert "count" in result
        for d in result["domains"]:
            assert "eu" in d["region"]

    @pytest.mark.asyncio
    async def test_list_domains_respects_domain_overrides(self, config, tmp_path):
        # A domain disabled via the admin Sources panel must not show as
        # available through agent chat either (review finding on WP-9).
        from src.storage.domain_overrides import DomainOverridesStore
        manager = ScanManager(
            config=config, broadcaster=EventBroadcaster(), data_dir=str(tmp_path),
        )
        victim = config.get_enabled_domains("all")[0]["id"]
        DomainOverridesStore(data_dir=str(tmp_path)).set_enabled(victim, False)

        result = await execute_tool("list_domains", {"group": "all"}, config, manager)

        assert victim not in [d["id"] for d in result["domains"]]

    @pytest.mark.asyncio
    async def test_estimate_cost(self, config, scan_manager):
        result = await execute_tool("estimate_cost", {"domains": "quick"}, config, scan_manager)
        assert "domain_count" in result
        assert "estimated_cost_usd" in result
        assert result["estimated_cost_usd"] > 0

    @pytest.mark.asyncio
    async def test_match_keywords(self, config, scan_manager):
        result = await execute_tool(
            "match_keywords",
            {"text": "data center waste heat recovery policy"},
            config, scan_manager,
        )
        assert "score" in result
        assert result["score"] > 0
        assert len(result["matches"]) > 0

    @pytest.mark.asyncio
    async def test_get_policy_stats(self, config, scan_manager):
        result = await execute_tool("get_policy_stats", {}, config, scan_manager)
        assert "total" in result
        assert "by_jurisdiction" in result
        assert "by_type" in result

    @pytest.mark.asyncio
    async def test_unknown_tool(self, config, scan_manager):
        result = await execute_tool("nonexistent_tool", {}, config, scan_manager)
        assert "error" in result
        assert "Unknown tool" in result["error"]

    @pytest.mark.asyncio
    async def test_get_domain_config_not_found(self, config, scan_manager):
        result = await execute_tool("get_domain_config", {"domain_id": "fake_domain"}, config, scan_manager)
        assert "error" in result
        assert "not found" in result["error"]


class TestSystemPrompt:
    """Test system prompt generation."""

    def test_includes_domain_count(self, config):
        prompt = _build_system_prompt(config)
        assert "275" in prompt or "government websites" in prompt

    def test_includes_regions(self, config):
        prompt = _build_system_prompt(config)
        assert "eu" in prompt
        assert "us" in prompt

    def test_includes_tool_descriptions(self, config):
        prompt = _build_system_prompt(config)
        assert "list_domains" in prompt
        assert "start_scan" in prompt
        assert "web_search" in prompt
        assert "add_domain" in prompt
        assert "analyze_url" in prompt

    def test_non_technical_language(self, config):
        prompt = _build_system_prompt(config)
        assert "not programmers" in prompt
        assert "plain" in prompt

    def test_polling_guidance_in_prompt(self, config):
        """System prompt should discourage aggressive polling."""
        prompt = _build_system_prompt(config)
        assert "recommended_wait_seconds" in prompt
        assert "30 seconds" in prompt


# --- _get_retry_delay ---

class TestGetRetryDelay:
    """Test retry delay extraction and exponential backoff."""

    def test_extracts_retry_after_header(self):
        """Should use retry-after header when present."""
        error = MagicMock(spec=anthropic.RateLimitError)
        error.response = MagicMock()
        error.response.headers = {"retry-after": "15"}
        delay = _get_retry_delay(error, attempt=1)
        assert delay == 15.0

    def test_caps_retry_after_at_max(self):
        """Extremely long retry-after should be capped."""
        error = MagicMock(spec=anthropic.RateLimitError)
        error.response = MagicMock()
        error.response.headers = {"retry-after": "9999"}
        delay = _get_retry_delay(error, attempt=1)
        assert delay == MAX_RETRY_DELAY

    def test_backoff_when_no_header(self):
        """Should use exponential backoff when no retry-after header."""
        error = MagicMock(spec=anthropic.RateLimitError)
        error.response = None
        delay1 = _get_retry_delay(error, attempt=1)
        delay2 = _get_retry_delay(error, attempt=2)
        delay3 = _get_retry_delay(error, attempt=3)
        assert delay1 == BASE_RETRY_DELAY           # 10s
        assert delay2 == BASE_RETRY_DELAY * 4        # 40s
        assert delay3 == min(BASE_RETRY_DELAY * 16, MAX_RETRY_DELAY)  # capped

    def test_backoff_with_invalid_header(self):
        """Should fall back to backoff when header is garbage."""
        error = MagicMock(spec=anthropic.RateLimitError)
        error.response = MagicMock()
        error.response.headers = {"retry-after": "not-a-number"}
        delay = _get_retry_delay(error, attempt=1)
        assert delay == BASE_RETRY_DELAY


# --- Agent Rate Limit Retry ---

def _make_text_response(text: str):
    """Create a mock API response with text content and end_turn."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    response.stop_reason = "end_turn"
    return response


class TestAgentRateLimitRetry:
    """Test that the agent loop retries on rate limit errors."""

    def _build_agent(self):
        """Create a PolicyAgent without real API key (for testing)."""
        agent = PolicyAgent.__new__(PolicyAgent)
        agent.config = ConfigLoader(config_dir="config")
        agent.config.load()
        agent.broadcaster = EventBroadcaster()
        agent.scan_manager = ScanManager(
            config=agent.config, broadcaster=agent.broadcaster, data_dir="data",
        )
        agent.tools = get_all_tools()
        agent.system_prompt = "test"
        agent.model = "test-model"
        agent._messages = []  # conversation memory
        return agent

    @pytest.mark.asyncio
    async def test_rate_limit_retry_succeeds(self):
        """Agent should retry on 429 and succeed on second attempt."""
        agent = self._build_agent()

        rate_error = anthropic.RateLimitError.__new__(anthropic.RateLimitError)
        rate_error.response = None

        success_response = _make_text_response("Here are the results.")

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[rate_error, success_response]
        )
        agent.client = mock_client

        text_output = []
        result = await agent.run(
            "test message",
            on_text=lambda t: text_output.append(t),
        )

        assert "results" in result
        assert mock_client.messages.create.call_count == 2
        # Check that user was notified about the retry
        assert any("Rate limited" in t or "waiting" in t for t in text_output)

    @pytest.mark.asyncio
    async def test_rate_limit_retry_exhausted(self):
        """Agent should return friendly error after exhausting retries."""
        agent = self._build_agent()

        rate_error = anthropic.RateLimitError.__new__(anthropic.RateLimitError)
        rate_error.response = None

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[rate_error] * MAX_API_RETRIES
        )
        agent.client = mock_client

        result = await agent.run("test message")

        assert "Rate limit exceeded" in result
        assert "saved" in result.lower() or "search_policies" in result
        assert mock_client.messages.create.call_count == MAX_API_RETRIES

    @pytest.mark.asyncio
    async def test_overload_error_retries(self):
        """Agent should retry on 529 overloaded errors."""
        agent = self._build_agent()

        # Simulate 529 overload
        overload_error = anthropic.APIStatusError.__new__(anthropic.APIStatusError)
        overload_error.status_code = 529
        overload_error.response = None

        success_response = _make_text_response("Done.")

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[overload_error, success_response]
        )
        agent.client = mock_client

        result = await agent.run("test message")

        assert "Done" in result
        assert mock_client.messages.create.call_count == 2

    @pytest.mark.asyncio
    async def test_auth_error_no_retry(self):
        """Authentication errors should fail immediately — no retry."""
        agent = self._build_agent()

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.AuthenticationError.__new__(
                anthropic.AuthenticationError
            )
        )
        agent.client = mock_client

        result = await agent.run("test message")

        assert "Authentication failed" in result
        # Should NOT retry — only 1 call
        assert mock_client.messages.create.call_count == 1


# --- Conversation Memory ---

class TestConversationMemory:
    """Test that conversation history persists across run() calls."""

    def _build_agent(self):
        """Create a PolicyAgent with mock client for memory tests."""
        agent = PolicyAgent.__new__(PolicyAgent)
        agent.config = ConfigLoader(config_dir="config")
        agent.config.load()
        agent.broadcaster = EventBroadcaster()
        agent.scan_manager = ScanManager(
            config=agent.config, broadcaster=agent.broadcaster, data_dir="data",
        )
        agent.tools = get_all_tools()
        agent.system_prompt = "test"
        agent.model = "test-model"
        agent._messages = []
        return agent

    @pytest.mark.asyncio
    async def test_messages_persist_across_runs(self):
        """Conversation history should grow with each run() call."""
        agent = self._build_agent()

        # First turn
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_text_response("Scan started with ID abc123.")
        )
        agent.client = mock_client

        await agent.run("Start a Nordic scan")
        # After first run: user message + assistant response = 2 messages
        assert len(agent._messages) == 2
        assert agent._messages[0]["role"] == "user"
        assert agent._messages[0]["content"] == "Start a Nordic scan"
        assert agent._messages[1]["role"] == "assistant"

        # Second turn
        mock_client.messages.create = AsyncMock(
            return_value=_make_text_response("The scan is 50% complete.")
        )

        await agent.run("What's the scan status?")
        # After second run: 4 messages (2 per turn)
        assert len(agent._messages) == 4
        assert agent._messages[2]["role"] == "user"
        assert agent._messages[2]["content"] == "What's the scan status?"
        assert agent._messages[3]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_reset_conversation_clears_history(self):
        """reset_conversation() should empty the message history."""
        agent = self._build_agent()

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_text_response("Hello!")
        )
        agent.client = mock_client

        await agent.run("Hi")
        assert len(agent._messages) > 0

        agent.reset_conversation()
        assert len(agent._messages) == 0

    @pytest.mark.asyncio
    async def test_messages_sent_to_api_include_history(self):
        """The API call should receive the full conversation history."""
        agent = self._build_agent()

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_text_response("Got it.")
        )
        agent.client = mock_client

        await agent.run("First message")
        await agent.run("Second message")

        # After both runs, _messages should contain full conversation:
        # user1, assistant1, user2, assistant2
        assert len(agent._messages) == 4
        assert agent._messages[0]["content"] == "First message"
        assert agent._messages[0]["role"] == "user"
        assert agent._messages[1]["role"] == "assistant"
        assert agent._messages[2]["content"] == "Second message"
        assert agent._messages[2]["role"] == "user"
        assert agent._messages[3]["role"] == "assistant"

        # Verify both API calls were made (2 run() calls)
        assert mock_client.messages.create.call_count == 2


# --- Conversation Trimming ---

class TestConversationTrimming:
    """Test that conversation history is trimmed to stay within limits."""

    def test_no_trim_when_under_limit(self):
        """Short conversations should not be trimmed."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": [MagicMock(type="text", text="Hi")]},
            {"role": "user", "content": "Bye"},
            {"role": "assistant", "content": [MagicMock(type="text", text="Bye")]},
        ]
        result = _trim_conversation(messages)
        assert len(result) == len(messages)

    def test_trim_when_over_limit(self):
        """Long conversations should be trimmed to MAX_CONVERSATION_TURNS."""
        # Need enough messages to exceed BOTH the size threshold (turns*3)
        # and the user-count threshold (>MAX_CONVERSATION_TURNS user messages).
        # Create 2x the limit to ensure trimming kicks in.
        count = MAX_CONVERSATION_TURNS * 2
        messages = []
        for i in range(count):
            messages.append({"role": "user", "content": f"Message {i}"})
            messages.append({"role": "assistant", "content": f"Reply {i}"})

        result = _trim_conversation(messages)

        # Should be trimmed
        assert len(result) < len(messages)
        # Last message should still be present
        assert result[-1]["content"] == f"Reply {count - 1}"

    def test_trim_preserves_recent_turns(self):
        """Trimming should keep the most recent turns, not the oldest."""
        count = MAX_CONVERSATION_TURNS * 2
        messages = []
        for i in range(count):
            messages.append({"role": "user", "content": f"Turn {i}"})
            messages.append({"role": "assistant", "content": f"Response {i}"})

        result = _trim_conversation(messages)

        # The newest turn should be present
        newest_idx = count - 1
        assert any(f"Turn {newest_idx}" in str(m.get("content", "")) for m in result)
        # The oldest turn should be gone
        assert not any("Turn 0" in str(m.get("content", "")) for m in result)


# --- list_scans tool ---

class TestListScansTool:
    """Test the list_scans tool."""

    def test_list_scans_tool_exists(self):
        """list_scans should be in the tool list."""
        tools = get_all_tools()
        names = [t.get("name") for t in tools]
        assert "list_scans" in names

    @pytest.mark.asyncio
    async def test_list_scans_empty(self, config, scan_manager):
        """list_scans with no scans returns empty list."""
        result = await execute_tool("list_scans", {}, config, scan_manager)
        assert result["count"] == 0
        assert result["scans"] == []

    @pytest.mark.asyncio
    async def test_list_scans_shows_running(self, config, scan_manager):
        """list_scans shows running scans."""
        from src.core.models import ScanJob, ScanStatus, ScanProgress

        job = ScanJob(
            scan_id="test-scan",
            status=ScanStatus.RUNNING,
            domain_count=10,
            domain_group="nordic",
            policy_count=3,
            progress=ScanProgress(total_domains=10, completed_domains=5),
        )
        scan_manager._jobs["test-scan"] = job

        result = await execute_tool("list_scans", {}, config, scan_manager)

        assert result["count"] == 1
        scan = result["scans"][0]
        assert scan["scan_id"] == "test-scan"
        assert scan["status"] == "running"
        assert scan["domain_group"] == "nordic"
        assert scan["policy_count"] == 3
        assert scan["progress"]["completed"] == 5
        assert scan["progress"]["total"] == 10


# --- Logging setup ---
# Comprehensive logging tests are in tests/unit/test_logging.py.
# This file only contains agent-specific logging concerns.
