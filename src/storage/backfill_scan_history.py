"""One-time backfill of ``scans`` rows (WP-5) from historical ``audit.jsonl``.

Run as ``python -m src.storage.backfill_scan_history [--dry-run] [--data-dir data]``.

Historical audit events predate the ``scans`` table: ``scan_started`` events
never recorded ``mode``/``channels`` (see ``src/orchestration/scan_manager.py``),
so backfilled rows have those two columns NULL — only scans that run after
this feature shipped get them populated by ``ScanManager`` directly. A
``scan_started`` event with no matching ``scan_completed`` (the process
crashed, or was cancelled before failure/cancellation had their own audit
events) has no completion data to backfill and is skipped entirely — the
live code path adds a row for those going forward, but there is nothing to
reconstruct for the past.

Idempotent by ``scan_id``: rows already present in the ``scans`` table are
left untouched (``INSERT OR IGNORE``); running the importer twice adds
nothing new the second time.
"""

import argparse
import json
from pathlib import Path

from . import db as storage_db

_INSERT_COLUMNS = (
    "scan_id, domain_group, mode, channels, status, started_at, completed_at, "
    "domains_scanned, policies_found, cost_usd, input_tokens, output_tokens"
)


def _read_audit_events(data_dir: Path) -> list[dict]:
    audit_file = data_dir / "logs" / "audit.jsonl"
    if not audit_file.exists():
        return []

    events = []
    for line in audit_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _pair_events(events: list[dict]) -> list[dict]:
    """Pair scan_started/scan_completed audit events by scan_id into rows.

    Keeps the last occurrence of each event type per scan_id (in case a
    scan_id somehow appears twice), and drops any scan_id with a
    scan_started but no scan_completed — see module docstring.
    """
    started: dict[str, dict] = {}
    completed: dict[str, dict] = {}
    for event in events:
        scan_id = event.get("scan_id")
        if not scan_id:
            continue
        if event.get("event") == "scan_started":
            started[scan_id] = event
        elif event.get("event") == "scan_completed":
            completed[scan_id] = event

    rows = []
    for scan_id, done in completed.items():
        start = started.get(scan_id, {})
        rows.append({
            "scan_id": scan_id,
            "domain_group": done.get("domain_group") or start.get("domain_group") or "",
            "mode": None,
            "channels": None,
            "status": "completed",
            "started_at": start.get("timestamp"),
            "completed_at": done.get("timestamp"),
            "domains_scanned": done.get("domains_scanned"),
            "policies_found": done.get("policies_found"),
            "cost_usd": done.get("cost_usd"),
            "input_tokens": None,
            "output_tokens": None,
        })
    return rows


def backfill(data_dir: str = "data", dry_run: bool = False) -> dict:
    """Import historical scan rows. Returns a summary-counts dict."""
    data_path = Path(data_dir)
    events = _read_audit_events(data_path)
    rows = _pair_events(events)

    conn = storage_db.connect(data_path)
    existing = {row[0] for row in conn.execute("SELECT scan_id FROM scans").fetchall()}
    new_rows = [row for row in rows if row["scan_id"] not in existing]

    if not dry_run:
        for row in new_rows:
            conn.execute(
                f"INSERT OR IGNORE INTO scans ({_INSERT_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["scan_id"], row["domain_group"], row["mode"], row["channels"],
                    row["status"], row["started_at"], row["completed_at"],
                    row["domains_scanned"], row["policies_found"], row["cost_usd"],
                    row["input_tokens"], row["output_tokens"],
                ),
            )
        conn.commit()

    return {
        "audit_events_read": len(events),
        "paired_scans_found": len(rows),
        "already_present": len(rows) - len(new_rows),
        "new_rows": 0 if dry_run else len(new_rows),
        "would_add": len(new_rows) if dry_run else 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill the scans table from historical data/logs/audit.jsonl",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--data-dir", default="data", help="Base data directory")
    args = parser.parse_args()

    result = backfill(data_dir=args.data_dir, dry_run=args.dry_run)
    verb = "Would add" if args.dry_run else "Added"
    count = result["would_add"] if args.dry_run else result["new_rows"]
    print(
        f"Read {result['audit_events_read']} audit event(s); "
        f"found {result['paired_scans_found']} completed scan(s) "
        f"({result['already_present']} already in the scans table). "
        f"{verb} {count} row(s)."
    )


if __name__ == "__main__":
    main()
