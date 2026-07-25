"""One-time seed of the ``schedules`` table (WP-11) with the current
server-crontab equivalents, run as::

    python -m src.storage.seed_schedules [--data-dir data]

Only one job is seeded: **"Monthly full scan"** (domains=all, channels
crawl+law_apis+transposition, ``monthly:1:06:00``, no ceiling) - the
recurring full-scope scan that already runs as a server cron job and maps
directly onto ``ScanManager.start_scan``.

**Out of scope by design:** the other current cron job, a weekly news/
signals sweep, is deliberately *not* seeded here. News runs through its own
runner outside ``ScanManager`` entirely - see
``src/orchestration/scan_manager.py``'s ``_domain_channel``, where
``channels=["news"]`` always resolves to zero domains - so there is no
``ScanManager.start_scan`` call a schedules row could ever fire for it.
Automating that sweep is a separate, out-of-scope feature (a different
runner, not this one).

Idempotent: seeds nothing if the ``schedules`` table already has any row
at all (not just a matching one) - running this against a table an admin
has already started customizing must never silently add a row back.
"""

import argparse

from .schedules import SchedulesStore

MONTHLY_FULL_SCAN = {
    "name": "Monthly full scan",
    "domains": "all",
    "channels": ["crawl", "law_apis", "transposition"],
    "deep": False,
    "topic": None,
    "cadence": "monthly:1:06:00",
    "monthly_ceiling_usd": None,
}


def seed(data_dir: str = "data") -> dict:
    """Insert the seed row(s) if (and only if) the table is currently empty."""
    store = SchedulesStore(data_dir=data_dir)
    if store.list():
        return {"seeded": 0, "skipped_reason": "schedules table is not empty"}

    store.create(**MONTHLY_FULL_SCAN)
    return {"seeded": 1, "skipped_reason": None}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed the schedules table with the current server-crontab equivalents",
    )
    parser.add_argument("--data-dir", default="data", help="Base data directory")
    args = parser.parse_args()

    result = seed(data_dir=args.data_dir)
    if result["seeded"]:
        print(f"Seeded {result['seeded']} schedule(s): {MONTHLY_FULL_SCAN['name']}")
    else:
        print(f"Nothing seeded: {result['skipped_reason']}")


if __name__ == "__main__":
    main()
