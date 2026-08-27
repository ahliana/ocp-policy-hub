"""Tests for the news tripwire channel (src/signals/news.py)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.signals.news import (
    MAX_FEED_BODY_CHARS,
    _collect,
    dedupe_items,
    parse_gdelt,
    parse_rss,
    run_news_signals,
    NewsItem,
)
from src.storage.leads import LeadStore
from src.storage.signals_status import SignalsStatusStore


GDELT_PAYLOAD = json.dumps({
    "articles": [
        {
            "url": "https://example.dk/nyhed/overskudsvarme-lov",
            "title": "Ny lov om overskudsvarme fra datacentre",
            "sourcecountry": "Denmark",
            "seendate": "20260710T120000Z",
        },
        {
            "url": "https://example.dk/nyhed/overskudsvarme-lov",
            "title": "Duplicate of the same article",
        },
    ],
})

RSS_PAYLOAD = """<?xml version="1.0"?>
<rss><channel>
  <item>
    <title>Germany tightens data centre heat reuse rules</title>
    <link>https://news.example.com/germany-heat</link>
    <description>The EnEfG amendment raises reuse quotas.</description>
  </item>
  <item>
    <title>Unrelated sports story</title>
    <link>https://news.example.com/sports</link>
  </item>
</channel></rss>
"""


class TestParsers:
    def test_parse_gdelt(self):
        items = parse_gdelt(GDELT_PAYLOAD, origin_query="overskudsvarme")
        assert len(items) == 2
        assert items[0].url.startswith("https://example.dk")
        assert "overskudsvarme" in items[0].title.lower()

    def test_parse_gdelt_malformed(self):
        assert parse_gdelt("not json", origin_query="x") == []

    def test_parse_rss(self):
        items = parse_rss(RSS_PAYLOAD, origin_query="feed:test")
        assert len(items) == 2
        assert items[0].title == "Germany tightens data centre heat reuse rules"
        assert items[0].snippet.startswith("The EnEfG")

    def test_parse_rss_malformed(self):
        assert parse_rss("<not-rss>", origin_query="feed:test") == []


class TestDedupe:
    def test_dedupes_by_url_and_title(self):
        items = [
            NewsItem(title="A story", url="https://a.example/x"),
            NewsItem(title="A story", url="https://a.example/x"),
            NewsItem(title="A STORY  ", url="https://b.example/y"),  # same title, diff url
            NewsItem(title="Different", url="https://c.example/z"),
        ]
        deduped = dedupe_items(items)
        assert len(deduped) == 2


@pytest.fixture
def signals_config():
    return {
        "enabled": True,
        "max_leads_per_run": 10,
        "gdelt": {"enabled": True, "queries": [{"q": '"overskudsvarme"', "timespan": "1w"}]},
        "google_news": {"enabled": True, "queries": [{"q": "fernwärme gesetz", "hl": "de", "gl": "DE"}]},
        "rss_feeds": [{"name": "DCD", "url": "https://feeds.example/rss"}],
        "watch_pages": [],
    }


def _mock_http(gdelt=GDELT_PAYLOAD, rss=RSS_PAYLOAD):
    async def fake_get(url, *args, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = gdelt if "gdeltproject" in url else rss
        return resp

    client = MagicMock()
    client.get = AsyncMock(side_effect=fake_get)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _mock_http_routed(handlers):
    """handlers: [(url_substring, outcome), ...] checked in order.

    outcome is either an Exception instance (raised) or a (status_code,
    text) tuple (a mock response is returned).
    """
    async def fake_get(url, *args, **kwargs):
        for substr, outcome in handlers:
            if substr in url:
                if isinstance(outcome, Exception):
                    raise outcome
                status_code, text = outcome
                resp = MagicMock()
                resp.status_code = status_code
                resp.text = text
                return resp
        raise AssertionError(f"unrouted URL in test fixture: {url}")

    client = MagicMock()
    client.get = AsyncMock(side_effect=fake_get)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


class TestParserFixtureDegradation:
    """WP-42: malformed feed bodies degrade to zero items, never an exception."""

    @pytest.mark.small
    def test_truncated_xml_yields_no_items(self):
        truncated = '<?xml version="1.0"?><rss><channel><item><title>Cut off'
        assert parse_rss(truncated, origin_query="feed:test") == []

    @pytest.mark.small
    def test_html_served_instead_of_rss_yields_no_items(self):
        html = "<html><head><title>404 Not Found</title></head><body>Nope</body></html>"
        assert parse_rss(html, origin_query="feed:test") == []

    @pytest.mark.small
    def test_wrong_declared_encoding_does_not_raise(self):
        # The declared encoding is a lie relative to the actual content, but
        # parse_rss never decodes bytes itself (httpx already produced this
        # str) - it must not choke on the mismatch.
        payload = (
            '<?xml version="1.0" encoding="ISO-8859-1"?>'
            "<rss><channel><item><title>Fernwärme Gesetz äöü"
            "</title><link>https://example.de/a</link></item></channel></rss>"
        )
        items = parse_rss(payload, origin_query="feed:test")
        assert len(items) == 1
        assert items[0].url == "https://example.de/a"

    @pytest.mark.small
    def test_empty_feed_yields_no_items_and_warns(self, caplog):
        with caplog.at_level("WARNING"):
            assert parse_rss("", origin_query="feed:test") == []
        assert any("empty" in r.message.lower() for r in caplog.records)

    @pytest.mark.small
    def test_whitespace_only_feed_treated_as_empty(self, caplog):
        with caplog.at_level("WARNING"):
            assert parse_rss("   \n  ", origin_query="feed:test") == []
        assert any("empty" in r.message.lower() for r in caplog.records)

    @pytest.mark.small
    def test_entities_and_cdata_parse_correctly(self):
        payload = (
            "<rss><channel><item>"
            "<title><![CDATA[Heat & Power Act]]></title>"
            "<link><![CDATA[https://example.de/heat-power]]></link>"
            "<description><![CDATA[Covers &amp; expands district heating]]></description>"
            "</item></channel></rss>"
        )
        items = parse_rss(payload, origin_query="feed:test")
        assert len(items) == 1
        assert items[0].title == "Heat & Power Act"
        assert items[0].url == "https://example.de/heat-power"


class TestFeedSizeCap:
    # Medium, not small: on Windows the asyncio Proactor event loop opens a
    # real localhost socketpair for its own self-pipe during loop setup,
    # independent of any network I/O the test performs - pytest-socket's
    # small-marker socket ban trips on loop construction itself.
    @pytest.mark.medium
    @pytest.mark.asyncio
    async def test_oversized_feed_body_skips_parse_and_records_failure(self):
        oversized_rss = "x" * (MAX_FEED_BODY_CHARS + 1)
        config = {
            "gdelt": {"enabled": False},
            "google_news": {"enabled": False},
            "rss_feeds": [{"name": "Huge", "url": "https://feeds.example/huge"}],
        }
        client = _mock_http_routed([("feeds.example", (200, oversized_rss))])
        with patch("src.signals.news.httpx.AsyncClient", return_value=client):
            items, stats = await _collect(config)
        assert items == []
        assert stats["feeds_ok"] == 0
        assert stats["feeds_failed"] == 1
        assert "Huge" in stats["failures"][0]["name"]


class TestBulkhead:
    """WP-42: one feed's failure never kills the sweep."""

    # Medium, not small: see TestFeedSizeCap's note on the Proactor loop.
    @pytest.mark.medium
    @pytest.mark.asyncio
    async def test_404_on_one_feed_does_not_block_others(self):
        config = {
            "gdelt": {"enabled": False},
            "google_news": {"enabled": False},
            "rss_feeds": [
                {"name": "Dead", "url": "https://feeds.example/dead"},
                {"name": "Good", "url": "https://feeds.example/good"},
            ],
        }
        client = _mock_http_routed([
            ("dead", (404, "Not Found")),
            ("good", (200, RSS_PAYLOAD)),
        ])
        with patch("src.signals.news.httpx.AsyncClient", return_value=client):
            items, stats = await _collect(config)
        assert len(items) == 2  # both items from the RSS_PAYLOAD feed
        assert stats["feeds_tried"] == 2
        assert stats["feeds_ok"] == 1
        assert stats["feeds_failed"] == 1
        assert stats["failures"] == [{"name": "feed:Dead", "detail": "HTTP 404"}]

    @pytest.mark.medium
    @pytest.mark.asyncio
    async def test_connect_error_does_not_block_others(self):
        config = {
            "gdelt": {"enabled": False},
            "google_news": {"enabled": False},
            "rss_feeds": [
                {"name": "Unreachable", "url": "https://feeds.example/unreachable"},
                {"name": "Good", "url": "https://feeds.example/good"},
            ],
        }
        client = _mock_http_routed([
            ("unreachable", httpx.ConnectError("connection refused")),
            ("good", (200, RSS_PAYLOAD)),
        ])
        with patch("src.signals.news.httpx.AsyncClient", return_value=client):
            items, stats = await _collect(config)
        assert len(items) == 2
        assert stats["feeds_ok"] == 1
        assert stats["feeds_failed"] == 1

    @pytest.mark.medium
    @pytest.mark.asyncio
    async def test_timeout_does_not_block_others(self):
        config = {
            "gdelt": {"enabled": False},
            "google_news": {"enabled": False},
            "rss_feeds": [
                {"name": "Slow", "url": "https://feeds.example/slow"},
                {"name": "Good", "url": "https://feeds.example/good"},
            ],
        }
        client = _mock_http_routed([
            ("slow", httpx.ReadTimeout("timed out")),
            ("good", (200, RSS_PAYLOAD)),
        ])
        with patch("src.signals.news.httpx.AsyncClient", return_value=client):
            items, stats = await _collect(config)
        assert len(items) == 2
        assert stats["feeds_ok"] == 1
        assert stats["feeds_failed"] == 1

    @pytest.mark.medium
    @pytest.mark.asyncio
    async def test_gdelt_and_google_news_failures_also_bulkheaded(self):
        config = {
            "gdelt": {"enabled": True, "queries": [{"q": "bad query", "timespan": "1w"}]},
            "google_news": {"enabled": True, "queries": [{"q": "bad", "hl": "en", "gl": "US"}]},
            "rss_feeds": [{"name": "Good", "url": "https://feeds.example/good"}],
        }
        client = _mock_http_routed([
            ("gdeltproject", httpx.ConnectError("refused")),
            ("news.google.com", (500, "server error")),
            ("feeds.example", (200, RSS_PAYLOAD)),
        ])
        with patch("src.signals.news.httpx.AsyncClient", return_value=client):
            items, stats = await _collect(config)
        assert len(items) == 2
        assert stats["feeds_tried"] == 3
        assert stats["feeds_ok"] == 1
        assert stats["feeds_failed"] == 2


class TestSweepSummaryLogAndPersistence:
    @pytest.mark.medium
    @pytest.mark.asyncio
    async def test_summary_persisted_to_kv_store(self, signals_config, tmp_path):
        store = LeadStore(data_dir=str(tmp_path))
        with patch("src.signals.news.httpx.AsyncClient", return_value=_mock_http()):
            await run_news_signals(signals_config, store, api_key=None)

        status = SignalsStatusStore(data_dir=str(tmp_path)).get()
        assert status["feeds_tried"] == 3
        assert status["feeds_ok"] == 3
        assert status["feeds_failed"] == 0
        assert status["leads_added"] > 0
        assert "ts" in status

    @pytest.mark.medium
    @pytest.mark.asyncio
    async def test_summary_records_per_feed_failures(self, tmp_path):
        config = {
            "enabled": True,
            "max_leads_per_run": 10,
            "gdelt": {"enabled": False},
            "google_news": {"enabled": False},
            "rss_feeds": [{"name": "Dead", "url": "https://feeds.example/dead"}],
        }
        store = LeadStore(data_dir=str(tmp_path))
        client = _mock_http_routed([("dead", (404, "Not Found"))])
        with patch("src.signals.news.httpx.AsyncClient", return_value=client):
            summary = await run_news_signals(config, store, api_key=None)

        assert summary["feeds_failed"] == 1
        assert summary["failures"] == [{"name": "feed:Dead", "detail": "HTTP 404"}]
        status = SignalsStatusStore(data_dir=str(tmp_path)).get()
        assert status["failures"] == [{"name": "feed:Dead", "detail": "HTTP 404"}]

    @pytest.mark.medium
    @pytest.mark.asyncio
    async def test_logs_one_structured_summary_line(self, signals_config, tmp_path, caplog):
        store = LeadStore(data_dir=str(tmp_path))
        with caplog.at_level("INFO", logger="src.signals.news"):
            with patch("src.signals.news.httpx.AsyncClient", return_value=_mock_http()):
                await run_news_signals(signals_config, store, api_key=None)
        summary_lines = [
            r for r in caplog.records if "News sweep complete" in r.message
        ]
        assert len(summary_lines) == 1
        line = summary_lines[0].message
        assert "feeds_tried=" in line
        assert "feeds_ok=" in line
        assert "feeds_failed=" in line
        assert "items_found=" in line
        assert "items_kept=" in line
        assert "leads_added=" in line


class TestUrlVariantSweepIdempotency:
    """WP-42 (5): reruns with URL variants of the same article add zero new
    leads once the dedupe key has been normalized (see the documented
    one-time-duplicate boundary in src.storage.leads._dedupe_key)."""

    @pytest.mark.medium
    @pytest.mark.asyncio
    async def test_utm_variant_rerun_adds_nothing_new(self, tmp_path):
        config = {
            "enabled": True,
            "max_leads_per_run": 10,
            "gdelt": {"enabled": False},
            "google_news": {"enabled": False},
            "rss_feeds": [{"name": "DCD", "url": "https://feeds.example/rss"}],
        }
        store = LeadStore(data_dir=str(tmp_path))

        plain_rss = """<rss><channel>
          <item>
            <title>Germany tightens data centre heat reuse rules</title>
            <link>https://news.example.com/germany-heat</link>
          </item>
        </channel></rss>"""
        tracked_rss = """<rss><channel>
          <item>
            <title>Germany tightens data centre heat reuse rules</title>
            <link>https://news.example.com/germany-heat?utm_source=newsletter</link>
          </item>
        </channel></rss>"""

        client1 = _mock_http_routed([("feeds.example", (200, plain_rss))])
        with patch("src.signals.news.httpx.AsyncClient", return_value=client1):
            first = await run_news_signals(config, store, api_key=None)
        assert first["leads_added"] == 1

        client2 = _mock_http_routed([("feeds.example", (200, tracked_rss))])
        with patch("src.signals.news.httpx.AsyncClient", return_value=client2):
            second = await run_news_signals(config, store, api_key=None)
        assert second["leads_added"] == 0
        assert len(store.list()) == 1


class TestRunNewsSignals:
    @pytest.mark.asyncio
    async def test_produces_leads_without_api_key(self, signals_config, tmp_path):
        """No key = no triage spend; items still become leads for humans."""
        store = LeadStore(data_dir=str(tmp_path))
        with patch("src.signals.news.httpx.AsyncClient", return_value=_mock_http()):
            summary = await run_news_signals(
                signals_config, store, api_key=None,
            )
        assert summary["leads_added"] > 0
        assert len(store.list(status="new")) == summary["leads_added"]

    @pytest.mark.asyncio
    async def test_cap_respected(self, signals_config, tmp_path):
        signals_config["max_leads_per_run"] = 1
        store = LeadStore(data_dir=str(tmp_path))
        with patch("src.signals.news.httpx.AsyncClient", return_value=_mock_http()):
            summary = await run_news_signals(signals_config, store, api_key=None)
        assert summary["leads_added"] == 1

    @pytest.mark.asyncio
    async def test_disabled_config_is_noop(self, tmp_path):
        store = LeadStore(data_dir=str(tmp_path))
        summary = await run_news_signals({"enabled": False}, store, api_key=None)
        assert summary["leads_added"] == 0

    @pytest.mark.asyncio
    async def test_triage_filters_with_api_key(self, signals_config, tmp_path):
        """With a key, Haiku triage keeps only policy-flavored items."""
        store = LeadStore(data_dir=str(tmp_path))

        triage_response = MagicMock()
        triage_response.content = [MagicMock(text=json.dumps([
            {"index": 0, "relevant": True, "policy_name": "Overskudsvarme Act",
             "jurisdiction": "Denmark"},
        ]))]
        triage_response.usage = MagicMock(input_tokens=10, output_tokens=10)

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(return_value=triage_response)

        with patch("src.signals.news.httpx.AsyncClient", return_value=_mock_http()), \
                patch("src.signals.news.anthropic.AsyncAnthropic", return_value=mock_anthropic):
            summary = await run_news_signals(
                signals_config, store, api_key="test-key",
            )
        leads = store.list(status="new")
        assert summary["leads_added"] == len(leads)
        assert all(lead.jurisdiction_guess for lead in leads)

    @pytest.mark.asyncio
    async def test_reruns_do_not_duplicate_leads(self, signals_config, tmp_path):
        store = LeadStore(data_dir=str(tmp_path))
        with patch("src.signals.news.httpx.AsyncClient", return_value=_mock_http()):
            first = await run_news_signals(signals_config, store, api_key=None)
            second = await run_news_signals(signals_config, store, api_key=None)
        assert first["leads_added"] > 0
        assert second["leads_added"] == 0
