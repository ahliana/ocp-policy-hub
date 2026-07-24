"""Parallel scan manager — dispatches domain workers, tracks progress, broadcasts events.

Policies are persisted to data/policies.json as each domain completes, so
results survive crashes even if the full scan hasn't finished. Google Sheets
export and auditor still run at scan completion as a second layer.
"""

import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog

from ..core.cache import URLCache
from ..core.config import ConfigLoader
from ..core.log_setup import log_audit_event
from ..core.crawler import AsyncCrawler
from ..core.extractor import HtmlExtractor
from ..core.keywords import KeywordMatcher
from ..core.llm import ClaudeClient
from ..core.models import (
    Policy, ScanJob, ScanStatus, ScanProgress, DomainProgress,
    DomainScanStatus, ScanEvent,
)
from ..core.scanner import DomainScanner
from ..core.verifier import Verifier
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
    ):
        self.config = config
        self.broadcaster = broadcaster
        self.api_key = api_key
        self.data_dir = data_dir

        self._jobs: dict[str, ScanJob] = {}
        self._policies: dict[str, list[Policy]] = {}  # scan_id → policies
        self._tasks: dict[str, asyncio.Task] = {}

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
    ) -> ScanJob:
        """Start a new parallel scan. Returns immediately with scan_id."""
        scan_id = str(uuid.uuid4())[:8]
        channels = channels or ["crawl"]

        # Resolve domains
        domains = self.config.get_enabled_domains(domains_group)

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
        # Channel scoping — "news" has its own runner and matches no
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
            self._run_scan(scan_id, domains, max_concurrent, skip_llm)
        )
        self._tasks[scan_id] = task
        return job

    @staticmethod
    def _domain_channel(domain: dict) -> str:
        """Classify a domain into a scan channel based on its source_type.

        'news' is never produced here — it has its own runner outside
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
        would otherwise scatter the APIs through the queue — a 165-domain
        US scan buried them at positions 40, 101 and 119 — so the useful
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
    ) -> None:
        """Run the parallel scan (background task)."""
        # Bind scan context so every log message from this task (and its
        # sub-tasks) includes the scan_id automatically.
        structlog.contextvars.bind_contextvars(scan_id=scan_id)

        job = self._jobs[scan_id]

        log_audit_event(
            data_dir=self.data_dir,
            event="scan_started",
            scan_id=scan_id,
            domain_count=len(domains),
            domain_group=job.domain_group,
        )

        await self.broadcaster.broadcast(ScanEvent(
            scan_id=scan_id,
            type="scan_started",
            data={"domain_count": len(domains)},
        ))

        # Shared resources
        settings = self.config.settings
        cache = URLCache.load(
            cache_path=Path(self.data_dir) / "url_cache.json"
        )
        extractor = HtmlExtractor(settings.config_dir)
        keyword_matcher = KeywordMatcher(self.config.keywords_config)
        verifier = Verifier()

        # Per-domain persistence — saves policies to data/policies.json as each
        # domain completes, so results survive crashes. Uses atomic writes and
        # deduplication by URL.
        store = PolicyStore(data_dir=self.data_dir)

        # Incremental Google Sheets export — write policies as each domain
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
                    f"Google Sheets connected — {len(sheets_exported_urls)} "
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
                    skip_extensions=self.config.get_skip_extensions(),
                    crawl_blocked_patterns=self.config.get_crawl_blocked_patterns()
                        + domain.get("blocked_path_patterns", []),
                    url_skip_paths=self.config.get_url_skip_paths(),
                    url_skip_patterns=self.config.get_url_skip_patterns(),
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
                            dp.near_misses = scanner.progress.near_misses
                            dp.keywords_matched = scanner.progress.keywords_matched
                            dp.policies_found = scanner.progress.policies_found
                            dp.errors = scanner.progress.errors
                            dp.error_message = scanner.progress.error_message
                            break

                    job.progress.completed_domains += 1

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

                        # Export to Google Sheets immediately — don't wait for
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

            # Final Google Sheets reconciliation — catch any policies that
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
                # Sheets wasn't configured or connection failed at start —
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

            # Rejected-status reconciliation — a policy rejected via the
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
        except Exception as e:
            logger.error(f"Scan {scan_id} failed: {e}")
            job.status = ScanStatus.FAILED
            job.completed_at = datetime.utcnow()
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

    # Deep scans lower min_keyword_score from the standard baseline (5.0, the
    # keywords.yaml default) to 2.0 (see _with_deep_scan_defaults), which lets
    # noticeably more pages pass the keyword gate. Assumption: that roughly
    # doubles the pass rate used for the standard estimate below.
    DEEP_KEYWORD_PASS_RATE = 0.20

    def estimate_cost(self, domains_group: str, deep: bool = False) -> dict:
        """Estimate API costs for a scan.

        Raises ConfigurationError (via get_enabled_domains) for an unknown
        group/region/domain scope — callers (the API route) turn that into a
        400, mirroring domains.py's list_domains.
        """
        domains = self.config.get_enabled_domains(domains_group)
        settings = self.config.settings

        max_pages_per_domain = settings.crawl.max_pages_per_domain
        keyword_pass_rate = 0.10
        if deep:
            # Reuse _with_deep_scan_defaults as the single source of truth for
            # the deep-scan max_pages value instead of duplicating it here.
            max_pages_per_domain = self._with_deep_scan_defaults({})["max_pages"]
            keyword_pass_rate = self.DEEP_KEYWORD_PASS_RATE

        est_pages_per_domain = max_pages_per_domain // 2
        total_pages = len(domains) * est_pages_per_domain
        screening_pass_rate = 0.50

        keyword_passes = int(total_pages * keyword_pass_rate)
        screening_calls = keyword_passes
        analysis_calls = int(screening_calls * screening_pass_rate)

        # Haiku: ~$0.25/M input, ~$1.25/M output
        # Sonnet: ~$3/M input, ~$15/M output
        haiku_cost = screening_calls * (2000 * 0.25 + 50 * 1.25) / 1_000_000
        sonnet_cost = analysis_calls * (20000 * 3.0 + 1000 * 15.0) / 1_000_000
        auditor_cost = (5000 * 3.0 + 2000 * 15.0) / 1_000_000

        return {
            "domain_count": len(domains),
            "estimated_pages": total_pages,
            "estimated_keyword_passes": keyword_passes,
            "estimated_screening_calls": screening_calls,
            "estimated_analysis_calls": analysis_calls,
            "estimated_cost_usd": round(haiku_cost + sonnet_cost + auditor_cost, 2),
        }
