"""Parallel scan manager - dispatches domain workers, tracks progress, broadcasts events.

Policies are persisted to data/policies.json as each domain completes, so
results survive crashes even if the full scan hasn't finished. Google Sheets
export and auditor still run at scan completion as a second layer.
"""

import asyncio
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog

from ..core.cache import URLCache
from ..core.config import ConfigLoader, ConfigurationError
from ..core.log_setup import log_audit_event
from ..core.crawler import AsyncCrawler
from ..core.extractor import HtmlExtractor
from ..core.keywords import build_keyword_matcher
from ..core.llm import ClaudeClient
from ..core.models import (
    Policy, ScanJob, ScanStatus, ScanProgress, DomainProgress,
    DomainScanStatus, ScanEvent, DEFAULT_ANALYSIS_MODEL,
)
from ..core.overrides import apply_domain_overrides
from ..core.pricing import PricingLoader
from ..notifications.mailer import notify_immediate
from ..core.scanner import DomainScanner
from ..core.verifier import Verifier
from ..storage.scan_history import ScanHistoryStore
from ..storage.store import PolicyStore
from .auditor import Auditor
from .events import EventBroadcaster

logger = logging.getLogger(__name__)


class ScanManager:
    """Manages parallel domain scanning with progress tracking."""

    def __init__(
        self,
        config: ConfigLoader,
        broadcaster: EventBroadcaster,
        api_key: Optional[str] = None,
        data_dir: str = "data",
        domain_overrides_store=None,
        scan_history_store: Optional[ScanHistoryStore] = None,
    ):
        self.config = config
        self.broadcaster = broadcaster
        self.api_key = api_key
        self.data_dir = data_dir
        # Optional (WP-8): a src.storage.domain_overrides.DomainOverridesStore.
        # None (the default every existing test and call site relies on)
        # means "no overlay" - start_scan/estimate_cost behave exactly as
        # before. deps.get_scan_manager() wires in the real store.
        self.domain_overrides_store = domain_overrides_store
        # Optional (WP-25): a src.storage.scan_history.ScanHistoryStore.
        # None (the default every existing test and call site relies on)
        # means estimate_cost() falls back to its static assumptions exactly
        # as before - no ScanHistoryStore construction, so no disk access.
        # deps.get_scan_manager() wires in the real (persisted) store.
        self.scan_history_store = scan_history_store
        self._pricing = PricingLoader()

        self._jobs: dict[str, ScanJob] = {}
        self._policies: dict[str, list[Policy]] = {}  # scan_id → policies
        self._tasks: dict[str, asyncio.Task] = {}

    def _overlay_domains(self, domains: list[dict]) -> list[dict]:
        """Drop any domain the admin overlay has disabled (WP-8/WP-9).

        A no-op when no store was wired in (see __init__), so every existing
        caller and test is unaffected.
        """
        if self.domain_overrides_store is None:
            return domains
        return apply_domain_overrides(domains, self.domain_overrides_store.get_all())

    @property
    def jobs(self) -> dict[str, ScanJob]:
        return self._jobs

    def get_policies(self, scan_id: str) -> list[Policy]:
        return self._policies.get(scan_id, [])

    def get_all_policies(self) -> list[Policy]:
        """Get all policies across all scans."""
        all_policies = []
        for policies in self._policies.values():
            all_policies.extend(policies)
        return all_policies

    async def start_scan(
        self,
        domains_group: str = "quick",
        max_concurrent: int = 5,
        skip_llm: bool = False,
        dry_run: bool = False,
        deep: bool = False,
        category: Optional[str] = None,
        tags: Optional[list[str]] = None,
        policy_type: Optional[str] = None,
        channels: Optional[list[str]] = None,
        source_params: Optional[dict] = None,
        budget_usd: Optional[float] = None,
    ) -> ScanJob:
        """Start a new parallel scan. Returns immediately with scan_id.

        ``budget_usd`` (WP-22b), when set, is a running-cost cap: once the
        LLM cost accrued so far reaches it, the scan stops launching further
        domains (in-flight ones still finish) and completes normally with
        ``job.budget_reached`` set. ``None`` (the default) means no cap -
        every existing caller is unaffected.
        """
        scan_id = str(uuid.uuid4())[:8]
        channels = channels or ["crawl"]

        # Resolve domains
        domains = self._overlay_domains(self.config.get_enabled_domains(domains_group))

        # Apply additional filters
        if category:
            domains = [d for d in domains if d.get("category") == category]
        if tags:
            domains = [
                d for d in domains
                if any(t in d.get("tags", []) for t in tags)
            ]
        if policy_type:
            domains = [
                d for d in domains
                if policy_type in d.get("policy_types", [])
            ]
        # Channel scoping - "news" has its own runner and matches no
        # domain here, so channels=["news"] naturally yields 0 domains.
        domains = [d for d in domains if self._domain_channel(d) in channels]
        if deep:
            domains = [self._with_deep_scan_defaults(d) for d in domains]
        if source_params:
            domains = [self._with_source_params(d, source_params) for d in domains]

        domains = self._structured_first(domains)

        job = ScanJob(
            scan_id=scan_id,
            status=ScanStatus.RUNNING,
            started_at=datetime.utcnow(),
            domain_group=domains_group,
            domain_count=len(domains),
            progress=ScanProgress(
                total_domains=len(domains),
                domains=[
                    DomainProgress(
                        domain_id=d["id"],
                        domain_name=d.get("name", d["id"]),
                    )
                    for d in domains
                ],
            ),
            options={
                "max_concurrent": max_concurrent,
                "skip_llm": skip_llm,
                "dry_run": dry_run,
                "deep": deep,
                "channels": channels,
                "budget_usd": budget_usd,
            },
        )

        self._jobs[scan_id] = job
        self._policies[scan_id] = []

        if dry_run:
            job.status = ScanStatus.COMPLETED
            job.completed_at = datetime.utcnow()
            return job

        # Launch background task
        task = asyncio.create_task(
            self._run_scan(scan_id, domains, max_concurrent, skip_llm, budget_usd)
        )
        self._tasks[scan_id] = task
        return job

    @staticmethod
    def _domain_channel(domain: dict) -> str:
        """Classify a domain into a scan channel based on its source_type.

        'news' is never produced here - it has its own runner outside
        scan_manager, so requesting channels=['news'] alone filters out
        every domain (0 domains, handled by the normal empty-scan path).
        """
        source_type = domain.get("source_type", "crawl")
        if source_type == "crawl":
            return "crawl"
        if source_type == "eurlex_nim":
            return "transposition"
        return "law_apis"

    @staticmethod
    def _structured_first(domains: list[dict]) -> list[dict]:
        """Order structured sources ahead of crawls, preserving config order.

        Structured sources (law APIs, transposition trackers) query an
        official index directly: they are fast, cheap, and account for most
        of what a scan finds. Crawls are the long tail. Config file order
        would otherwise scatter the APIs through the queue - a 165-domain
        US scan buried them at positions 40, 101 and 119 - so the useful
        results arrived last and Stop threw them away.
        """
        return sorted(
            domains, key=lambda d: ScanManager._domain_channel(d) == "crawl"
        )

    @staticmethod
    def _with_source_params(domain: dict, overrides: Optional[dict]) -> dict:
        """Merge per-request source params into a structured-source domain.

        Request values win over the domain's configured source_params (that
        is the point: the admin scoped this run). Crawl domains have no
        source client, so they pass through untouched.
        """
        if not overrides or domain.get("source_type", "crawl") == "crawl":
            return domain
        domain = dict(domain)
        domain["source_params"] = {**domain.get("source_params", {}), **overrides}
        return domain

    @staticmethod
    def _with_deep_scan_defaults(domain: dict) -> dict:
        """Apply CLI --deep defaults without mutating shared config."""
        domain = dict(domain)
        domain.setdefault("max_depth", 5)
        domain.setdefault("max_pages", 500)
        domain.setdefault("min_keyword_score", 2.0)
        return domain

    @staticmethod
    def _with_keyword_score_default(domain: dict, settings) -> dict:
        """Default the keyword gate to settings.analysis.min_keyword_score.

        Domains without an explicit min_keyword_score otherwise fall back to
        the stricter keywords.yaml threshold inside KeywordMatcher, silently
        ignoring the documented settings value.
        """
        domain = dict(domain)
        domain.setdefault("min_keyword_score", settings.analysis.min_keyword_score)
        return domain

    @staticmethod
    def _rejected_url_statuses(store: PolicyStore) -> dict[str, str]:
        """URL -> "rejected" for every rejected policy in ``store``.

        Feeds the scan-end Staging-sheet reconciliation pass: a policy
        rejected via the review workflow (any time, not just this scan) gets
        its Staging row flipped to "rejected" too, one-way (app -> sheet).
        """
        return {p["url"]: "rejected" for p in store.search(review_status="rejected")}

    async def _run_scan(
        self,
        scan_id: str,
        domains: list[dict],
        max_concurrent: int,
        skip_llm: bool,
        budget_usd: Optional[float] = None,
    ) -> None:
        """Run the parallel scan (background task)."""
        # Bind scan context so every log message from this task (and its
        # sub-tasks) includes the scan_id automatically.
        structlog.contextvars.bind_contextvars(scan_id=scan_id)

        job = self._jobs[scan_id]

        # Per-domain scan channel (WP-23), keyed by domain_id - fed into
        # record_domains() at scan end alongside each domain's final
        # DomainProgress.
        channel_by_domain_id = {d["id"]: self._domain_channel(d) for d in domains}

        log_audit_event(
            data_dir=self.data_dir,
            event="scan_started",
            scan_id=scan_id,
            domain_count=len(domains),
            domain_group=job.domain_group,
        )

        # Persisted scan history (WP-5) - a row per scan, next to the audit
        # trail above. Written at start, updated at completion/failure/
        # cancellation (see the three record_completion() calls below).
        history = ScanHistoryStore(data_dir=self.data_dir)

        # Estimate-vs-actual ledger (WP-24): the same estimate a cost-preview
        # call would have returned for this exact scope/channels/deep,
        # captured at the moment the scan actually started. Estimation
        # failure (e.g. a since-removed pricing entry) must never block the
        # scan itself - store NULLs and keep going.
        estimated_cost_usd = estimated_low_usd = estimated_high_usd = None
        try:
            estimate = self.estimate_cost(
                job.domain_group,
                deep=job.options.get("deep", False),
                channels=job.options.get("channels"),
            )
            estimated_cost_usd = estimate["estimated_cost_usd"]
            estimated_low_usd = estimate["estimated_cost_low_usd"]
            estimated_high_usd = estimate["estimated_cost_high_usd"]
        except (ConfigurationError, ValueError, KeyError, sqlite3.Error) as e:
            # The realistic estimate failures: scope/channel resolution
            # (ConfigurationError), an empty pricing table (ValueError),
            # and the measured-rates history read (sqlite3.Error).
            logger.warning(f"Cost estimation failed for scan {scan_id}: {e}")

        history.record_start(
            scan_id=scan_id,
            domain_group=job.domain_group,
            mode="deep" if job.options.get("deep") else "standard",
            channels=job.options.get("channels", []),
            started_at=job.started_at,
            estimated_cost_usd=estimated_cost_usd,
            estimated_low_usd=estimated_low_usd,
            estimated_high_usd=estimated_high_usd,
        )

        def _persist_domain_funnel() -> None:
            """Write this scan's per-domain funnel (WP-23), once, at scan
            end - alongside record_completion() below. Never blocks scan
            completion on a persistence failure."""
            try:
                history.record_domains(
                    scan_id=scan_id,
                    domains=[
                        (dp, channel_by_domain_id.get(dp.domain_id, "crawl"))
                        for dp in job.progress.domains
                    ],
                    completed_at=job.completed_at,
                )
            except sqlite3.Error as persist_err:
                logger.warning(
                    f"Failed to persist per-domain funnel for scan {scan_id}: "
                    f"{persist_err}"
                )

        await self.broadcaster.broadcast(ScanEvent(
            scan_id=scan_id,
            type="scan_started",
            data={"domain_count": len(domains)},
        ))

        # Shared resources
        settings = self.config.settings
        # Snapshot the url-filter config once per scan: POST /api/config/reload
        # reassigns self.config on this live instance, and a single run must
        # not crawl its early domains under one filter set and its later
        # domains under another (settings/models are already captured above).
        skip_extensions = self.config.get_skip_extensions()
        crawl_blocked_patterns = self.config.get_crawl_blocked_patterns()
        url_skip_paths = self.config.get_url_skip_paths()
        url_skip_patterns = self.config.get_url_skip_patterns()
        cache = URLCache.load(
            cache_path=Path(self.data_dir) / "url_cache.json"
        )
        extractor = HtmlExtractor(settings.config_dir)
        keyword_matcher = build_keyword_matcher(self.config, self.data_dir)
        verifier = Verifier()

        # Per-domain persistence - saves policies to data/policies.json as each
        # domain completes, so results survive crashes. Uses atomic writes and
        # deduplication by URL.
        store = PolicyStore(data_dir=self.data_dir)

        # Incremental Google Sheets export - write policies as each domain
        # completes, not just at scan end.  This means if the user quits
        # mid-scan, all policies found so far are already in the Sheet.
        sheets_client = None
        sheets_exported_urls: set[str] = set()
        output_cfg = self.config.settings.output
        sheet_name = output_cfg.staging_sheet_name
        sheets_status = job.sheets_export  # mutable reference

        if output_cfg.spreadsheet_id and output_cfg.google_credentials_b64:
            sheets_status.configured = True
            try:
                from ..output.sheets import SheetsClient
                sheets_client = SheetsClient(
                    credentials_b64=output_cfg.google_credentials_b64,
                    spreadsheet_id=output_cfg.spreadsheet_id,
                )
                sheets_client.connect()
                sheets_exported_urls = sheets_client.get_existing_urls(sheet_name)
                sheets_status.connected = True
                sheets_status.status = "connected"
                logger.info(
                    f"Google Sheets connected - {len(sheets_exported_urls)} "
                    f"existing policies in '{sheet_name}'"
                )
            except Exception as e:
                sheets_status.status = "failed"
                sheets_status.error = str(e)
                logger.warning(
                    "Google Sheets connection failed: %s. "
                    "Policies will be saved to data/policies.json only. "
                    "Check GOOGLE_CREDENTIALS and SPREADSHEET_ID in your .env file.",
                    e,
                )
                sheets_client = None
        else:
            sheets_status.status = "not_configured"
            logger.info(
                "Google Sheets export not configured. "
                "Policies will be saved to data/policies.json. "
                "To enable: set GOOGLE_CREDENTIALS and SPREADSHEET_ID in .env"
            )

        llm_client = None
        if not skip_llm and self.api_key:
            llm_client = ClaudeClient(
                api_key=self.api_key,
                analysis_model=settings.analysis.analysis_model,
                screening_model=settings.analysis.screening_model,
            )

        semaphore = asyncio.Semaphore(max_concurrent)

        async def scan_domain(domain: dict) -> list[Policy]:
            async with semaphore:
                # Mid-scan budget stop (WP-22b): an earlier domain already
                # pushed running cost to the cap - don't launch this one.
                # Domains already past the semaphore (in-flight) when the
                # cap was reached still finish normally.
                if budget_usd is not None and job.budget_reached:
                    for dp in job.progress.domains:
                        if dp.domain_id == domain["id"]:
                            dp.status = DomainScanStatus.SKIPPED
                            dp.error_message = "Skipped: scan budget reached"
                            break
                    job.progress.completed_domains += 1
                    return []

                domain = self._with_keyword_score_default(domain, settings)
                # Bind domain context for log correlation
                structlog.contextvars.bind_contextvars(
                    domain_id=domain["id"],
                )

                crawler = AsyncCrawler(
                    max_depth=domain.get("max_depth", settings.crawl.max_depth),
                    max_pages=domain.get("max_pages", settings.crawl.max_pages_per_domain),
                    delay_seconds=settings.crawl.delay_seconds,
                    timeout_seconds=settings.crawl.timeout_seconds,
                    user_agent=settings.crawl.user_agent,
                    max_retries=settings.crawl.max_retries,
                    skip_extensions=skip_extensions,
                    crawl_blocked_patterns=crawl_blocked_patterns
                        + domain.get("blocked_path_patterns", []),
                    url_skip_paths=url_skip_paths,
                    url_skip_patterns=url_skip_patterns,
                )

                scanner = DomainScanner(
                    domain=domain,
                    crawler=crawler,
                    extractor=extractor,
                    keyword_matcher=keyword_matcher,
                    llm_client=llm_client,
                    cache=cache,
                    verifier=verifier,
                    scan_id=scan_id,
                    skip_llm=skip_llm,
                    on_event=self.broadcaster.broadcast,
                    screening_min_confidence=settings.analysis.screening_min_confidence,
                    scope_setting=settings.analysis.data_center_required,
                )

                try:
                    policies = await scanner.scan()

                    # Update job progress
                    for dp in job.progress.domains:
                        if dp.domain_id == domain["id"]:
                            dp.status = scanner.progress.status
                            dp.pages_crawled = scanner.progress.pages_crawled
                            dp.pages_filtered = scanner.progress.pages_filtered
                            dp.filtered_short_content = scanner.progress.filtered_short_content
                            dp.filtered_excluded = scanner.progress.filtered_excluded
                            dp.filtered_keywords = scanner.progress.filtered_keywords
                            dp.filtered_screening = scanner.progress.filtered_screening
                            dp.filtered_out_of_scope = (
                                scanner.progress.filtered_out_of_scope)
                            dp.near_misses = scanner.progress.near_misses
                            dp.keywords_matched = scanner.progress.keywords_matched
                            dp.llm_skipped = scanner.progress.llm_skipped
                            dp.policies_found = scanner.progress.policies_found
                            dp.errors = scanner.progress.errors
                            dp.error_message = scanner.progress.error_message
                            break

                    job.progress.completed_domains += 1

                    # Mid-scan budget stop (WP-22b): cheap running-cost
                    # check after each domain completes. Once it lands at
                    # or past the cap, no further domain is launched (see
                    # the check at the top of this function) - domains
                    # already in flight when this trips still finish.
                    if (
                        budget_usd is not None
                        and llm_client is not None
                        and not job.budget_reached
                    ):
                        llm_client.update_cost_estimate()
                        if llm_client.cost.total_usd >= budget_usd:
                            job.budget_reached = True
                            log_audit_event(
                                data_dir=self.data_dir,
                                event="scan_budget_reached",
                                scan_id=scan_id,
                                budget_usd=budget_usd,
                                cost_usd=llm_client.cost.total_usd,
                            )
                            notify_immediate(
                                "ops_alerts",
                                f"PolicyPulse: scan {scan_id} stopped after reaching its budget",
                                f"The scan for {job.domain_group} stopped early after "
                                f"reaching its budget of ${budget_usd:.2f} "
                                f"(spent ${llm_client.cost.total_usd:.2f}).\n\n"
                                "Open the admin page to act on these.",
                                data_dir=self.data_dir,
                            )

                    # Persist policies immediately so they survive crashes.
                    # PolicyStore.add_policies deduplicates by URL and saves
                    # atomically to data/policies.json.
                    if policies:
                        try:
                            store.add_policies(policies)
                        except Exception as persist_err:
                            logger.error(
                                f"Failed to persist {len(policies)} policies "
                                f"from {domain['id']}: {persist_err}"
                            )
                        # Update in-memory list and job count incrementally
                        self._policies[scan_id].extend(policies)
                        job.policy_count += len(policies)

                        # Export to Google Sheets immediately - don't wait for
                        # scan completion.  If the user quits mid-scan, these
                        # policies are already safe in the Sheet.
                        if sheets_client:
                            new_for_sheets = [
                                p for p in policies
                                if p.url not in sheets_exported_urls
                            ]
                            if new_for_sheets:
                                try:
                                    count = sheets_client.append_policies(
                                        new_for_sheets, sheet_name,
                                    )
                                    for p in new_for_sheets:
                                        sheets_exported_urls.add(p.url)
                                    sheets_status.exported_count += count
                                    logger.info(
                                        f"Exported {count} policies from "
                                        f"{domain['id']} to Google Sheets"
                                    )
                                except Exception as sheets_err:
                                    sheets_status.failed_count += len(new_for_sheets)
                                    sheets_status.error = str(sheets_err)
                                    logger.warning(
                                        f"Sheets export failed for {domain['id']}: "
                                        f"{sheets_err}"
                                    )

                        # Audit: record each policy discovery
                        for p in policies:
                            log_audit_event(
                                data_dir=self.data_dir,
                                event="policy_found",
                                scan_id=scan_id,
                                domain_id=domain["id"],
                                policy_name=p.policy_name,
                                url=p.url,
                                relevance=p.relevance_score,
                            )

                    return policies

                except Exception as e:
                    logger.error(f"Domain {domain['id']} failed: {e}")
                    for dp in job.progress.domains:
                        if dp.domain_id == domain["id"]:
                            dp.status = DomainScanStatus.FAILED
                            dp.error_message = str(e)
                            break
                    job.progress.completed_domains += 1
                    return []

                finally:
                    await crawler.close()

        try:
            # Run all domains in parallel (bounded by semaphore).
            # Policies are saved per-domain inside scan_domain() so they
            # survive crashes. We still await all tasks to completion.
            tasks = [scan_domain(d) for d in domains]
            await asyncio.gather(*tasks, return_exceptions=True)

            # All policies were collected in self._policies[scan_id] above
            all_policies = self._policies.get(scan_id, [])

            # Reconcile policy_count in case any race condition
            job.policy_count = len(all_policies)

            # Update LLM cost
            if llm_client:
                llm_client.update_cost_estimate()
                job.cost = llm_client.cost

            # Post-scan verification summary
            flagged = [p for p in all_policies if p.verification_flags]
            await self.broadcaster.broadcast(ScanEvent(
                scan_id=scan_id,
                type="verification_complete",
                data={
                    "flagged": len(flagged),
                    "passed": len(all_policies) - len(flagged),
                },
            ))

            # Post-scan auditor (1 LLM call)
            if not skip_llm and self.api_key and all_policies:
                try:
                    auditor = Auditor(api_key=self.api_key)
                    advisory = await auditor.generate_advisory(
                        scan_summary={
                            "scan_id": scan_id,
                            "domains_scanned": len(domains),
                            "policies_found": len(all_policies),
                            "cost": job.cost.model_dump() if job.cost else {},
                        },
                        domain_results=[
                            dp.model_dump() for dp in job.progress.domains
                        ],
                        flagged_issues=[
                            {
                                "url": p.url,
                                "policy_name": p.policy_name,
                                "flags": [f.value for f in p.verification_flags],
                            }
                            for p in flagged
                        ],
                    )
                    job.audit_advisory = advisory
                    # Fold the auditor's own call into the scan's recorded
                    # actuals (WP-22) - it never fires if generate_advisory
                    # failed (last_input_tokens stays None), so nothing is
                    # added in that case.
                    if auditor.last_input_tokens is not None and job.cost:
                        auditor_price = self._pricing.pricing_for(auditor.model)
                        auditor_cost = auditor_price.cost_usd(
                            auditor.last_input_tokens, auditor.last_output_tokens,
                        )
                        job.cost.input_tokens += auditor.last_input_tokens
                        job.cost.output_tokens += auditor.last_output_tokens
                        job.cost.total_usd = round(job.cost.total_usd + auditor_cost, 4)
                    await self.broadcaster.broadcast(ScanEvent(
                        scan_id=scan_id,
                        type="audit_complete",
                        data={"advisory": advisory or "No advisory generated"},
                    ))
                    await auditor.close()
                except Exception as e:
                    logger.warning(f"Auditor failed: {e}")

            # Save cache
            cache.save()

            # Final Google Sheets reconciliation - catch any policies that
            # slipped through the per-domain export (e.g. if Sheets was
            # temporarily unavailable for one domain).  When incremental
            # export is working, this usually finds nothing new.
            if sheets_client and all_policies:
                try:
                    missed = [
                        p for p in all_policies
                        if p.url not in sheets_exported_urls
                    ]
                    if missed:
                        count = sheets_client.append_policies(missed, sheet_name)
                        sheets_status.exported_count += count
                        logger.info(
                            f"Final Sheets reconciliation: exported {count} "
                            f"missed policies"
                        )
                except Exception as e:
                    sheets_status.error = str(e)
                    logger.warning(f"Final Sheets export failed: {e}")
            elif not sheets_client and all_policies:
                # Sheets wasn't configured or connection failed at start -
                # try once more as a fallback
                if output_cfg.spreadsheet_id and output_cfg.google_credentials_b64:
                    try:
                        from ..output.sheets import SheetsClient
                        fallback = SheetsClient(
                            credentials_b64=output_cfg.google_credentials_b64,
                            spreadsheet_id=output_cfg.spreadsheet_id,
                        )
                        fallback.connect()
                        existing = fallback.get_existing_urls(sheet_name)
                        new_policies = [
                            p for p in all_policies if p.url not in existing
                        ]
                        if new_policies:
                            count = fallback.append_policies(
                                new_policies, sheet_name,
                            )
                            sheets_status.connected = True
                            sheets_status.status = "connected"
                            sheets_status.exported_count += count
                            logger.info(
                                f"Fallback Sheets export: {count} policies"
                            )
                    except Exception as e:
                        sheets_status.error = str(e)
                        logger.warning(
                            f"Fallback Sheets export failed: {e}"
                        )

            # Rejected-status reconciliation - a policy rejected via the
            # review workflow (any time, not just this scan) gets its
            # Staging row's Review Status flipped to "rejected" too.
            # One-way, app -> sheet, never the reverse.
            if sheets_client:
                try:
                    rejected = self._rejected_url_statuses(store)
                    if rejected:
                        updated = sheets_client.update_review_statuses(rejected, sheet_name)
                        logger.info(
                            f"Sheet reconciliation: marked {updated} rejected "
                            f"policies on the Staging sheet"
                        )
                except Exception as e:
                    logger.warning(f"Rejected-status sheet reconciliation failed: {e}")

            job.status = ScanStatus.COMPLETED
            job.completed_at = datetime.utcnow()

            log_audit_event(
                data_dir=self.data_dir,
                event="scan_completed",
                scan_id=scan_id,
                domain_group=job.domain_group,
                domains_scanned=len(domains),
                policies_found=len(all_policies),
                cost_usd=job.cost.total_usd if job.cost else 0,
                duration_s=(
                    (job.completed_at - job.started_at).total_seconds()
                    if job.started_at else None
                ),
            )
            _persist_domain_funnel()
            history.record_completion(
                scan_id=scan_id,
                # job.status stays the enum-constrained ScanStatus.COMPLETED
                # above - the scans table's status column is free text, so
                # the budget-reached fact is distinguishable there without
                # needing a new enum member (WP-22b).
                status="completed_budget_reached" if job.budget_reached else "completed",
                completed_at=job.completed_at,
                domains_scanned=len(domains),
                policies_found=len(all_policies),
                cost_usd=job.cost.total_usd if job.cost else 0,
                input_tokens=job.cost.input_tokens if job.cost else None,
                output_tokens=job.cost.output_tokens if job.cost else None,
            )

            await self.broadcaster.broadcast(ScanEvent(
                scan_id=scan_id,
                type="scan_complete",
                data={
                    "total_policies": len(all_policies),
                    "cost_usd": job.cost.total_usd if job.cost else 0,
                    "sheets_export": sheets_status.model_dump(),
                },
            ))

        except asyncio.CancelledError:
            job.status = ScanStatus.CANCELLED
            job.completed_at = datetime.utcnow()
            _persist_domain_funnel()
            history.record_completion(
                scan_id=scan_id,
                status="cancelled",
                completed_at=job.completed_at,
                domains_scanned=len(domains),
                policies_found=len(self._policies.get(scan_id, [])),
                cost_usd=job.cost.total_usd if job.cost else 0,
                input_tokens=job.cost.input_tokens if job.cost else None,
                output_tokens=job.cost.output_tokens if job.cost else None,
            )
        except Exception as e:
            logger.error(f"Scan {scan_id} failed: {e}")
            job.status = ScanStatus.FAILED
            job.completed_at = datetime.utcnow()
            _persist_domain_funnel()
            history.record_completion(
                scan_id=scan_id,
                status="failed",
                completed_at=job.completed_at,
                domains_scanned=len(domains),
                policies_found=len(self._policies.get(scan_id, [])),
                cost_usd=job.cost.total_usd if job.cost else 0,
                input_tokens=job.cost.input_tokens if job.cost else None,
                output_tokens=job.cost.output_tokens if job.cost else None,
            )
            notify_immediate(
                "ops_alerts",
                f"PolicyPulse: scan {scan_id} failed",
                f"The scan for {job.domain_group} failed: {e}\n\n"
                "Open the admin page to act on these.",
                data_dir=self.data_dir,
            )
            await self.broadcaster.broadcast(ScanEvent(
                scan_id=scan_id,
                type="error",
                data={"error": str(e)},
            ))
        finally:
            if llm_client:
                await llm_client.close()

    async def stop_scan(self, scan_id: str) -> bool:
        """Cancel a running scan."""
        task = self._tasks.get(scan_id)
        if task and not task.done():
            task.cancel()
            job = self._jobs.get(scan_id)
            if job:
                job.status = ScanStatus.CANCELLED
                job.completed_at = datetime.utcnow()
            return True
        return False

    # Deep scans lower min_keyword_score from the standard baseline (3.0,
    # settings.analysis.min_keyword_score's default per config/settings.yaml)
    # to 2.0 (see _with_deep_scan_defaults), which lets noticeably more pages
    # pass the keyword gate. Assumption: that roughly doubles the pass rate
    # used for the standard estimate below.
    DEEP_KEYWORD_PASS_RATE = 0.20

    # Range-estimate multipliers (WP-26) for a channel whose rates are still
    # assumed (no calibration data): wide, since there is nothing to bound
    # the guess with yet. Measured channels instead use their rate's
    # 25th/75th percentile spread - see _channel_cost_range below.
    ASSUMED_LOW_MULTIPLIER = 0.4
    ASSUMED_HIGH_MULTIPLIER = 2.5
    # A measured channel's low/high band is widened to at least this
    # fraction of the typical cost either side, even if the IQR spread is
    # narrower - a handful of scans can look falsely tight.
    MEASURED_BAND_FLOOR = 0.2

    def _measured_rates(self) -> dict:
        """Calibrated crawl/structured rates (WP-25), or an all-None shape
        when no ``scan_history_store`` is wired in - every existing
        ScanManager() construction site, which then falls back to the
        static assumptions exactly as before WP-25 introduced this."""
        if self.scan_history_store is None:
            return {
                "crawl": {
                    "keyword_rate": None, "screening_pass_rate": None,
                    "pages_per_domain": None, "scans": 0,
                    "spread": {
                        "keyword_rate": {"p25": None, "p75": None},
                        "screening_pass_rate": {"p25": None, "p75": None},
                        "pages_per_domain": {"p25": None, "p75": None},
                    },
                },
                "structured": {
                    "items_per_source": None, "screening_pass_rate": None,
                    "scans": 0,
                    "spread": {
                        "items_per_source": {"p25": None, "p75": None},
                        "screening_pass_rate": {"p25": None, "p75": None},
                    },
                },
            }
        return self.scan_history_store.measured_rates()

    def _channel_cost_range(
        self, *, is_measured: bool, typical_cost: float,
        low_screening_calls: int, high_screening_calls: int,
        low_analysis_calls: int, high_analysis_calls: int,
        screening_price, analysis_price, screening_input: int, screening_output: int,
        analysis_input: int, analysis_output: int,
    ) -> tuple[float, float]:
        """(low_cost, high_cost) for one channel (WP-26).

        Assumed channels get a wide fixed multiplier band around the typical
        cost. Measured channels recompute the cost at each rate's 25th/75th
        percentile, then widen to at least +/-MEASURED_BAND_FLOOR of typical
        if that band turns out narrower - a few calibration scans can look
        falsely precise. Either way, low <= typical <= high always holds.
        """
        if not is_measured:
            return (
                typical_cost * self.ASSUMED_LOW_MULTIPLIER,
                typical_cost * self.ASSUMED_HIGH_MULTIPLIER,
            )

        low_cost = (
            low_screening_calls * screening_price.cost_usd(screening_input, screening_output)
            + low_analysis_calls * analysis_price.cost_usd(analysis_input, analysis_output)
        )
        high_cost = (
            high_screening_calls * screening_price.cost_usd(screening_input, screening_output)
            + high_analysis_calls * analysis_price.cost_usd(analysis_input, analysis_output)
        )
        floor_low = typical_cost * (1 - self.MEASURED_BAND_FLOOR)
        floor_high = typical_cost * (1 + self.MEASURED_BAND_FLOOR)
        return min(low_cost, floor_low), max(high_cost, floor_high)

    def estimate_cost(
        self, domains_group: str, deep: bool = False, channels: Optional[list[str]] = None,
    ) -> dict:
        """Estimate API costs for a scan.

        Raises ConfigurationError (via get_enabled_domains) for an unknown
        group/region/domain scope - callers (the API route) turn that into a
        400, mirroring domains.py's list_domains.

        ``channels`` (optional) narrows the domain set to those matching the
        selected scan channels, exactly like start_scan does - so a schedule
        scoped to only law databases isn't costed as if it also crawled every
        website. ``None`` (the default) counts every domain, preserving the
        behavior of callers that don't pass it (e.g. cost_projection).

        Screening/analysis models are resolved from
        ``settings.analysis.screening_model``/``analysis_model`` - the same
        place a real scan reads them from (see ``_run_scan``'s ClaudeClient
        construction) - so an admin's cost-level choice
        (``CostSettingsStore.apply_to_config``, applied once at startup and
        on every settings-route update) changes the estimate too (WP-20).

        Domains are split by channel (WP-21): crawl domains use the page/
        keyword-gate model below; structured domains (law_apis,
        transposition) skip both - real scans skip the keyword gate for them
        entirely (src/core/scanner.py) - and instead assume a flat number of
        items per source, all of which reach screening.

        Calibration (WP-25): once ``scan_history_store`` has enough completed
        scans, its measured keyword-gate/screening-pass/pages-per-domain/
        items-per-source rates replace the static assumptions below, per
        channel and per metric - each number's provenance (measured vs
        assumed) is recorded in ``assumptions``. ``deep=True`` always keeps
        the static deep-scan assumptions for the crawl channel: scan_domains
        history doesn't distinguish deep from standard runs, and deep crawls
        far more pages per domain, so blending the two would be misleading.
        Structured channels are unaffected by ``deep`` (it only changes
        crawl behavior - see ``_with_deep_scan_defaults``), so their measured
        rates still apply.

        Range estimates (WP-26): every number returned also has a low/high
        counterpart (``estimated_cost_low_usd``/``estimated_cost_high_usd``
        overall, ``cost_low_usd``/``cost_high_usd`` per channel) - see
        ``_channel_cost_range``.
        """
        domains = self._overlay_domains(self.config.get_enabled_domains(domains_group))
        if channels is not None:
            domains = [d for d in domains if self._domain_channel(d) in channels]
        settings = self.config.settings

        max_pages_per_domain = settings.crawl.max_pages_per_domain
        static_keyword_pass_rate = 0.10
        if deep:
            # Reuse _with_deep_scan_defaults as the single source of truth for
            # the deep-scan max_pages value instead of duplicating it here.
            max_pages_per_domain = self._with_deep_scan_defaults({})["max_pages"]
            static_keyword_pass_rate = self.DEEP_KEYWORD_PASS_RATE

        static_screening_pass_rate = 0.50
        est = self._pricing.estimator
        static_structured_items_per_source = est.get("structured_items_per_source", 40)
        screening_input = est.get("screening_input", 2000)
        screening_output = est.get("screening_output", 50)
        analysis_input = est.get("analysis_input", 20000)
        analysis_output = est.get("analysis_output", 1000)
        auditor_input = est.get("auditor_input", 5000)
        auditor_output = est.get("auditor_output", 2000)

        screening_price = self._pricing.pricing_for(settings.analysis.screening_model)
        analysis_price = self._pricing.pricing_for(settings.analysis.analysis_model)

        measured = self._measured_rates()
        crawl_measured = measured["crawl"]
        structured_measured = measured["structured"]

        channel_domains: dict[str, list[dict]] = {}
        for d in domains:
            channel_domains.setdefault(self._domain_channel(d), []).append(d)

        channels_out: dict[str, dict] = {}
        total_pages = 0
        total_keyword_passes = 0
        total_screening_calls = 0
        total_analysis_calls = 0
        total_typical_cost = 0.0
        total_low_cost = 0.0
        total_high_cost = 0.0
        assumptions: list[str] = []

        for channel_name, group in channel_domains.items():
            count = len(group)
            is_crawl = channel_name == "crawl"

            if is_crawl:
                # deep=True bypasses measured crawl rates entirely - see the
                # docstring's Calibration paragraph.
                use_measured_pages = not deep and crawl_measured["pages_per_domain"] is not None
                if use_measured_pages:
                    est_pages_per_domain = crawl_measured["pages_per_domain"]
                    assumptions.append(
                        f"crawl: {est_pages_per_domain:.0f} pages/domain measured "
                        f"across {crawl_measured['scans']} scans"
                    )
                else:
                    est_pages_per_domain = max_pages_per_domain // 2
                    assumptions.append(
                        f"crawl: {max_pages_per_domain} max pages/domain configured, "
                        f"{est_pages_per_domain} assumed crawled (half of max)"
                    )
                items_or_pages = count * est_pages_per_domain

                keyword_measured = not deep and crawl_measured["keyword_rate"] is not None
                if keyword_measured:
                    keyword_rate = crawl_measured["keyword_rate"]
                    assumptions.append(
                        f"crawl: keyword gate: {keyword_rate:.1%} measured across "
                        f"{crawl_measured['scans']} scans"
                    )
                else:
                    keyword_rate = static_keyword_pass_rate
                    assumptions.append(
                        f"crawl: {keyword_rate:.0%} of crawled pages assumed to pass "
                        "the keyword gate (assumed - no scan history yet)"
                    )
                keyword_passes = int(items_or_pages * keyword_rate)
                screening_calls = keyword_passes

                screening_measured = (
                    not deep and crawl_measured["screening_pass_rate"] is not None
                )
                if screening_measured:
                    channel_screening_pass_rate = crawl_measured["screening_pass_rate"]
                    assumptions.append(
                        f"crawl: screening pass rate: {channel_screening_pass_rate:.1%} "
                        f"measured across {crawl_measured['scans']} scans"
                    )
                else:
                    channel_screening_pass_rate = static_screening_pass_rate
                    assumptions.append(
                        f"crawl: {channel_screening_pass_rate:.0%} of screened items "
                        "assumed to reach analysis (assumed - no scan history yet)"
                    )
                analysis_calls = int(screening_calls * channel_screening_pass_rate)

                is_range_measured = keyword_measured and screening_measured
                spread = crawl_measured["spread"]
                if is_range_measured:
                    kw_low, kw_high = spread["keyword_rate"]["p25"], spread["keyword_rate"]["p75"]
                    scr_low = spread["screening_pass_rate"]["p25"]
                    scr_high = spread["screening_pass_rate"]["p75"]
                    low_screening_calls = int(items_or_pages * kw_low)
                    high_screening_calls = int(items_or_pages * kw_high)
                    low_analysis_calls = int(low_screening_calls * scr_low)
                    high_analysis_calls = int(high_screening_calls * scr_high)
                else:
                    low_screening_calls = high_screening_calls = 0
                    low_analysis_calls = high_analysis_calls = 0
            else:
                # Structured sources skip the crawl page model and the
                # keyword gate entirely (scanner.py sets is_relevant=True
                # unconditionally for them) - every assumed item reaches
                # screening. Every structured channel (law_apis,
                # transposition) shares one calibration bucket.
                items_measured = structured_measured["items_per_source"] is not None
                if items_measured:
                    items_per_source = structured_measured["items_per_source"]
                    assumptions.append(
                        f"structured sources: {items_per_source:.0f} items/source "
                        f"measured across {structured_measured['scans']} scans"
                    )
                else:
                    items_per_source = static_structured_items_per_source
                    assumptions.append(
                        f"structured sources: {items_per_source} items each "
                        "(assumed - no scan history yet)"
                    )
                items_or_pages = count * items_per_source
                keyword_passes = items_or_pages
                screening_calls = items_or_pages

                screening_measured = structured_measured["screening_pass_rate"] is not None
                if screening_measured:
                    channel_screening_pass_rate = structured_measured["screening_pass_rate"]
                    assumptions.append(
                        f"structured sources: screening pass rate: "
                        f"{channel_screening_pass_rate:.1%} measured across "
                        f"{structured_measured['scans']} scans"
                    )
                else:
                    channel_screening_pass_rate = static_screening_pass_rate
                    assumptions.append(
                        f"structured sources: {channel_screening_pass_rate:.0%} of "
                        "screened items assumed to reach analysis "
                        "(assumed - no scan history yet)"
                    )
                analysis_calls = int(screening_calls * channel_screening_pass_rate)

                is_range_measured = items_measured and screening_measured
                spread = structured_measured["spread"]
                if is_range_measured:
                    scr_low = spread["screening_pass_rate"]["p25"]
                    scr_high = spread["screening_pass_rate"]["p75"]
                    # Only the *rate* percentiles feed the band (the item
                    # count itself stays fixed at its typical value across
                    # low/typical/high - see the docstring).
                    low_screening_calls = high_screening_calls = screening_calls
                    low_analysis_calls = int(screening_calls * scr_low)
                    high_analysis_calls = int(screening_calls * scr_high)
                else:
                    low_screening_calls = high_screening_calls = 0
                    low_analysis_calls = high_analysis_calls = 0

            typical_cost = (
                screening_calls * screening_price.cost_usd(screening_input, screening_output)
                + analysis_calls * analysis_price.cost_usd(analysis_input, analysis_output)
            )
            low_cost, high_cost = self._channel_cost_range(
                is_measured=is_range_measured, typical_cost=typical_cost,
                low_screening_calls=low_screening_calls, high_screening_calls=high_screening_calls,
                low_analysis_calls=low_analysis_calls, high_analysis_calls=high_analysis_calls,
                screening_price=screening_price, analysis_price=analysis_price,
                screening_input=screening_input, screening_output=screening_output,
                analysis_input=analysis_input, analysis_output=analysis_output,
            )

            channels_out[channel_name] = {
                "domain_count": count,
                "estimated_items_or_pages": items_or_pages,
                "screening_calls": screening_calls,
                "analysis_calls": analysis_calls,
                "cost_usd": round(typical_cost, 2),
                "cost_low_usd": round(low_cost, 2),
                "cost_high_usd": round(high_cost, 2),
            }

            total_pages += items_or_pages
            total_keyword_passes += keyword_passes
            total_screening_calls += screening_calls
            total_analysis_calls += analysis_calls
            total_typical_cost += typical_cost
            total_low_cost += low_cost
            total_high_cost += high_cost

        # Auditor: a single flat post-scan call, unconditional in this
        # estimate (real scans skip it only when skip_llm/no api key/no
        # policies found - see _run_scan) - priced at the model the
        # auditor actually calls (Auditor's own default, not the
        # cost-level-selected analysis model, since Auditor doesn't
        # currently read the cost level). Added flat to typical/low/high
        # alike - it doesn't vary with calibration.
        auditor_price = self._pricing.pricing_for(DEFAULT_ANALYSIS_MODEL)
        auditor_raw_cost = auditor_price.cost_usd(auditor_input, auditor_output)

        return {
            "domain_count": len(domains),
            "estimated_pages": total_pages,
            "estimated_keyword_passes": total_keyword_passes,
            "estimated_screening_calls": total_screening_calls,
            "estimated_analysis_calls": total_analysis_calls,
            "estimated_cost_usd": round(total_typical_cost + auditor_raw_cost, 2),
            "estimated_cost_low_usd": round(total_low_cost + auditor_raw_cost, 2),
            "estimated_cost_high_usd": round(total_high_cost + auditor_raw_cost, 2),
            "channels": channels_out,
            "auditor_cost_usd": round(auditor_raw_cost, 2),
            "assumptions": assumptions,
        }
