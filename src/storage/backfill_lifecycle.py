"""One-time backfill for policies whose lifecycle_stage COLUMN is NULL.

Some rows predate consistent population of the lifecycle_stage column: the
column is NULL even though the row's ``raw`` JSON may already carry a valid
stage. This derives the column value (and keeps ``raw`` consistent with it,
since ``raw`` is the source of truth) from ``raw``'s own ``lifecycle_stage``
field when it is one of ``LIFECYCLE_STAGES``, else falls back to "unknown"
for both the column and ``raw``.

Run as::

    python -m src.storage.backfill_lifecycle [--dry-run] [--data-dir data]

Idempotent: once every row has a non-NULL lifecycle_stage column, a second
run examines 0 rows and updates 0 rows.

FTS note: ``policies_fts`` (see src/storage/db.py) indexes only policy_name,
summary, key_requirements, and jurisdiction — lifecycle_stage is not part of
the FTS content, so this backfill has no FTS impact either way. The write
still goes through a plain ``UPDATE policies ...`` statement, so the
existing ``policies_fts_au`` trigger fires normally (a harmless refresh of
the indexed columns) rather than leaving the index in a stale state.
"""

import argparse
import json
import logging
from collections import Counter
from pathlib import Path

from ..core.models import LIFECYCLE_STAGES
from . import db as storage_db

logger = logging.getLogger(__name__)


def _derive_stage(raw: dict) -> str:
    stage = raw.get("lifecycle_stage")
    return stage if stage in LIFECYCLE_STAGES else "unknown"


def backfill_lifecycle(data_dir: str | Path, dry_run: bool = False) -> dict:
    """Backfill NULL lifecycle_stage columns from ``raw``.

    Returns a summary dict: ``examined`` (NULL-column rows found),
    ``updated`` (rows actually written — always 0 for dry_run), and
    ``counts`` (resulting lifecycle_stage -> row count, reflecting the
    would-be outcome even under dry_run).
    """
    conn = storage_db.connect(data_dir)
    try:
        rows = conn.execute(
            "SELECT url, raw FROM policies WHERE lifecycle_stage IS NULL"
        ).fetchall()
        examined = len(rows)
        updated = 0

        # Seed the resulting-counts tally with every row this backfill does
        # NOT touch (lifecycle_stage already set), then add in the derived
        # stage for each NULL row below.
        counts = Counter(
            row[0] for row in conn.execute(
                "SELECT lifecycle_stage FROM policies WHERE lifecycle_stage IS NOT NULL"
            )
        )

        for url, raw_text in rows:
            record = json.loads(raw_text)
            stage = _derive_stage(record)
            counts[stage] += 1

            if dry_run:
                print(f"[dry-run] {url}: lifecycle_stage -> {stage!r}")
                continue

            record["lifecycle_stage"] = stage
            conn.execute(
                "UPDATE policies SET lifecycle_stage = ?, raw = ? WHERE url = ?",
                (stage, json.dumps(record, ensure_ascii=False, default=str), url),
            )
            updated += 1

        if not dry_run:
            conn.commit()

        return {"examined": examined, "updated": updated, "counts": dict(counts)}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview changes without writing"
    )
    parser.add_argument(
        "--data-dir", default="data", help="Data directory containing policypulse.db"
    )
    args = parser.parse_args()

    summary = backfill_lifecycle(args.data_dir, dry_run=args.dry_run)

    print(f"Examined {summary['examined']} row(s) with a NULL lifecycle_stage column.")
    if args.dry_run:
        print("Dry run: no changes written.")
    else:
        print(f"Updated {summary['updated']} row(s).")
    print("Resulting lifecycle_stage counts:")
    for stage, count in sorted(summary["counts"].items(), key=lambda kv: (-kv[1], str(kv[0]))):
        print(f"  {stage}: {count}")


if __name__ == "__main__":
    main()
