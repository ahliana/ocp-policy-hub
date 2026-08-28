"""Single-domain scanning pipeline — the unit of parallelism.

Pipeline stages:
  crawl → extract → url_filter → keywords → cache_check
  → haiku_screen → sonnet_analyze → verify
"""

import logging
from typing import Optional, Callable, Awaitable
from urllib.parse import urlparse

from .cache import URLCache, compute_content_hash
from .scope import DEFAULT_SETTING as DEFAULT_SCOPE
from .scope import OUT_OF_SCOPE, scope_verdict
from .crawler import AsyncCrawler
from .extractor import HtmlExtractor
from .keywords import KeywordMatcher
from .llm import ClaudeClient, LLMAuthError
from .models import (
    CrawlResult, Policy, DomainProgress, DomainScanStatus,
    ScanEvent,
)
from .verifier import Verifier

logger = logging.getLogger(__name__)


class DomainScanner:
    """Scans a single domain through the full pipeline."""

    def __init__(
        self,
        domain: dict,
        crawler: AsyncCrawler,
        extractor: HtmlExtractor,
        keyword_matcher: KeywordMatcher,
        llm_client: Optional[ClaudeClient],
        cache: URLCache,
        verifier: Verifier,
        scan_id: str = "",
        skip_llm: bool = False,
        on_event: Optional[Callable[[ScanEvent], Awaitable[None]]] = None,
        screening_min_confidence: int = 5,
        scope_setting: str = DEFAULT_SCOPE,
    ):
        self.domain = domain
        self.crawler = crawler
        self.extractor = extractor
        self.keyword_matcher = keyword_matcher
        self.llm_client = llm_client
        self.cache = cache
        self.verifier = verifier
        self.scan_id = scan_id
        self.skip_llm = skip_llm
        self.on_event = on_event
        self.screening_min_confidence = screening_min_confidence
        self.scope_setting = scope_setting

        self.domain_id = domain.get("id", "")
        self.progress = DomainProgress(
            domain_id=self.domain_id,
            domain_name=domain.get("name", ""),
        )

    async def _emit(self, event_type: str, data: dict = None) -> None:
        """Emit a scan event."""
        if self.on_event:
            event = ScanEvent(
                scan_id=self.scan_id,
                type=event_type,
                domain_id=self.domain_id,
                data=data or {},
            )
            await self.on_event(event)

    async def scan(self) -> list[Policy]:
        """Run the full pipeline for this domain. Returns discovered policies."""
        self.progress.status = DomainScanStatus.RUNNING
        await self._emit("domain_started", {"domain_name": self.domain.get("name", "")})

        policies: list[Policy] = []

        try:
            # Stage 1: Acquire documents — structured source or crawl
            source_type = self.domain.get("source_type", "crawl")
            if source_type != "crawl":
                from ..sources import get_source
                source = get_source(source_type)
                logger.info(
                    "Domain %s uses structured source '%s'",
                    self.domain_id, source_type,
                )
                crawl_results = await source.fetch(self.domain)
                for r in crawl_results:
                    r.domain_id = self.domain_id
            else:
                crawl_results = await self.crawler.crawl_domain(
                    base_url=self.domain["base_url"],
                    start_paths=self.domain.get("start_paths", ["/"]),
                    domain_id=self.domain_id,
                    allowed_path_patterns=self.domain.get("allowed_path_patterns"),
                    blocked_path_patterns=self.domain.get("blocked_path_patterns"),
                    max_depth_override=self.domain.get("max_depth"),
                    max_pages_override=self.domain.get("max_pages"),
                    requires_playwright=self.domain.get("requires_playwright", False),
                )

            self.progress.pages_crawled = len(crawl_results)

            # Process each successful page through the pipeline
            processed_urls = {r.url for r in crawl_results}
            for result in crawl_results:
                if not result.is_success or not result.content:
                    if result.is_blocked:
                        self.progress.errors += 1
                    continue

                await self._emit("page_fetched", {
                    "url": result.url,
                    "status": result.status.value,
                    "response_ms": result.response_time_ms,
                })

                page_policies = await self._process_page_isolated(result)
                for policy in page_policies:
                    policies.append(policy)
                    self.progress.policies_found += 1
                    await self._emit("policy_found", {
                        "url": policy.url,
                        "policy_name": policy.policy_name,
                        "relevance": policy.relevance_score,
                    })

            # Citation follow-up: analysis extracts referenced_urls from
            # every policy; laws cite laws, so same-site references we did
            # not crawl are fetched and processed too.
            followup_policies = await self._follow_referenced_urls(
                policies, processed_urls,
            )
            for policy in followup_policies:
                policies.append(policy)
                self.progress.policies_found += 1
                await self._emit("policy_found", {
                    "url": policy.url,
                    "policy_name": policy.policy_name,
                    "relevance": policy.relevance_score,
                })

            # Verify all policies for this domain
            domain_regions = self.domain.get("region", [])
            self.verifier.verify_batch(policies, domain_regions)

            self.progress.status = DomainScanStatus.COMPLETED

        except Exception as e:
            logger.error(f"Domain scan failed for {self.domain_id}: {e}")
            self.progress.status = DomainScanStatus.FAILED
            self.progress.error_message = str(e)
            self.progress.errors += 1
            await self._emit("error", {
                "domain_id": self.domain_id,
                "error": str(e),
            })

        if self.progress.filtered_out_of_scope:
            logger.info(
                "%s: %d pages dropped by the data-centre scope rule (setting=%s)",
                self.domain_id, self.progress.filtered_out_of_scope,
                self.scope_setting,
            )

        await self._emit("domain_complete", {
            "pages": self.progress.pages_crawled,
            "policies": self.progress.policies_found,
            "errors": self.progress.errors,
            "out_of_scope": self.progress.filtered_out_of_scope,
        })

        return policies

    MAX_REFERENCED_URLS = 20  # follow-up fetch budget per domain

    async def _process_page_isolated(self, result: CrawlResult) -> list[Policy]:
        """Run _process_page with per-page error isolation.

        One failed page (rate-limit exhaustion, parse error) must not lose
        the rest of the domain; an auth failure affects every page and
        still aborts.
        """
        try:
            return await self._process_page(result)
        except LLMAuthError:
            raise
        except Exception as e:
            logger.error(
                "Page processing failed for %s: %s — continuing with "
                "remaining pages", result.url, e,
            )
            self.progress.errors += 1
            return []

    async def _follow_referenced_urls(
        self, policies: list[Policy], processed_urls: set[str],
    ) -> list[Policy]:
        """Fetch and process same-site URLs cited by discovered policies."""
        base_netloc = urlparse(self.domain["base_url"]).netloc
        queue: list[str] = []
        for policy in policies:
            for ref in policy.referenced_urls:
                if ref in processed_urls or ref in queue:
                    continue
                parsed = urlparse(ref)
                if parsed.scheme not in ("http", "https"):
                    continue
                if not AsyncCrawler._same_site(parsed.netloc, base_netloc):
                    logger.info(
                        "Referenced URL is cross-site, not auto-followed: %s",
                        ref,
                    )
                    continue
                queue.append(ref)

        if not queue:
            return []
        if len(queue) > self.MAX_REFERENCED_URLS:
            logger.info(
                "Capping referenced-URL follow-up at %d of %d candidates",
                self.MAX_REFERENCED_URLS, len(queue),
            )
            queue = queue[:self.MAX_REFERENCED_URLS]

        found: list[Policy] = []
        for url in queue:
            processed_urls.add(url)
            result = await self.crawler.fetch_url(url)
            result.domain_id = self.domain_id
            if not result.is_success or not result.content:
                continue
            logger.info("Following referenced policy URL: %s", url)
            found.extend(await self._process_page_isolated(result))
        return found

    async def _process_page(self, result: CrawlResult) -> list[Policy]:
        """Process a single page through extract → keywords → LLM → verify."""

        # Stage 2: Extract content
        extracted = self.extractor.extract(result.content, result.url)

        # Structured sources (LegiScan, GovInfo, DIP, ...) return one-line
        # bills already matched by the source's own targeted query. The web-
        # page gates below — the <50-word short filter and the keyword score
        # gate — are tuned for full HTML pages and would wrongly drop these
        # thin-but-relevant hits, so skip them and let the LLM screen/analyze.
        is_structured = self.domain.get("source_type", "crawl") != "crawl"

        if not is_structured:
            if not extracted.text or extracted.word_count < 50:
                self.progress.pages_filtered += 1
                self.progress.filtered_short_content += 1
                return []

            # Stage 3: Keyword matching
            kw_result = self.keyword_matcher.match(extracted.text)
            if kw_result.is_excluded:
                self.progress.pages_filtered += 1
                self.progress.filtered_excluded += 1
                return []

            min_score = self.domain.get("min_keyword_score")
            is_relevant = self.keyword_matcher.is_relevant(
                kw_result, url=result.url, min_score_override=min_score,
            )
        else:
            kw_result = self.keyword_matcher.match(extracted.text or "")
            is_relevant = True

        if not is_relevant:
            self.progress.pages_filtered += 1
            self.progress.filtered_keywords += 1
            # Near misses at INFO: these are the pages to inspect when
            # tuning thresholds or keyword lists.
            if self.keyword_matcher.check_near_miss(kw_result, url=result.url, min_score_override=min_score):
                kw_result.is_near_miss = True
                self.progress.near_misses += 1
                logger.info(
                    "Near miss at keyword gate: %s (score=%.1f+%.1f url bonus, "
                    "matched=%s)",
                    result.url, kw_result.score, kw_result.url_bonus,
                    [m.term for m in kw_result.matches],
                )
            else:
                logger.info(
                    "Dropped at keyword gate: %s (score=%.1f, matches=%d)",
                    result.url, kw_result.score, len(kw_result.matches),
                )
            return []

        self.progress.keywords_matched += 1
        await self._emit("keyword_match", {
            "url": result.url,
            "score": kw_result.score + kw_result.url_bonus,
            "categories": kw_result.categories_matched,
        })

        # Stage 4: Cache check
        content_hash = compute_content_hash(extracted.text)
        cached = self.cache.get(result.url, content_hash)
        if cached:
            if not cached.is_relevant:
                return []
            # Still return a policy stub from cache? For now skip re-analysis.
            logger.debug(f"Cache hit: {result.url}")
            return []  # Cache hit means we already have this policy

        # Stage 4b: scope gate. Both lanes have rejoined by here, so
        # this is the earliest point that sees every document: the
        # keyword gate above is skipped entirely by structured sources.
        # Runs before any model call, so an out-of-scope document costs
        # nothing.
        verdict = scope_verdict(extracted.text or "", self.scope_setting)
        if verdict == OUT_OF_SCOPE:
            self.progress.pages_filtered += 1
            self.progress.filtered_out_of_scope += 1
            logger.info(
                "Dropped at scope gate (no data centre reference): %s",
                result.url,
            )
            self.cache.set(
                result.url, is_relevant=False,
                relevance_score=0, content_hash=content_hash,
            )
            return []

        # Stage 5: LLM analysis (skip if disabled)
        if self.skip_llm or not self.llm_client:
            # Cache as "needs LLM" but don't analyze
            self.cache.set(
                result.url, is_relevant=True,
                relevance_score=0, content_hash=content_hash,
            )
            self.progress.llm_skipped += 1
            logger.info(
                "Keyword match at %s but LLM analysis %s — "
                "page cached for future re-scan with LLM enabled",
                result.url,
                "disabled" if self.skip_llm else "unavailable (no API key)",
            )
            return []

        # Stage 5a: Haiku screening
        screening = await self.llm_client.screen_relevance(
            extracted.text, result.url,
            anchor_terms=[m.term for m in kw_result.matches],
        )
        if not screening.relevant:
            if screening.confidence >= self.screening_min_confidence:
                self.progress.pages_filtered += 1
                self.progress.filtered_screening += 1
                logger.info(
                    "Dropped at screening gate: %s (confidence=%d)",
                    result.url, screening.confidence,
                )
                self.cache.set(
                    result.url, is_relevant=False,
                    relevance_score=0, content_hash=content_hash,
                )
                return []
            # Borderline rejection: the screener is not confident enough to
            # make the final call — escalate to full analysis instead.
            logger.info(
                "Borderline screening rejection for %s (confidence=%d < %d) "
                "— escalating to analysis",
                result.url, screening.confidence, self.screening_min_confidence,
            )

        # Stage 5b: Sonnet analysis
        analysis = await self.llm_client.analyze_policy(
            extracted.text, result.url, extracted.language,
        )

        # Cache the result
        self.cache.set(
            result.url,
            is_relevant=analysis.is_relevant,
            relevance_score=analysis.relevance_score,
            content_hash=content_hash,
            policy_type=analysis.policy_type,
        )

        # Stage 6: Convert to Policy records (index pages can hold several)
        policies = self.llm_client.to_policies(
            analysis, result.url,
            language=extracted.language or "en",
            domain_id=self.domain_id,
            scan_id=self.scan_id,
        )
        # A source-declared stage (bill status, open consultation) is
        # authoritative over the analysis model's inference.
        if result.lifecycle_stage:
            for policy in policies:
                policy.lifecycle_stage = result.lifecycle_stage
        return policies
