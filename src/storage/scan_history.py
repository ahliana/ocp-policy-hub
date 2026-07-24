"""Persisted scan run history (WP-5) — the ``scans`` table.

Every scan that actually runs through ``ScanManager._run_scan`` (dry runs and
the agent's ``--discover`` workflow never reach it — see scan_manager.py) gets
one row: written at start (``status="running"``) and updated in place at
completion, failure, or cancellation. This is the source of truth for "what
did a scan cost last time" — the cost-projection feature (WP-7) blends these
actuals with the static ``estimate_cost()`` formula once a scope has enough
completed runs to trust the average.

``channels`` is stored as a JSON array (text) since SQLite has no native
array type; ``list()`` deserializes it back for callers.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import db as storage_db


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
        input_tokens, output_tokens,
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
    }


_COLUMNS = (
    "scan_id, domain_group, mode, channels, status, started_at, completed_at, "
    "domains_scanned, policies_found, cost_usd, input_tokens, output_tokens"
)


class ScanHistoryStore:
    """Persistence for the ``scans`` table — one row per scan run."""

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
    ) -> None:
        """Insert the "running" row for a scan that just started."""
        self._conn.execute(
            "INSERT OR IGNORE INTO scans "
            "(scan_id, domain_group, mode, channels, status, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                scan_id, domain_group, mode,
                json.dumps(list(channels or [])),
                "running", _iso(started_at),
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
        called — should not happen in normal operation, but callers should
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
        limit/offset) — the ``total`` a paginated caller needs alongside one
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
        count — failed/cancelled scans have unreliable or partial totals.
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
