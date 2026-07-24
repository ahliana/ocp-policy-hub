"""SQLite storage foundation: connection factory, schema DDL, and the
one-time JSON -> SQLite migration.

Everything lives in a single file, ``policypulse.db``, inside the store's
``data_dir``. WAL mode and foreign keys are enabled on every connection.

Schema
------
- ``policies`` / ``leads``: primary-keyed tables with a handful of typed
  columns (for SQL filtering) plus a ``raw`` JSON column holding the
  complete original dict. ``raw`` is the source of truth for round-tripping
  — callers must get back exactly what the JSON file version returned.
- ``kv``: small bookkeeping counters (ask usage, LegiScan usage/seen,
  NIM seen) that used to live in their own tiny JSON files.
- ``jurisdictions``: a read-only mirror of ``config/jurisdictions.yaml``,
  rebuilt from the YAML on every connection. The YAML remains the source
  of truth; nothing ever writes to this table except the rebuild.
- ``policies_fts``: an FTS5 external-content index over policy_name,
  summary, key_requirements, and jurisdiction, kept in sync by triggers.
  If the local SQLite build lacks FTS5, the table and triggers are simply
  not created — callers detect this with :func:`fts5_enabled` and fall
  back to a LIKE query.

Migration
---------
:func:`migrate_json_to_db` runs once, the first time a store connects to a
``data_dir`` that has legacy JSON files but no ``policypulse.db`` yet. It
reads ``policies.json``, ``leads.json``, and the four kv JSON files, writes
everything into a fresh db in one transaction, then verifies every record
round-trips byte-for-byte before considering the migration successful. On
any mismatch it raises and deletes the partial db, so a later run gets a
clean second attempt. The legacy JSON files themselves are never modified
or deleted — they are the rollback path.
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_FILENAME = "policypulse.db"

POLICIES_JSON = "policies.json"
LEADS_JSON = "leads.json"

# name -> legacy filename, for the small bookkeeping files consolidated
# into the kv table by migration.
KV_LEGACY_FILES = {
    "ask_usage": "ask_usage.json",
    "legiscan_usage": "legiscan_usage.json",
    "legiscan_seen": "legiscan_seen.json",
    "nim_seen": "nim_seen.json",
}

_SCHEMA_CORE = """
CREATE TABLE IF NOT EXISTS policies (
    url TEXT PRIMARY KEY,
    policy_name TEXT,
    jurisdiction TEXT,
    policy_type TEXT,
    lifecycle_stage TEXT,
    review_status TEXT,
    relevance_score INTEGER,
    scan_id TEXT,
    domain_id TEXT,
    source_language TEXT,
    raw TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_policies_jurisdiction ON policies(jurisdiction);
CREATE INDEX IF NOT EXISTS idx_policies_policy_type ON policies(policy_type);
CREATE INDEX IF NOT EXISTS idx_policies_scan_id ON policies(scan_id);
CREATE INDEX IF NOT EXISTS idx_policies_review_status ON policies(review_status);
CREATE INDEX IF NOT EXISTS idx_policies_relevance_score ON policies(relevance_score);

CREATE TABLE IF NOT EXISTS leads (
    lead_id TEXT PRIMARY KEY,
    source_url TEXT UNIQUE,
    status TEXT,
    raw TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);

CREATE TABLE IF NOT EXISTS kv (
    name TEXT PRIMARY KEY,
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jurisdictions (
    slug TEXT PRIMARY KEY,
    name TEXT,
    kind TEXT,
    iso3 TEXT,
    iso_numeric TEXT,
    parent TEXT
);

CREATE TABLE IF NOT EXISTS scans (
    scan_id TEXT PRIMARY KEY,
    domain_group TEXT,
    mode TEXT,
    channels TEXT,
    status TEXT,
    started_at TEXT,
    completed_at TEXT,
    domains_scanned INTEGER,
    policies_found INTEGER,
    cost_usd REAL,
    input_tokens INTEGER,
    output_tokens INTEGER
);

CREATE INDEX IF NOT EXISTS idx_scans_domain_group ON scans(domain_group);
CREATE INDEX IF NOT EXISTS idx_scans_status ON scans(status);
CREATE INDEX IF NOT EXISTS idx_scans_started_at ON scans(started_at);
"""

_SCHEMA_FTS5 = """
CREATE VIRTUAL TABLE IF NOT EXISTS policies_fts USING fts5(
    policy_name, summary, key_requirements, jurisdiction,
    content='policies', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS policies_fts_ai AFTER INSERT ON policies BEGIN
    INSERT INTO policies_fts(rowid, policy_name, summary, key_requirements, jurisdiction)
    VALUES (
        new.rowid, new.policy_name,
        json_extract(new.raw, '$.summary'),
        json_extract(new.raw, '$.key_requirements'),
        new.jurisdiction
    );
END;

CREATE TRIGGER IF NOT EXISTS policies_fts_ad AFTER DELETE ON policies BEGIN
    INSERT INTO policies_fts(policies_fts, rowid, policy_name, summary, key_requirements, jurisdiction)
    VALUES (
        'delete', old.rowid, old.policy_name,
        json_extract(old.raw, '$.summary'),
        json_extract(old.raw, '$.key_requirements'),
        old.jurisdiction
    );
END;

CREATE TRIGGER IF NOT EXISTS policies_fts_au AFTER UPDATE ON policies BEGIN
    INSERT INTO policies_fts(policies_fts, rowid, policy_name, summary, key_requirements, jurisdiction)
    VALUES (
        'delete', old.rowid, old.policy_name,
        json_extract(old.raw, '$.summary'),
        json_extract(old.raw, '$.key_requirements'),
        old.jurisdiction
    );
    INSERT INTO policies_fts(rowid, policy_name, summary, key_requirements, jurisdiction)
    VALUES (
        new.rowid, new.policy_name,
        json_extract(new.raw, '$.summary'),
        json_extract(new.raw, '$.key_requirements'),
        new.jurisdiction
    );
END;
"""


class MigrationVerificationError(RuntimeError):
    """Raised when migrated data does not round-trip against its JSON source."""


def fts5_supported() -> bool:
    """Probe whether this SQLite build has the FTS5 extension compiled in."""
    probe = sqlite3.connect(":memory:")
    try:
        probe.execute("CREATE VIRTUAL TABLE t USING fts5(a)")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        probe.close()


def fts5_enabled(conn: sqlite3.Connection) -> bool:
    """Whether this particular connection's db actually has the FTS5 index."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='policies_fts'"
    ).fetchone()
    return row is not None


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_CORE)
    if fts5_supported():
        conn.executescript(_SCHEMA_FTS5)
    conn.commit()


def _rebuild_jurisdictions(conn: sqlite3.Connection) -> None:
    """Reload config/jurisdictions.yaml and refresh the read-only mirror."""
    from ..core import jurisdictions as jurisdictions_module

    by_slug = jurisdictions_module._load()
    conn.execute("DELETE FROM jurisdictions")
    conn.executemany(
        "INSERT INTO jurisdictions (slug, name, kind, iso3, iso_numeric, parent) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (j.slug, j.name, j.kind, j.iso3, j.iso_numeric, j.parent)
            for j in by_slug.values()
        ],
    )
    conn.commit()


def connect(data_dir: str | Path) -> sqlite3.Connection:
    """Open (creating and migrating if needed) the store's SQLite db."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / DB_FILENAME

    migrate_json_to_db(data_dir)

    # Store instances are constructed once and reused as FastAPI dependency
    # singletons in some call sites (see tests/unit/test_leads_api.py), so
    # requests handled on the thread pool reuse this same connection.
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_schema(conn)
    _rebuild_jurisdictions(conn)
    return conn


def escape_like(term: str) -> str:
    """Escape ``%``/``_``/``\\`` so a LIKE pattern matches ``term`` literally."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_fts_match_query(query: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression.

    Every whitespace-separated token is wrapped in double quotes (doubling
    any embedded quote), which forces FTS5 to treat it as a literal phrase —
    ``AND``/``OR``/``NOT``, parentheses, ``*``, and column-filter ``:``
    syntax are all neutralized this way. The quoted tokens are implicitly
    ANDed by FTS5, and a trailing ``*`` on the last one turns it into a
    prefix match (FTS5 supports a prefix ``*`` immediately after a quoted
    phrase's closing quote — it matches the phrase's final token as a
    prefix rather than a whole term).
    """
    tokens = query.split()
    quoted = ['"' + token.replace('"', '""') + '"' for token in tokens]
    quoted[-1] += "*"
    return " ".join(quoted)


def _insert_policy_row(conn: sqlite3.Connection, record: dict) -> None:
    conn.execute(
        """
        INSERT INTO policies (
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


def _insert_lead_row(conn: sqlite3.Connection, record: dict) -> None:
    conn.execute(
        "INSERT INTO leads (lead_id, source_url, status, raw) VALUES (?, ?, ?, ?)",
        (
            record.get("lead_id"),
            record.get("source_url"),
            record.get("status"),
            json.dumps(record, ensure_ascii=False, default=str),
        ),
    )


def _verify_migration(
    conn: sqlite3.Connection,
    policies_data: list[dict],
    leads_data: list[dict],
    kv_data: dict[str, dict],
) -> None:
    """Count, key-set, and full dict-equality checks against the JSON source."""
    db_urls = {row[0] for row in conn.execute("SELECT url FROM policies")}
    src_urls = {p["url"] for p in policies_data}
    if len(db_urls) != len(policies_data) or db_urls != src_urls:
        raise MigrationVerificationError(
            f"Policy migration mismatch: {len(policies_data)} source records vs "
            f"{len(db_urls)} rows in db (url-set equal: {db_urls == src_urls})"
        )
    for record in policies_data:
        row = conn.execute(
            "SELECT raw FROM policies WHERE url = ?", (record["url"],)
        ).fetchone()
        if row is None or json.loads(row[0]) != record:
            raise MigrationVerificationError(
                f"Policy round-trip mismatch for url {record.get('url')!r}"
            )

    db_lead_ids = {row[0] for row in conn.execute("SELECT lead_id FROM leads")}
    src_lead_ids = {lead["lead_id"] for lead in leads_data}
    if len(db_lead_ids) != len(leads_data) or db_lead_ids != src_lead_ids:
        raise MigrationVerificationError(
            f"Lead migration mismatch: {len(leads_data)} source records vs "
            f"{len(db_lead_ids)} rows in db (id-set equal: {db_lead_ids == src_lead_ids})"
        )
    for record in leads_data:
        row = conn.execute(
            "SELECT raw FROM leads WHERE lead_id = ?", (record["lead_id"],)
        ).fetchone()
        if row is None or json.loads(row[0]) != record:
            raise MigrationVerificationError(
                f"Lead round-trip mismatch for lead_id {record.get('lead_id')!r}"
            )

    for name, payload in kv_data.items():
        row = conn.execute("SELECT data FROM kv WHERE name = ?", (name,)).fetchone()
        if row is None or json.loads(row[0]) != payload:
            raise MigrationVerificationError(f"kv round-trip mismatch for {name!r}")


def migrate_json_to_db(data_dir: str | Path) -> None:
    """One-time, idempotent JSON -> SQLite migration for ``data_dir``.

    No-ops if ``policypulse.db`` already exists (already migrated) or if
    there are no legacy JSON files to migrate (fresh install). The legacy
    files are read but never modified or deleted.
    """
    data_dir = Path(data_dir)
    db_path = data_dir / DB_FILENAME
    if db_path.exists():
        return

    policies_path = data_dir / POLICIES_JSON
    leads_path = data_dir / LEADS_JSON
    kv_paths = {name: data_dir / filename for name, filename in KV_LEGACY_FILES.items()}

    has_legacy = (
        policies_path.exists()
        or leads_path.exists()
        or any(p.exists() for p in kv_paths.values())
    )
    if not has_legacy:
        return

    policies_data: list[dict] = []
    if policies_path.exists():
        policies_data = json.loads(policies_path.read_text(encoding="utf-8"))
        if not isinstance(policies_data, list):
            raise ValueError(f"{policies_path} does not contain a JSON list")

    leads_data: list[dict] = []
    if leads_path.exists():
        leads_data = json.loads(leads_path.read_text(encoding="utf-8"))
        if not isinstance(leads_data, list):
            raise ValueError(f"{leads_path} does not contain a JSON list")

    kv_data: dict[str, dict] = {}
    for name, path in kv_paths.items():
        if path.exists():
            kv_data[name] = json.loads(path.read_text(encoding="utf-8"))

    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        _ensure_schema(conn)
        with conn:
            for record in policies_data:
                _insert_policy_row(conn, record)
            for record in leads_data:
                _insert_lead_row(conn, record)
            for name, payload in kv_data.items():
                conn.execute(
                    "INSERT INTO kv (name, data) VALUES (?, ?)",
                    (name, json.dumps(payload, ensure_ascii=False, default=str)),
                )

        _verify_migration(conn, policies_data, leads_data, kv_data)
    except Exception:
        conn.close()
        for suffix in ("", "-journal", "-wal", "-shm"):
            leftover = db_path.with_name(db_path.name + suffix)
            if leftover.exists():
                leftover.unlink()
        raise
    else:
        conn.close()
        logger.info(
            "Migrated %d policies, %d leads, %d kv entries from JSON to %s",
            len(policies_data), len(leads_data), len(kv_data), db_path,
        )


def kv_get(conn: sqlite3.Connection, name: str) -> Optional[dict]:
    row = conn.execute("SELECT data FROM kv WHERE name = ?", (name,)).fetchone()
    return json.loads(row[0]) if row else None


def kv_set(conn: sqlite3.Connection, name: str, data: dict) -> None:
    conn.execute(
        "INSERT INTO kv (name, data) VALUES (?, ?) "
        "ON CONFLICT(name) DO UPDATE SET data = excluded.data",
        (name, json.dumps(data, ensure_ascii=False, default=str)),
    )
    conn.commit()
