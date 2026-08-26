"""Persisted scan run history (WP-5) - the ``scans`` table.

Every scan that actually runs through ``ScanManager._run_scan`` (dry runs and
the agent's ``--discover`` workflow never reach it - see scan_manager.py) gets
one row: written at start (``status="running"``) and updated in place at
completion, failure, or cancellation. This is the source of truth for "what
did a scan cost last time" - the cost-projection feature (WP-7) blends these
actuals with the static ``estimate_cost()`` formula once a scope has enough
completed runs to trust the average.

``channels`` is stored as a JSON array (text) since SQLite has no native
array type; ``list()`` deserializes it back for callers.

``record_domains``/``measured_rates`` (WP-23/WP-25) work off a second table,
``scan_domains`` - one row per domain per scan, holding the funnel counts
``ScanManager`` tracks in memory during a run. ``measured_rates`` feeds back
into ``ScanManager.estimate_cost()`` once there is enough completed-scan
history to calibrate against, in place of its static assumptions.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from . import db as storage_db

if TYPE_CHECKING:
    from ..core.models import DomainProgress


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _row_to_dict(row: tuple) -> dict:
    (
        scan_id, domain_group, mode, channels, status, started_at,
        completed_at, domains_scanned, policies_found, cost_usd,
        input_tokens, output_tokens, estimated_cost_usd, estimated_low_usd,
        estimated_high_usd,
    ) = row
    return {
        "scan_id": scan_id,
        "domain_group": domain_group,
        "mode": mode,
        "channels": json.loads(channels) if channels else [],
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "domains_scanned": domains_scanned,
        "policies_found": policies_found,
        "cost_usd": cost_usd,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": estimated_cost_usd,
        "estimated_low_usd": estimated_low_usd,
        "estimated_high_usd": estimated_high_usd,
    }


_COLUMNS = (
    "scan_id, domain_group, mode, channels, status, started_at, completed_at, "
    "domains_scanned, policies_found, cost_usd, input_tokens, output_tokens, "
    "estimated_cost_usd, estimated_low_usd, estimated_high_usd"
)

_DOMAIN_COLUMNS = (
    "scan_id, domain_id, channel, pages_crawled, keywords_matched, "
    "filtered_keywords, filtered_screening, llm_skipped, policies_found, "
    "errors, completed_at"
)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _median_p25_p75(values: list[float]) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """(median, p25, p75) for ``values`` - median and outlier-resistant IQR
    bounds (WP-25). ``None`` for an empty list; a single value is its own
    median/p25/p75 (``statistics.quantiles`` requires at least two points).
    """
    if not values:
        return None, None, None
    if len(values) == 1:
        v = round(values[0], 4)
        return v, v, v
    median = round(statistics.median(values), 4)
    q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return median, round(q1, 4), round(q3, 4)


class ScanHistoryStore:
    """Persistence for the ``scans`` table - one row per scan run."""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self._conn: sqlite3.Connection = storage_db.connect(self.data_dir)

    def record_start(
        self,
        scan_id: str,
        domain_group: str,
        mode: str,
        channels: list[str],
        started_at,
        estimated_cost_usd: Optional[float] = None,
        estimated_low_usd: Optional[float] = None,
        estimated_high_usd: Optional[float] = None,
    ) -> None:
        """Insert the "running" row for a scan that just started.

        ``estimated_*`` (WP-24) is the ``ScanManager.estimate_cost()`` trio
        computed for this scan's exact scope/channels/deep at the moment it
        started - ``None`` for all three if estimation itself failed (a
        scan must never be blocked by a broken estimate).
        """
        self._conn.execute(
            "INSERT OR IGNORE INTO scans "
            "(scan_id, domain_group, mode, channels, status, started_at, "
            "estimated_cost_usd, estimated_low_usd, estimated_high_usd) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scan_id, domain_group, mode,
                json.dumps(list(channels or [])),
                "running", _iso(started_at),
                estimated_cost_usd, estimated_low_usd, estimated_high_usd,
            ),
        )
        self._conn.commit()

    def record_completion(
        self,
        scan_id: str,
        status: str,
        completed_at,
        domains_scanned: Optional[int] = None,
        policies_found: Optional[int] = None,
        cost_usd: Optional[float] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
    ) -> bool:
        """Update a scan's row with its final outcome.

        ``status`` is one of "completed", "failed", "cancelled". Returns
        False if no row with ``scan_id`` exists (record_start was never
        called - should not happen in normal operation, but callers should
        not assume it always finds a row).
        """
        cur = self._conn.execute(
            "UPDATE scans SET status = ?, completed_at = ?, domains_scanned = ?, "
            "policies_found = ?, cost_usd = ?, input_tokens = ?, output_tokens = ? "
            "WHERE scan_id = ?",
            (
                status, _iso(completed_at), domains_scanned, policies_found,
                cost_usd, input_tokens, output_tokens, scan_id,
            ),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def record_domains(
        self,
        scan_id: str,
        domains: list[tuple["DomainProgress", str]],
        completed_at,
    ) -> None:
        """Persist the per-domain funnel (WP-23) - one row per domain,
        written in a single transaction: either every row lands or none do.

        ``domains`` pairs each domain's final ``DomainProgress`` with its
        scan channel (``ScanManager._domain_channel()``). A no-op for an
        empty list (a zero-domain scan has nothing to record).
        """
        if not domains:
            return
        with self._conn:
            for progress, channel in domains:
                self._conn.execute(
                    f"INSERT OR REPLACE INTO scan_domains ({_DOMAIN_COLUMNS}) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        scan_id, progress.domain_id, channel,
                        progress.pages_crawled, progress.keywords_matched,
                        progress.filtered_keywords, progress.filtered_screening,
                        progress.llm_skipped, progress.policies_found,
                        progress.errors, _iso(completed_at),
                    ),
                )

    def domains_for_scan(self, scan_id: str) -> list[dict]:
        """All ``scan_domains`` rows for one scan - feeds the DB fallback
        for GET /api/scans/{scan_id} once a completed scan's job has left
        in-process memory (server restart, or eviction)."""
        rows = self._conn.execute(
            f"SELECT {_DOMAIN_COLUMNS} FROM scan_domains WHERE scan_id = ?",
            (scan_id,),
        ).fetchall()
        return [
            {
                "scan_id": r[0],
                "domain_id": r[1],
                "channel": r[2],
                "pages_crawled": r[3],
                "keywords_matched": r[4],
                "filtered_keywords": r[5],
                "filtered_screening": r[6],
                "llm_skipped": r[7],
                "policies_found": r[8],
                "errors": r[9],
                "completed_at": r[10],
            }
            for r in rows
        ]

    def get(self, scan_id: str) -> Optional[dict]:
        """One scan's row by id, or ``None`` - the DB-fallback lookup for
        GET /api/scans/{scan_id}."""
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM scans WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    @staticmethod
    def _conditions(domain_group: Optional[str], status: Optional[str]) -> tuple[list[str], list]:
        conditions: list[str] = []
        params: list = []
        if domain_group:
            conditions.append("domain_group = ?")
            params.append(domain_group)
        if status:
            conditions.append("status = ?")
            params.append(status)
        return conditions, params

    def list(
        self,
        domain_group: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Scans newest-first (``started_at`` desc, ``rowid`` desc tie-break)."""
        conditions, params = self._conditions(domain_group, status)
        query = f"SELECT {_COLUMNS} FROM scans"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY started_at DESC, rowid DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self._conn.execute(query, params).fetchall()
        return [_row_to_dict(row) for row in rows]

    def count(self, domain_group: Optional[str] = None, status: Optional[str] = None) -> int:
        """Total rows matching the same filters ``list()`` accepts (minus
        limit/offset) - the ``total`` a paginated caller needs alongside one
        page of results, mirroring ``PolicyStore.count()``."""
        conditions, params = self._conditions(domain_group, status)
        query = "SELECT COUNT(*) FROM scans"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        return self._conn.execute(query, params).fetchone()[0]

    def stats(self, domain_group: str) -> dict:
        """Aggregate actuals for ``domain_group`` over its completed runs.

        Feeds the cost-projection blend rule (WP-7): once a scope has real
        completed runs, their mean cost/policy count is preferred over the
        static ``estimate_cost()`` formula. Only ``status="completed"`` runs
        count - failed/cancelled scans have unreliable or partial totals.
        """
        rows = self._conn.execute(
            "SELECT cost_usd, policies_found FROM scans "
            "WHERE domain_group = ? AND status = 'completed' "
            "ORDER BY completed_at DESC",
            (domain_group,),
        ).fetchall()

        if not rows:
            return {
                "runs": 0,
                "mean_cost_usd": None,
                "last_cost_usd": None,
                "mean_policies": None,
            }

        costs = [r[0] for r in rows if r[0] is not None]
        policies = [r[1] for r in rows if r[1] is not None]
        return {
            "runs": len(rows),
            "mean_cost_usd": (sum(costs) / len(costs)) if costs else None,
            "last_cost_usd": rows[0][0],
            "mean_policies": (sum(policies) / len(policies)) if policies else None,
        }

    def measured_rates(self) -> dict:
        """Calibrated crawl/structured rates from completed scans' funnels
        (WP-25) - feeds ``ScanManager.estimate_cost()``'s provenance-tagged
        assumptions once real scan history exists to calibrate against.

        Completed scans count, including budget-capped ones
        (``completed_budget_reached``): a capped scan's per-domain rows are
        fully-scanned domains (the skipped tail has zero pages and is
        filtered out below), so they are valid rate evidence even though the
        RUN was truncated - which is also why ``stats()`` deliberately still
        excludes capped runs from mean-cost-per-run. Failed/cancelled runs
        stay out entirely (unreliable/partial funnels). Every
        structured channel (``law_apis``, ``transposition``) rolls into one
        "structured" bucket, mirroring how ``estimate_cost()`` already prices
        them (a flat items-per-source model, no keyword gate).

        A bucket's rates stay ``None`` - falling back to the caller's static
        assumptions - until it has rows from at least 2 distinct completed
        scans *and* at least 3 domain rows total; below that there isn't
        enough signal to trust over a fixed assumption. Rates are aggregated
        with the median (outlier-resistant); ``spread`` gives each rate's
        25th/75th percentile (interquartile range) for range estimates
        (WP-26).
        """
        rows = self._conn.execute(
            "SELECT sd.scan_id, sd.channel, sd.pages_crawled, sd.keywords_matched, "
            "sd.filtered_screening, sd.llm_skipped FROM scan_domains sd "
            "JOIN scans s ON s.scan_id = sd.scan_id "
            "WHERE s.status IN ('completed', 'completed_budget_reached')"
        ).fetchall()

        crawl_rows = [r for r in rows if r[1] == "crawl"]
        structured_rows = [r for r in rows if r[1] != "crawl"]

        return {
            "crawl": self._crawl_rates(crawl_rows),
            "structured": self._structured_rates(structured_rows),
        }

    @classmethod
    def _crawl_rates(cls, rows: list[tuple]) -> dict:
        # row: (scan_id, channel, pages_crawled, keywords_matched,
        #       filtered_screening, llm_skipped)
        scans = len({r[0] for r in rows})
        if not (scans >= 2 and len(rows) >= 3):
            return {
                "keyword_rate": None, "screening_pass_rate": None,
                "pages_per_domain": None, "scans": scans,
                "spread": {
                    "keyword_rate": {"p25": None, "p75": None},
                    "screening_pass_rate": {"p25": None, "p75": None},
                    "pages_per_domain": {"p25": None, "p75": None},
                },
            }

        keyword_rates = [
            _clamp01(kw / pages) for (_, _, pages, kw, _, _) in rows if pages > 0
        ]
        pages_values = [float(pages) for (_, _, pages, _, _, _) in rows if pages > 0]
        screening_rates = [
            _clamp01((kw - fs - skip) / kw) for (_, _, _, kw, fs, skip) in rows if kw > 0
        ]

        kw_median, kw_p25, kw_p75 = _median_p25_p75(keyword_rates)
        pages_median, pages_p25, pages_p75 = _median_p25_p75(pages_values)
        scr_median, scr_p25, scr_p75 = _median_p25_p75(screening_rates)

        return {
            "keyword_rate": kw_median,
            "screening_pass_rate": scr_median,
            "pages_per_domain": pages_median,
            "scans": scans,
            "spread": {
                "keyword_rate": {"p25": kw_p25, "p75": kw_p75},
                "screening_pass_rate": {"p25": scr_p25, "p75": scr_p75},
                "pages_per_domain": {"p25": pages_p25, "p75": pages_p75},
            },
        }

    @classmethod
    def _structured_rates(cls, rows: list[tuple]) -> dict:
        # row: (scan_id, channel, pages_crawled, keywords_matched,
        #       filtered_screening, llm_skipped) - pages_crawled here is an
        # item count (see the scan_domains DDL comment in db.py).
        scans = len({r[0] for r in rows})
        if not (scans >= 2 and len(rows) >= 3):
            return {
                "items_per_source": None, "screening_pass_rate": None,
                "scans": scans,
                "spread": {
                    "items_per_source": {"p25": None, "p75": None},
                    "screening_pass_rate": {"p25": None, "p75": None},
                },
            }

        items_values = [float(pages) for (_, _, pages, _, _, _) in rows]
        screening_rates = [
            _clamp01((kw - fs - skip) / kw) for (_, _, _, kw, fs, skip) in rows if kw > 0
        ]

        items_median, items_p25, items_p75 = _median_p25_p75(items_values)
        scr_median, scr_p25, scr_p75 = _median_p25_p75(screening_rates)

        return {
            "items_per_source": items_median,
            "screening_pass_rate": scr_median,
            "scans": scans,
            "spread": {
                "items_per_source": {"p25": items_p25, "p75": items_p75},
                "screening_pass_rate": {"p25": scr_p25, "p75": scr_p75},
            },
        }
