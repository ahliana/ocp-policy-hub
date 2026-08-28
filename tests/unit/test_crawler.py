"""Tests for AsyncCrawler — URL filtering, link extraction, diagnosis, and Playwright."""

import re
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core.crawler import AsyncCrawler, _diagnose_response


# --- _diagnose_response ---

class TestDiagnoseResponse:
    def test_cloudflare_server(self):
        result = _diagnose_response(403, {"server": "cloudflare"}, "")
        assert "Cloudflare" in result

    def test_akamai_server(self):
        result = _diagnose_response(403, {"server": "AkamaiGHost"}, "")
        assert "Akamai" in result

    def test_body_pattern_access_denied(self):
        result = _diagnose_response(403, {}, "Access Denied - you are blocked")
        assert "Access Denied" in result

    def test_body_pattern_rate_limit(self):
        result = _diagnose_response(429, {}, "rate limit exceeded")
        assert "rate limited" in result

    def test_plain_status_code(self):
        result = _diagnose_response(500, {}, "")
        assert result == "HTTP 500"


# --- AsyncCrawler._should_skip_url ---

class TestShouldSkipUrl:
    def test_skips_matching_path(self):
        crawler = AsyncCrawler(url_skip_paths=["/login", "/admin"])
        assert crawler._should_skip_url("https://a.gov/login")
        assert crawler._should_skip_url("https://a.gov/admin/panel")

    def test_does_not_skip_normal_url(self):
        crawler = AsyncCrawler(url_skip_paths=["/login"])
        assert not crawler._should_skip_url("https://a.gov/policy")

    def test_skips_matching_pattern(self):
        crawler = AsyncCrawler(url_skip_patterns=[re.compile(r"/archive/\d+")])
        assert crawler._should_skip_url("https://a.gov/archive/2024/doc")

    def test_does_not_skip_non_matching_pattern(self):
        crawler = AsyncCrawler(url_skip_patterns=[re.compile(r"/archive/\d+")])
        assert not crawler._should_skip_url("https://a.gov/policy/heat")


# --- AsyncCrawler._extract_links ---

class TestExtractLinks:
    def test_extracts_same_domain_links(self):
        crawler = AsyncCrawler()
        html = """
        <html><body>
            <a href="/page2">Page 2</a>
            <a href="https://example.gov/page3">Page 3</a>
            <a href="https://other.com/external">External</a>
        </body></html>
        """
        links = crawler._extract_links(
            html, "https://example.gov/page1", "https://example.gov",
        )
        assert "https://example.gov/page2" in links
        assert "https://example.gov/page3" in links
        assert not any("other.com" in link for link in links)

    def test_skips_file_extensions(self):
        crawler = AsyncCrawler()
        html = """
        <html><body>
            <a href="/doc.pdf">PDF</a>
            <a href="/image.jpg">Image</a>
            <a href="/archive.zip">Zip</a>
            <a href="/page">Page</a>
        </body></html>
        """
        links = crawler._extract_links(
            html, "https://example.gov/", "https://example.gov",
        )
        # PDFs carry the actual statutes and must be followed
        assert any(link.endswith(".pdf") for link in links)
        assert not any(link.endswith(".jpg") for link in links)
        assert not any(link.endswith(".zip") for link in links)
        assert "https://example.gov/page" in links

    def test_nav_links_are_followed(self):
        """Statute tables of contents live inside <nav>/<aside> on
        legislation sites; link discovery must not strip them."""
        crawler = AsyncCrawler()
        html = """
        <html><body>
            <nav><a href="/law/section-11">Section 11</a></nav>
            <aside role="navigation"><a href="/law/section-12">Section 12</a></aside>
            <p><a href="/law/full-text">Full text</a></p>
        </body></html>
        """
        links = crawler._extract_links(
            html, "https://example.gov/law", "https://example.gov",
        )
        assert "https://example.gov/law/section-11" in links
        assert "https://example.gov/law/section-12" in links
        assert "https://example.gov/law/full-text" in links

    def test_subdomain_links_followed(self):
        """Gov sites put document stores on sibling subdomains."""
        crawler = AsyncCrawler()
        html = """
        <html><body>
            <a href="https://efiling.energy.ca.gov/doc/1">Filing</a>
            <a href="https://energy.ca.gov/page">Apex</a>
            <a href="https://othersite.com/x">External</a>
        </body></html>
        """
        links = crawler._extract_links(
            html, "https://www.energy.ca.gov/", "https://www.energy.ca.gov",
        )
        assert any("efiling.energy.ca.gov" in link for link in links)
        assert any(link.startswith("https://energy.ca.gov/page") for link in links)
        assert not any("othersite.com" in link for link in links)

    def test_url_priority_prefers_law_paths(self):
        crawler = AsyncCrawler()
        assert crawler._url_priority("https://a.gov/laws/energy-heat-act") > \
            crawler._url_priority("https://a.gov/press/photo-gallery")
        assert crawler._url_priority("https://a.gov/statute.pdf") > \
            crawler._url_priority("https://a.gov/about-us")

    @pytest.mark.asyncio
    async def test_crawl_fetches_promising_links_first(self):
        """With a tight page budget, law-like URLs must win over press."""
        crawler = AsyncCrawler(delay_seconds=0, max_retries=1, max_pages=2, max_depth=2)
        seed_html = """
        <html><body>
            <a href="/press/gallery">Gallery</a>
            <a href="/laws/heat-act">Heat Act</a>
        </body></html>
        """
        fetched = []

        async def fake_fetch(client, url):
            from src.core.models import CrawlResult, PageStatus
            if url.endswith("sitemap.xml"):
                return CrawlResult(url=url, status=PageStatus.NOT_FOUND)
            fetched.append(url)
            return CrawlResult(
                url=url, status=PageStatus.SUCCESS,
                content=seed_html if len(fetched) == 1 else "<html><body>content text here</body></html>",
                content_type="text/html", content_length=10,
            )

        with patch.object(crawler, "_fetch_with_retry", side_effect=fake_fetch), \
                patch.object(crawler, "_ensure_client", AsyncMock(return_value=MagicMock())):
            await crawler.crawl_domain(
                base_url="https://example.gov",
                start_paths=["/"],
                domain_id="d1",
            )
        assert len(fetched) == 2
        assert fetched[1].endswith("/laws/heat-act")

    @pytest.mark.asyncio
    async def test_sitemap_seeds_deep_urls(self):
        """sitemap.xml enumerates deep law URLs the BFS would never reach."""
        from src.core.models import CrawlResult, PageStatus

        crawler = AsyncCrawler(delay_seconds=0, max_retries=1, max_pages=5, max_depth=2)
        fetched = []

        async def fake_fetch(client, url):
            fetched.append(url)
            if url.endswith("sitemap.xml"):
                return CrawlResult(
                    url=url, status=PageStatus.SUCCESS, content_type="application/xml",
                    content=(
                        "<urlset><url><loc>https://example.gov/laws/deep-heat-statute"
                        "</loc></url><url><loc>https://example.gov/press/x</loc></url>"
                        "</urlset>"
                    ),
                    content_length=10,
                )
            return CrawlResult(
                url=url, status=PageStatus.SUCCESS, content="<html><body>page</body></html>",
                content_type="text/html", content_length=10,
            )

        with patch.object(crawler, "_fetch_with_retry", side_effect=fake_fetch), \
                patch.object(crawler, "_ensure_client", AsyncMock(return_value=MagicMock())):
            await crawler.crawl_domain(
                base_url="https://example.gov", start_paths=["/"], domain_id="d1",
            )
        assert any(u.endswith("/laws/deep-heat-statute") for u in fetched)

    @pytest.mark.asyncio
    async def test_playwright_fallback_on_empty_page(self):
        """A JS shell served over httpx must trigger a Playwright retry."""
        from src.core.models import CrawlResult, PageStatus

        crawler = AsyncCrawler(delay_seconds=0, max_retries=1, max_pages=1, max_depth=1)
        shell = CrawlResult(
            url="https://example.gov/", status=PageStatus.SUCCESS,
            content='<html><body><div id="root"></div></body></html>',
            content_type="text/html", content_length=48,
        )
        rendered = CrawlResult(
            url="https://example.gov/", status=PageStatus.SUCCESS,
            content="<html><body>" + ("Real policy content about heat. " * 30) + "</body></html>",
            content_type="text/html", content_length=1000, used_playwright=True,
        )

        with patch.object(crawler, "_fetch_with_retry", AsyncMock(return_value=shell)), \
                patch.object(crawler, "_fetch_playwright", AsyncMock(return_value=rendered)), \
                patch.object(crawler, "_ensure_client", AsyncMock(return_value=MagicMock())):
            results = await crawler.crawl_domain(
                base_url="https://example.gov", start_paths=["/"], domain_id="d1",
            )
        assert results[0].used_playwright
        assert "Real policy content" in results[0].content

    def test_blocked_patterns_filtered(self):
        crawler = AsyncCrawler()
        html = """
        <html><body>
            <a href="/policy/good">Good</a>
            <a href="/archive/old">Old</a>
        </body></html>
        """
        links = crawler._extract_links(
            html, "https://example.gov/", "https://example.gov",
            blocked_patterns=["/archive/*"],
        )
        assert "https://example.gov/policy/good" in links
        assert not any("archive" in link for link in links)

    def test_allowed_patterns_restrict(self):
        crawler = AsyncCrawler()
        html = """
        <html><body>
            <a href="/policy/heat">Policy</a>
            <a href="/about">About</a>
        </body></html>
        """
        links = crawler._extract_links(
            html, "https://example.gov/", "https://example.gov",
            allowed_patterns=["/policy/*"],
        )
        assert "https://example.gov/policy/heat" in links
        assert not any("about" in link for link in links)

    def test_keeps_nav_links(self):
        """Nav links are kept: statute ToCs render as navigation, and the
        priority queue deprioritizes generic nav noise instead."""
        crawler = AsyncCrawler()
        html = """
        <html><body>
            <nav><a href="/home">Home</a></nav>
            <div><a href="/content">Content</a></div>
        </body></html>
        """
        links = crawler._extract_links(
            html, "https://example.gov/", "https://example.gov",
        )
        assert "https://example.gov/content" in links
        assert "https://example.gov/home" in links

    def test_deduplicates_links(self):
        crawler = AsyncCrawler()
        html = """
        <html><body>
            <a href="/page">Link 1</a>
            <a href="/page">Link 2</a>
        </body></html>
        """
        links = crawler._extract_links(
            html, "https://example.gov/", "https://example.gov",
        )
        assert links.count("https://example.gov/page") == 1

    def test_normalizes_urls_strips_fragment(self):
        crawler = AsyncCrawler()
        html = '<html><body><a href="/page#section">Link</a></body></html>'
        links = crawler._extract_links(
            html, "https://example.gov/", "https://example.gov",
        )
        # Fragments should be stripped during normalization
        assert any("/page" in link and "#" not in link for link in links)

    def test_skips_non_http_schemes(self):
        crawler = AsyncCrawler()
        html = """
        <html><body>
            <a href="mailto:test@test.com">Email</a>
            <a href="javascript:void(0)">JS</a>
            <a href="/valid">Valid</a>
        </body></html>
        """
        links = crawler._extract_links(
            html, "https://example.gov/", "https://example.gov",
        )
        assert not any("mailto" in link for link in links)
        assert not any("javascript" in link for link in links)


# --- AsyncCrawler fetch and crawl (async tests using mocked httpx) ---

def _mock_response(status_code=200, text="", headers=None, content=None):
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    resp.headers = headers or {}
    resp.content = content if content is not None else text.encode()
    return resp


class TestAsyncCrawlerFetch:
    @pytest.mark.asyncio
    async def test_fetch_success(self):
        crawler = AsyncCrawler(delay_seconds=0, max_retries=1)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(
            200, "<html><body>Hello</body></html>",
            headers={"content-type": "text/html"},
        ))
        result = await crawler._fetch_with_retry(mock_client, "https://example.gov/page")
        assert result.status.value == "success"
        assert "Hello" in result.content

    @pytest.mark.asyncio
    async def test_fetch_pdf_extracts_text(self):
        from tests.unit.test_pdf import _minimal_pdf

        crawler = AsyncCrawler(delay_seconds=0, max_retries=1)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(
            200, headers={"content-type": "application/pdf"},
            content=_minimal_pdf("District Heating Act 2026"),
        ))
        result = await crawler._fetch_with_retry(
            mock_client, "https://example.gov/statute.pdf",
        )
        assert result.status.value == "success"
        assert "District Heating Act 2026" in result.content
        assert result.content_type == "application/pdf"

    @pytest.mark.asyncio
    async def test_fetch_corrupt_pdf_is_error_not_garbage(self):
        crawler = AsyncCrawler(delay_seconds=0, max_retries=1)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(
            200, headers={"content-type": "application/pdf"},
            content=b"\x00\x01 not a pdf",
        ))
        result = await crawler._fetch_with_retry(
            mock_client, "https://example.gov/broken.pdf",
        )
        assert result.status.value != "success"
        assert result.content is None

    @pytest.mark.asyncio
    async def test_fetch_404(self):
        crawler = AsyncCrawler(delay_seconds=0, max_retries=1)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(404, ""))
        result = await crawler._fetch_with_retry(mock_client, "https://example.gov/missing")
        assert result.status.value == "not_found"

    @pytest.mark.asyncio
    async def test_fetch_403(self):
        crawler = AsyncCrawler(delay_seconds=0, max_retries=1)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(403, "Access denied"))
        result = await crawler._fetch_with_retry(mock_client, "https://example.gov/denied")
        assert result.status.value == "access_denied"

    @pytest.mark.asyncio
    async def test_fetch_429_rate_limited(self):
        crawler = AsyncCrawler(delay_seconds=0, max_retries=1)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(429, "rate limit"))
        result = await crawler._fetch_with_retry(mock_client, "https://example.gov/api")
        assert result.status.value == "rate_limited"

    @pytest.mark.asyncio
    async def test_fetch_timeout_retries(self):
        crawler = AsyncCrawler(delay_seconds=0, max_retries=2)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        result = await crawler._fetch_with_retry(mock_client, "https://example.gov/slow")
        assert result.status.value == "timeout"
        assert mock_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_fetch_500_returns_unknown_error(self):
        crawler = AsyncCrawler(delay_seconds=0, max_retries=1)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(500, "Server Error"))
        result = await crawler._fetch_with_retry(mock_client, "https://example.gov/error")
        assert result.status.value == "unknown_error"


# ---------------------------------------------------------------------------
# Playwright fetch (mocked — no real browser)
# ---------------------------------------------------------------------------

def _mock_pw_page(status_code=200, content="<html><body>rendered</body></html>"):
    """Create a mock Playwright page with goto/content/close."""
    mock_response = MagicMock()
    mock_response.status = status_code

    page = AsyncMock()
    page.goto = AsyncMock(return_value=mock_response)
    page.content = AsyncMock(return_value=content)
    page.close = AsyncMock()
    return page


def _mock_pw_browser(page=None):
    """Create a mock Playwright browser that returns a given page."""
    browser = AsyncMock()
    browser.new_page = AsyncMock(return_value=page or _mock_pw_page())
    browser.close = AsyncMock()
    return browser


class TestPlaywrightFetch:
    """Tests for _fetch_playwright with mocked Playwright."""

    @pytest.mark.asyncio
    async def test_playwright_success(self):
        crawler = AsyncCrawler(delay_seconds=0)
        page = _mock_pw_page(200, "<html><body>SPA content</body></html>")
        crawler._pw_browser = _mock_pw_browser(page)

        result = await crawler._fetch_playwright("https://spa.gov/page")

        assert result.status.value == "success"
        assert result.used_playwright is True
        assert "SPA content" in result.content
        assert result.content_length > 0
        page.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_playwright_404(self):
        crawler = AsyncCrawler(delay_seconds=0)
        page = _mock_pw_page(404)
        crawler._pw_browser = _mock_pw_browser(page)

        result = await crawler._fetch_playwright("https://spa.gov/missing")

        assert result.status.value == "not_found"
        assert "404" in result.error_message

    @pytest.mark.asyncio
    async def test_playwright_403(self):
        crawler = AsyncCrawler(delay_seconds=0)
        page = _mock_pw_page(403)
        crawler._pw_browser = _mock_pw_browser(page)

        result = await crawler._fetch_playwright("https://spa.gov/denied")

        assert result.status.value == "access_denied"

    @pytest.mark.asyncio
    async def test_playwright_429(self):
        crawler = AsyncCrawler(delay_seconds=0)
        page = _mock_pw_page(429)
        crawler._pw_browser = _mock_pw_browser(page)

        result = await crawler._fetch_playwright("https://spa.gov/limited")

        assert result.status.value == "rate_limited"

    @pytest.mark.asyncio
    async def test_playwright_500(self):
        crawler = AsyncCrawler(delay_seconds=0)
        page = _mock_pw_page(500)
        crawler._pw_browser = _mock_pw_browser(page)

        result = await crawler._fetch_playwright("https://spa.gov/error")

        assert result.status.value == "unknown_error"
        assert "500" in result.error_message

    @pytest.mark.asyncio
    async def test_playwright_no_response(self):
        crawler = AsyncCrawler(delay_seconds=0)
        page = AsyncMock()
        page.goto = AsyncMock(return_value=None)
        page.close = AsyncMock()
        crawler._pw_browser = _mock_pw_browser(page)

        result = await crawler._fetch_playwright("https://spa.gov/empty")

        assert result.status.value == "unknown_error"
        assert "no response" in result.error_message

    @pytest.mark.asyncio
    async def test_playwright_timeout(self):
        crawler = AsyncCrawler(delay_seconds=0, timeout_seconds=5)
        page = AsyncMock()
        page.goto = AsyncMock(side_effect=Exception("Timeout 5000ms exceeded"))
        page.close = AsyncMock()
        crawler._pw_browser = _mock_pw_browser(page)

        result = await crawler._fetch_playwright("https://spa.gov/slow")

        assert result.status.value == "timeout"
        assert "Playwright" in result.error_message

    @pytest.mark.asyncio
    async def test_playwright_generic_error(self):
        crawler = AsyncCrawler(delay_seconds=0)
        page = AsyncMock()
        page.goto = AsyncMock(side_effect=Exception("net::ERR_CONNECTION_REFUSED"))
        page.close = AsyncMock()
        crawler._pw_browser = _mock_pw_browser(page)

        result = await crawler._fetch_playwright("https://spa.gov/down")

        assert result.status.value == "unknown_error"
        assert "ERR_CONNECTION_REFUSED" in result.error_message

    @pytest.mark.asyncio
    async def test_playwright_page_closed_on_error(self):
        crawler = AsyncCrawler(delay_seconds=0)
        page = AsyncMock()
        page.goto = AsyncMock(side_effect=Exception("crash"))
        page.close = AsyncMock()
        crawler._pw_browser = _mock_pw_browser(page)

        await crawler._fetch_playwright("https://spa.gov/crash")

        page.close.assert_awaited_once()
        assert page.close.await_count == 1

    @pytest.mark.asyncio
    async def test_playwright_sets_user_agent(self):
        crawler = AsyncCrawler(delay_seconds=0, user_agent="TestBot/1.0")
        page = _mock_pw_page()
        browser = _mock_pw_browser(page)
        crawler._pw_browser = browser

        await crawler._fetch_playwright("https://spa.gov/page")

        browser.new_page.assert_awaited_once_with(user_agent="TestBot/1.0")
        assert browser.new_page.call_args.kwargs["user_agent"] == "TestBot/1.0"


class TestEnsurePlaywright:
    """Tests for _ensure_playwright browser lifecycle."""

    @pytest.mark.asyncio
    async def test_reuses_existing_browser(self):
        crawler = AsyncCrawler()
        mock_browser = MagicMock()
        crawler._pw_browser = mock_browser

        result = await crawler._ensure_playwright()

        assert result is mock_browser

    @pytest.mark.asyncio
    async def test_raises_on_missing_playwright(self):
        crawler = AsyncCrawler()
        with patch.dict("sys.modules", {"playwright": None, "playwright.async_api": None}):
            with pytest.raises(RuntimeError, match="Playwright is required"):
                # Force fresh import check
                crawler._pw_browser = None
                await crawler._ensure_playwright()


class TestCrawlDomainPlaywright:
    """Tests for crawl_domain with requires_playwright=True."""

    @pytest.mark.asyncio
    async def test_crawl_domain_uses_playwright_when_required(self):
        crawler = AsyncCrawler(delay_seconds=0, max_depth=0, max_pages=1)
        page = _mock_pw_page(200, "<html><body>JS content</body></html>")
        crawler._pw_browser = _mock_pw_browser(page)

        with patch.object(crawler, "_sitemap_urls", AsyncMock(return_value=[])):
            results = await crawler.crawl_domain(
                base_url="https://spa.gov",
                start_paths=["/app"],
                domain_id="test_spa",
                requires_playwright=True,
            )

        assert len(results) == 1
        assert results[0].used_playwright is True
        assert results[0].status.value == "success"
        assert results[0].domain_id == "test_spa"

    @pytest.mark.asyncio
    async def test_crawl_domain_uses_httpx_by_default(self):
        crawler = AsyncCrawler(delay_seconds=0, max_depth=0, max_pages=1)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(
            200, "<html>static</html>", headers={"content-type": "text/html"},
        ))
        crawler._client = mock_client

        results = await crawler.crawl_domain(
            base_url="https://static.gov",
            start_paths=["/page"],
            requires_playwright=False,
        )

        assert len(results) == 1
        assert results[0].used_playwright is False

    @pytest.mark.asyncio
    async def test_playwright_domain_pages_never_fetched_over_httpx(self):
        """On Playwright domains httpx serves only sitemap seeding (and
        PDFs); page fetches must go through the browser."""
        crawler = AsyncCrawler(delay_seconds=0, max_depth=0, max_pages=1)
        page = _mock_pw_page(200, "<html>js</html>")
        crawler._pw_browser = _mock_pw_browser(page)
        httpx_fetch = AsyncMock()

        with patch.object(crawler, "_sitemap_urls", AsyncMock(return_value=[])), \
                patch.object(crawler, "_fetch_with_retry", httpx_fetch):
            results = await crawler.crawl_domain(
                base_url="https://spa.gov",
                start_paths=["/app"],
                requires_playwright=True,
            )

        httpx_fetch.assert_not_awaited()
        assert results[0].used_playwright is True


class TestClosePlaywright:
    """Tests for close() cleaning up Playwright resources."""

    @pytest.mark.asyncio
    async def test_close_cleans_up_playwright(self):
        crawler = AsyncCrawler()
        mock_browser = AsyncMock()
        mock_pw = AsyncMock()
        crawler._pw_browser = mock_browser
        crawler._playwright = mock_pw

        await crawler.close()

        mock_browser.close.assert_awaited_once()
        mock_pw.stop.assert_awaited_once()
        assert crawler._pw_browser is None
        assert crawler._playwright is None

    @pytest.mark.asyncio
    async def test_close_handles_no_playwright(self):
        crawler = AsyncCrawler()
        # No Playwright resources — should not raise
        await crawler.close()

        assert crawler._client is None
        assert crawler._pw_browser is None
        assert crawler._playwright is None

    @pytest.mark.asyncio
    async def test_close_handles_both_httpx_and_playwright(self):
        crawler = AsyncCrawler()
        mock_client = AsyncMock()
        mock_browser = AsyncMock()
        mock_pw = AsyncMock()
        crawler._client = mock_client
        crawler._pw_browser = mock_browser
        crawler._playwright = mock_pw

        await crawler.close()

        mock_client.aclose.assert_awaited_once()
        mock_browser.close.assert_awaited_once()
        mock_pw.stop.assert_awaited_once()
        assert crawler._client is None
        assert crawler._pw_browser is None
        assert crawler._playwright is None


class TestPlaywrightDomainConfig:
    """Domain YAML config must correctly pass requires_playwright to scanner."""

    def test_a_js_rendered_domain_still_carries_the_flag(self):
        """The plumbing this class exists to protect. va_legislature is a
        JavaScript-rendered site that is still crawled, so the flag must
        still reach the scanner."""
        from src.core.config import ConfigLoader
        config = ConfigLoader(config_dir="config")
        config.load()
        domains = config.get_enabled_domains("all")
        legislature = [d for d in domains if d["id"] == "va_legislature"]
        assert len(legislature) == 1
        assert legislature[0]["requires_playwright"] is True

    def test_the_per_bill_virginia_domains_are_superseded_and_off(self):
        """Replaced 2026-08-28 by the va_lis_2026 session-file source.

        Each of these rendered one React bill page with a headless browser,
        and HB 323 never reached the database anyway. The session file
        carries every bill in the session, so leaving these enabled would
        fetch the same bills twice, the slow way. They stay in the YAML as
        a record of what was watched by hand; this test is what stops one
        being switched back on without noticing the duplication.
        """
        from src.core.config import ConfigLoader
        config = ConfigLoader(config_dir="config")
        config.load()
        enabled = {d["id"] for d in config.get_enabled_domains("all")}
        for domain_id in ("us_va_hb323_2026", "us_va_hb906_2026", "us_va_hb824_2026"):
            assert domain_id not in enabled, (
                f"{domain_id} is enabled again. If that is deliberate, the "
                "va_lis_2026 source will already be delivering the same bill."
            )
        assert "va_lis_2026" in enabled, (
            "The per-bill domains are off, so the session-file source must be "
            "on or Virginia is not covered at all."
        )

    def test_virginia_yaml_has_five_domains(self):
        import yaml
        with open("config/domains/us/virginia.yaml") as f:
            data = yaml.safe_load(f)
        assert len(data["domains"]) == 5

    def test_scanner_passes_requires_playwright(self):
        """DomainScanner.scan() must pass requires_playwright from domain config."""
        from pathlib import Path
        source = (Path(__file__).resolve().parents[2] / "src" / "core" / "scanner.py")
        text = source.read_text(encoding="utf-8")
        assert "requires_playwright=self.domain.get(\"requires_playwright\"" in text


class TestContainerBrowserLaunch:
    """Chromium in containers with default 64MB /dev/shm wedges silently;
    the launch args must opt out of /dev/shm (observed live: scan a4cd43f6
    stalled 10/18 domains at zero pages on the production container)."""

    @pytest.mark.asyncio
    async def test_launch_disables_dev_shm(self, monkeypatch):
        from src.core.crawler import AsyncCrawler

        captured = {}

        class FakeChromium:
            async def launch(self, **kwargs):
                captured.update(kwargs)
                return _mock_pw_browser()

        class FakePlaywright:
            chromium = FakeChromium()

            async def start(self):
                return self

        import playwright.async_api as pa
        monkeypatch.setattr(pa, "async_playwright", lambda: FakePlaywright())

        crawler = AsyncCrawler.__new__(AsyncCrawler)
        crawler._pw_browser = None
        crawler._playwright = None
        await crawler._ensure_playwright()
        assert "--disable-dev-shm-usage" in captured.get("args", [])
