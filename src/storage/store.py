"""SQLite-backed store for policies and scan results.

Schema, connection handling, and the JSON -> SQLite migration live in
``src/storage/db.py``. This module keeps the same public interface the
JSON-file version had (same constructor, same method signatures and
result shapes) so callers never notice the storage swap.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from . import db as storage_db
from ..core.models import Policy

logger = logging.getLogger(__name__)


def _review_status_conditions(
    exclude_review_status: Optional[str],
    review_status_in: Optional[list[str]],
    column: str = "review_status",
) -> tuple[list[str], list]:
    """WHERE-clause fragments for the two review-status visibility filters.

    Shared by get_all/search/search_text so the public review visibility
    clamp (exclude a status, or restrict to a status list) is expressed once
    as SQL rather than duplicated per query builder.
    """
    conditions: list[str] = []
    params: list = []
    if exclude_review_status:
        conditions.append(f"({column} IS NULL OR {column} != ?)")
        params.append(exclude_review_status)
    if review_status_in:
        placeholders = ",".join("?" for _ in review_status_in)
        conditions.append(f"{column} IN ({placeholders})")
        params.extend(review_status_in)
    return conditions, params


class PolicyStore:
    """Persistent storage for discovered policies."""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.policies_file = self.data_dir / "policies.json"
        self._presanitize_legacy_json()
        self._conn = storage_db.connect(self.data_dir)

    def _presanitize_legacy_json(self) -> None:
        """Back up a corrupt legacy policies.json before migration runs.

        Preserves the pre-SQLite behavior: a corrupt or wrong-shaped
        policies.json never blocks startup or loses data — it's renamed to
        ``.corrupt`` and migration proceeds as if it were never there.
        """
        db_path = self.data_dir / storage_db.DB_FILENAME
        if db_path.exists() or not self.policies_file.exists():
            return
        try:
            data = json.loads(self.policies_file.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                logger.error(
                    "policies.json contains %s instead of a list — "
                    "backing up to policies.json.corrupt and starting fresh",
                    type(data).__name__,
                )
                self._backup_corrupt_file()
        except json.JSONDecodeError as e:
            logger.error(
                "policies.json is corrupted (JSON parse error: %s) — "
                "backing up to policies.json.corrupt so data is not lost",
                e,
            )
            self._backup_corrupt_file()
        except Exception as e:
            logger.error(
                "Failed to read policies.json: %s — "
                "file preserved, starting with empty policy list",
                e,
            )

    def _backup_corrupt_file(self) -> None:
        """Move corrupt policies.json to .corrupt so the user can recover data."""
        backup = self.policies_file.with_suffix(".json.corrupt")
        try:
            self.policies_file.rename(backup)
            logger.warning("Corrupt file backed up to %s", backup)
        except OSError as e:
            logger.error("Failed to backup corrupt file: %s", e)

    def save(self) -> bool:
        """Commit pending writes. Kept for interface compatibility."""
        try:
            self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to save policies: {e}")
            return False

    def add_policies(self, policies: list[Policy]) -> int:
        """Add policies, deduplicating by URL. Returns count added."""
        added = 0
        for policy in policies:
            record = policy.model_dump(mode="json")
            cur = self._conn.execute(
                """
                INSERT OR IGNORE INTO policies (
                    url, policy_name, jurisdiction, policy_type, lifecycle_stage,
                    review_status, relevance_score, scan_id, domain_id,
                    source_language, raw
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.get("url"),
                    record.get("policy_name"),
                    record.get("jurisdiction"),
                    record.get("policy_type"),
                    record.get("lifecycle_stage"),
                    record.get("review_status"),
                    record.get("relevance_score"),
                    record.get("scan_id"),
                    record.get("domain_id"),
                    record.get("source_language"),
                    json.dumps(record, ensure_ascii=False, default=str),
                ),
            )
            if cur.rowcount:
                added += 1
        # Commit unconditionally: even a batch that turns out to be all
        # duplicates still ran INSERT OR IGNORE statements, which open an
        # implicit transaction that must be closed out — otherwise it's
        # left open on this connection and blocks the next writer.
        self._conn.commit()
        return added

    def get_all(
        self,
        exclude_review_status: Optional[str] = None,
        review_status_in: Optional[list[str]] = None,
    ) -> list[dict]:
        """All policies, optionally narrowed by review status.

        ``exclude_review_status``/``review_status_in`` back the public
        review visibility clamp (src/api/review_visibility.py) with a WHERE
        clause instead of filtering the full list in Python at the route
        layer.
        """
        query = "SELECT raw FROM policies"
        conditions, params = _review_status_conditions(
            exclude_review_status, review_status_in
        )
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY rowid"
        rows = self._conn.execute(query, params).fetchall()
        return [json.loads(row[0]) for row in rows]

    def update_review_status(
        self, url: str, review_status: str, note: Optional[str] = None
    ) -> bool:
        """Set a policy's review status by URL. Returns False if not found.

        ``note`` is the optional reject reason (WP-4 Library): it lives only
        in the raw JSON as ``review_note``, no typed column. It is stored
        when rejecting with a reason; any other status — including a bare
        "rejected" with no reason — clears it, so a stale reason never
        survives a status change it doesn't belong to.
        """
        if review_status == "rejected" and note:
            cur = self._conn.execute(
                "UPDATE policies SET review_status = ?, "
                "raw = json_set(json_set(raw, '$.review_status', ?), "
                "'$.review_note', ?) WHERE url = ?",
                (review_status, review_status, note, url),
            )
        else:
            cur = self._conn.execute(
                "UPDATE policies SET review_status = ?, "
                "raw = json_remove(json_set(raw, '$.review_status', ?), "
                "'$.review_note') WHERE url = ?",
                (review_status, review_status, url),
            )
        # Commit unconditionally — the UPDATE opened a transaction whether
        # or not it matched a row, and an unmatched url must not leave it
        # open on this connection.
        self._conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def _search_conditions(
        jurisdiction: Optional[str],
        policy_type: Optional[str],
        min_score: Optional[int],
        scan_id: Optional[str],
        review_status: Optional[str],
        lifecycle_stage: Optional[str],
        exclude_review_status: Optional[str],
        review_status_in: Optional[list[str]],
    ) -> tuple[list[str], list]:
        """WHERE-clause fragments shared by ``search()`` and ``count()``, so
        the total-matching-rows count can never drift from what a page of
        results was actually filtered by.
        """
        conditions: list[str] = []
        params: list = []

        if jurisdiction:
            conditions.append("LOWER(jurisdiction) LIKE ? ESCAPE '\\'")
            params.append(f"%{storage_db.escape_like(jurisdiction.lower())}%")
        if review_status:
            conditions.append("review_status = ?")
            params.append(review_status)
        if policy_type:
            conditions.append("policy_type = ?")
            params.append(policy_type)
        if min_score is not None:
            conditions.append("COALESCE(relevance_score, 0) >= ?")
            params.append(min_score)
        if scan_id:
            conditions.append("scan_id = ?")
            params.append(scan_id)
        if lifecycle_stage:
            conditions.append("lifecycle_stage = ?")
            params.append(lifecycle_stage)

        visibility_conditions, visibility_params = _review_status_conditions(
            exclude_review_status, review_status_in
        )
        conditions.extend(visibility_conditions)
        params.extend(visibility_params)
        return conditions, params

    # sort name -> (SQL sort expression, default direction). "discovered_at"
    # isn't a typed column (see src/storage/db.py schema) so it sorts via
    # json_extract on the raw column instead — the bundled SQLite has JSON1
    # built in (confirmed at dev time; sqlite3.sqlite_version >= 3.38), so no
    # LIKE-style fallback is needed the way FTS5 needs one.
    _SORT_COLUMNS = {
        "name": ("policy_name", "asc"),
        "jurisdiction": ("jurisdiction", "asc"),
        "relevance": ("relevance_score", "desc"),
        "discovered_at": ("json_extract(raw, '$.discovered_at')", "desc"),
    }

    def search(
        self,
        jurisdiction: Optional[str] = None,
        policy_type: Optional[str] = None,
        min_score: Optional[int] = None,
        scan_id: Optional[str] = None,
        review_status: Optional[str] = None,
        lifecycle_stage: Optional[str] = None,
        exclude_review_status: Optional[str] = None,
        review_status_in: Optional[list[str]] = None,
        sort: Optional[str] = None,
        sort_dir: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> list[dict]:
        """Search policies with filters.

        The jurisdiction filter is a case-insensitive substring match — the
        exact semantics of the JSON-backed store this replaced. FTS5 token
        matching answers mid-word fragments differently, so the FTS index is
        deliberately NOT used here; it exists for the upcoming free-text
        search feature, where new semantics belong.

        ``exclude_review_status``/``review_status_in`` are the public review
        visibility clamp (src/api/review_visibility.py) — additive filters
        that combine with ``review_status`` rather than replace it.

        ``sort`` is one of ``"discovered_at"``, ``"name"``, ``"jurisdiction"``,
        ``"relevance"`` (default ``rowid`` insertion order, ascending, when
        omitted). ``sort_dir`` is ``"asc"``/``"desc"``; each sort key has a
        sensible default direction when ``sort_dir`` is omitted. ``rowid`` is
        always the tie-breaker so equal sort keys still paginate stably.
        ``limit``/``offset`` apply in SQL (``LIMIT``/``OFFSET``), not by
        slicing the full Python list.
        """
        conditions, params = self._search_conditions(
            jurisdiction, policy_type, min_score, scan_id, review_status,
            lifecycle_stage, exclude_review_status, review_status_in,
        )

        query = "SELECT raw, rowid FROM policies"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        if sort in self._SORT_COLUMNS:
            column, default_dir = self._SORT_COLUMNS[sort]
            direction = "DESC" if (sort_dir or default_dir).lower() == "desc" else "ASC"
            query += f" ORDER BY {column} {direction}, rowid ASC"
        else:
            query += " ORDER BY rowid"

        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
            if offset is not None:
                query += " OFFSET ?"
                params.append(offset)
        elif offset is not None:
            # SQLite requires a LIMIT for OFFSET; -1 means "no limit".
            query += " LIMIT -1 OFFSET ?"
            params.append(offset)

        rows = self._conn.execute(query, params).fetchall()
        return [json.loads(row[0]) for row in rows]

    def count(
        self,
        jurisdiction: Optional[str] = None,
        policy_type: Optional[str] = None,
        min_score: Optional[int] = None,
        scan_id: Optional[str] = None,
        review_status: Optional[str] = None,
        lifecycle_stage: Optional[str] = None,
        exclude_review_status: Optional[str] = None,
        review_status_in: Optional[list[str]] = None,
    ) -> int:
        """Total rows matching the same filters ``search()`` accepts (minus
        sort/limit/offset) — the ``total`` a paginated caller needs alongside
        one page of results.
        """
        conditions, params = self._search_conditions(
            jurisdiction, policy_type, min_score, scan_id, review_status,
            lifecycle_stage, exclude_review_status, review_status_in,
        )
        query = "SELECT COUNT(*) FROM policies"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        return self._conn.execute(query, params).fetchone()[0]

    def search_text(
        self,
        query: str,
        jurisdiction: Optional[str] = None,
        policy_type: Optional[str] = None,
        min_score: Optional[int] = None,
        review_status: Optional[str] = None,
        lifecycle_stage: Optional[str] = None,
        limit: int = 50,
        exclude_review_status: Optional[str] = None,
        review_status_in: Optional[list[str]] = None,
    ) -> list[dict]:
        """Free-text search across policy name, summary, key requirements,
        and jurisdiction, ANDing every whitespace-separated token of
        ``query`` and treating the last token as a prefix.

        Uses the ``policies_fts`` FTS5 index (ranked by ``bm25``, name
        matches weighted highest) when available, and falls back to a
        per-token, per-column case-insensitive substring scan otherwise —
        same filters, same result shape either way. Malicious or malformed
        query text (quotes, parentheses, boolean operators) is neutralized
        by quoting, never raised.

        ``exclude_review_status``/``review_status_in`` are the public review
        visibility clamp (src/api/review_visibility.py).
        """
        query = (query or "").strip()
        if not query:
            return []

        limit = max(1, min(int(limit), 100))

        conditions: list[str] = []
        params: list = []
        if jurisdiction:
            conditions.append("LOWER(policies.jurisdiction) LIKE ? ESCAPE '\\'")
            params.append(f"%{storage_db.escape_like(jurisdiction.lower())}%")
        if review_status:
            conditions.append("policies.review_status = ?")
            params.append(review_status)
        if policy_type:
            conditions.append("policies.policy_type = ?")
            params.append(policy_type)
        if min_score is not None:
            conditions.append("COALESCE(policies.relevance_score, 0) >= ?")
            params.append(min_score)
        if lifecycle_stage:
            conditions.append("policies.lifecycle_stage = ?")
            params.append(lifecycle_stage)

        visibility_conditions, visibility_params = _review_status_conditions(
            exclude_review_status, review_status_in, column="policies.review_status"
        )
        conditions.extend(visibility_conditions)
        params.extend(visibility_params)

        if storage_db.fts5_enabled(self._conn):
            match_query = storage_db.build_fts_match_query(query)
            sql = (
                "SELECT policies.raw FROM policies "
                "JOIN policies_fts ON policies.rowid = policies_fts.rowid "
                "WHERE policies_fts MATCH ?"
            )
            all_params = [match_query, *params]
            if conditions:
                sql += " AND " + " AND ".join(conditions)
            # Name matches rank far above summary/requirements/jurisdiction hits.
            sql += " ORDER BY bm25(policies_fts, 10.0, 1.0, 1.0, 1.0) LIMIT ?"
            all_params.append(limit)
            rows = self._conn.execute(sql, all_params).fetchall()
            return [json.loads(row[0]) for row in rows]

        # LIKE fallback: every token must substring-match at least one of
        # the four indexed fields, case-insensitively.
        like_conditions = []
        like_params: list = []
        for token in query.split():
            escaped = f"%{storage_db.escape_like(token.lower())}%"
            like_conditions.append(
                "(LOWER(policies.policy_name) LIKE ? ESCAPE '\\' "
                "OR LOWER(COALESCE(json_extract(policies.raw, '$.summary'), '')) LIKE ? ESCAPE '\\' "
                "OR LOWER(COALESCE(json_extract(policies.raw, '$.key_requirements'), '')) "
                "LIKE ? ESCAPE '\\' "
                "OR LOWER(policies.jurisdiction) LIKE ? ESCAPE '\\')"
            )
            like_params.extend([escaped] * 4)

        sql = "SELECT policies.raw FROM policies"
        all_conditions = conditions + like_conditions
        all_params = params + like_params
        if all_conditions:
            sql += " WHERE " + " AND ".join(all_conditions)
        sql += " ORDER BY policies.rowid LIMIT ?"
        all_params.append(limit)
        rows = self._conn.execute(sql, all_params).fetchall()
        return [json.loads(row[0]) for row in rows]

    def get_stats(self) -> dict:
        """Get aggregate policy statistics."""
        policies = self.get_all()
        by_jurisdiction: dict[str, int] = {}
        by_type: dict[str, int] = {}
        by_score: dict[str, int] = {"1-3": 0, "4-6": 0, "7-8": 0, "9-10": 0}
        flagged = 0

        for p in policies:
            j = p.get("jurisdiction", "Unknown") or "Unknown"
            by_jurisdiction[j] = by_jurisdiction.get(j, 0) + 1

            pt = p.get("policy_type", "unknown") or "unknown"
            by_type[pt] = by_type.get(pt, 0) + 1

            score = p.get("relevance_score", 0) or 0
            if score <= 3:
                by_score["1-3"] += 1
            elif score <= 6:
                by_score["4-6"] += 1
            elif score <= 8:
                by_score["7-8"] += 1
            else:
                by_score["9-10"] += 1

            if p.get("verification_flags"):
                flagged += 1

        return {
            "total": len(policies),
            "by_jurisdiction": by_jurisdiction,
            "by_type": by_type,
            "by_score_range": by_score,
            "flagged_count": flagged,
        }
