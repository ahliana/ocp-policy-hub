"""Digest assembly and the scheduled digest job (WP-44).

``assemble_digest`` builds one topic's plain-language email body from the
stores that already hold the underlying facts - it has no email-sending
knowledge of its own. ``run_digest_tick``/``run_digest_tick_for_data_dir``
are the small function the schedule runner's tick calls: almost every tick
it is a no-op (see ``_daily_due``/``_weekly_due``), and only actually
assembles and sends once a day (06:30 UTC) and once a week (Monday 06:30
UTC) - gated by kv last-run state (``NotificationStateStore``), not new
schedule rows, so there is nothing here for an admin to configure or
accidentally break.
"""

import logging
import sqlite3
from datetime import datetime, time, timedelta
from typing import Optional

from ..storage.notifications import (
    NotificationStateStore, NotificationSubscriptionsStore, parse_naive_utc,
)
from ..storage.scan_history import ScanHistoryStore
from ..storage.signals_status import SignalsStatusStore
from ..storage.store import PolicyStore
from .mailer import Mailer

logger = logging.getLogger(__name__)

TOPICS = ("early_signals", "ops_alerts")

# "New finds worth an early look" (WP-44 design): the lifecycle stages a
# policy sits in before it is settled law - see core.models.LIFECYCLE_STAGES
# for the full set this is a subset of.
EARLY_LIFECYCLE_STAGES = ("proposed", "consultation", "in_committee")

_DIGEST_TIME = time(6, 30)
_WEEKLY_WEEKDAY = 0  # Monday, matching datetime.weekday()

_BOOTSTRAP_WINDOW = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}


def _daily_due(last_run: Optional[str], now: datetime) -> bool:
    """Due once a day: at/after 06:30 UTC, and not already run today."""
    if now.time() < _DIGEST_TIME:
        return False
    if last_run is None:
        return True
    return parse_naive_utc(last_run).date() < now.date()


def _weekly_due(last_run: Optional[str], now: datetime) -> bool:
    """Due once a week: Monday, at/after 06:30 UTC, not already run today."""
    if now.weekday() != _WEEKLY_WEEKDAY or now.time() < _DIGEST_TIME:
        return False
    if last_run is None:
        return True
    return parse_naive_utc(last_run).date() < now.date()


_DUE_CHECKS = {"daily": _daily_due, "weekly": _weekly_due}


def _since_for(frequency: str, last_run: Optional[str], now: datetime) -> datetime:
    if last_run is not None:
        return parse_naive_utc(last_run)
    return now - _BOOTSTRAP_WINDOW[frequency]


def _early_signals_digest(policy_store: PolicyStore, since: datetime) -> Optional[dict]:
    finds = []
    for policy in policy_store.get_all():
        if policy.get("review_status") != "new":
            continue
        if policy.get("lifecycle_stage") not in EARLY_LIFECYCLE_STAGES:
            continue
        discovered_at = policy.get("discovered_at")
        if not discovered_at or parse_naive_utc(discovered_at) < since:
            continue
        finds.append(policy)
    if not finds:
        return None

    lines = [f"{len(finds)} new policy find(s) worth an early look:", ""]
    for policy in finds:
        lines.append(
            f"- {policy.get('policy_name') or 'Untitled'} "
            f"({policy.get('jurisdiction') or 'unknown jurisdiction'}) - "
            f"{policy.get('lifecycle_stage')}"
        )
    lines += ["", "Open the admin page to act on these."]
    return {
        "subject": f"PolicyPulse: {len(finds)} new policy find(s) to review",
        "body": "\n".join(lines),
    }


def _ops_alerts_digest(
    signals_status_store: SignalsStatusStore,
    scan_history_store: ScanHistoryStore,
    since: datetime,
) -> Optional[dict]:
    lines: list[str] = []
    total = 0

    sweep = signals_status_store.get()
    sweep_ts = sweep.get("ts") if sweep else None
    if (
        sweep and sweep.get("feeds_failed", 0) > 0
        and sweep_ts and parse_naive_utc(sweep_ts) >= since
    ):
        total += sweep["feeds_failed"]
        lines.append(f"The news sweep had {sweep['feeds_failed']} feed failure(s):")
        for failure in sweep.get("failures", []):
            lines.append(f"  - {failure.get('name')}: {failure.get('detail')}")
        lines.append("")

    failed = [
        s for s in scan_history_store.list(status="failed", limit=200)
        if s.get("completed_at") and parse_naive_utc(s["completed_at"]) >= since
    ]
    if failed:
        total += len(failed)
        lines.append(f"{len(failed)} scan(s) failed:")
        lines += [f"  - {s['domain_group']} (scan {s['scan_id']})" for s in failed]
        lines.append("")

    budget_capped = [
        s for s in scan_history_store.list(status="completed_budget_reached", limit=200)
        if s.get("completed_at") and parse_naive_utc(s["completed_at"]) >= since
    ]
    if budget_capped:
        total += len(budget_capped)
        lines.append(f"{len(budget_capped)} scan(s) stopped early after reaching their budget:")
        lines += [f"  - {s['domain_group']} (scan {s['scan_id']})" for s in budget_capped]
        lines.append("")

    if total == 0:
        return None

    lines.append("Open the admin page to act on these.")
    return {
        "subject": f"PolicyPulse: {total} operational alert(s)",
        "body": "\n".join(lines),
    }


def assemble_digest(
    topic: str,
    since: datetime,
    policy_store: PolicyStore,
    signals_status_store: SignalsStatusStore,
    scan_history_store: ScanHistoryStore,
) -> Optional[dict]:
    """One topic's digest since ``since``, or None when there is nothing to
    report (an empty digest is never sent).

    - "early_signals": new (review_status="new") finds in an early
      lifecycle stage (EARLY_LIFECYCLE_STAGES), discovered since ``since``.
    - "ops_alerts": the last news-sweep summary's feed failures (if within
      the window) plus scans that failed or stopped early on budget since
      ``since``.
    """
    if topic == "early_signals":
        return _early_signals_digest(policy_store, since)
    if topic == "ops_alerts":
        return _ops_alerts_digest(signals_status_store, scan_history_store, since)
    raise ValueError(f"Unknown notification topic: {topic!r}")


def _process_frequency(
    frequency: str,
    since: datetime,
    subscriptions_store: NotificationSubscriptionsStore,
    mailer: Mailer,
    policy_store: PolicyStore,
    signals_status_store: SignalsStatusStore,
    scan_history_store: ScanHistoryStore,
) -> dict:
    if not mailer.smtp_configured:
        return {"topics_sent": {}, "skipped": "no credentials"}

    subscribers = [s for s in subscriptions_store.list() if s["frequency"] == frequency]
    topics_sent: dict[str, int] = {}
    for topic in TOPICS:
        digest = assemble_digest(
            topic, since, policy_store, signals_status_store, scan_history_store,
        )
        if digest is None:
            continue
        recipients = [s["email"] for s in subscribers if topic in s["topics"]]
        if not recipients:
            continue
        if mailer.send(recipients, digest["subject"], digest["body"]):
            topics_sent[topic] = len(recipients)
    return {"topics_sent": topics_sent, "skipped": None}


def run_digest_tick(
    subscriptions_store: NotificationSubscriptionsStore,
    state_store: NotificationStateStore,
    mailer: Mailer,
    policy_store: PolicyStore,
    signals_status_store: SignalsStatusStore,
    scan_history_store: ScanHistoryStore,
    now: Optional[datetime] = None,
) -> dict:
    """One schedule-runner tick's worth of digest work for both frequencies.

    Almost every tick this is a no-op for both - see ``_daily_due``/
    ``_weekly_due``. Returns ``{frequency: {"topics_sent": ..., "skipped":
    ...}}`` for whichever frequency(ies) actually ran this tick (empty dict
    most ticks); the same payload is written to
    ``state_store.record_last_digest`` under a single "ts"/"frequencies"
    record so GET /api/notifications/status always reflects the most recent
    tick that did anything, without one frequency's write clobbering the
    other's when both are due at once (Monday 06:30 UTC).
    """
    now = now or datetime.utcnow()
    fired: dict[str, dict] = {}

    for frequency, due in _DUE_CHECKS.items():
        last_run = state_store.get_digest_last_run(frequency)
        if not due(last_run, now):
            continue
        since = _since_for(frequency, last_run, now)
        fired[frequency] = _process_frequency(
            frequency, since, subscriptions_store, mailer,
            policy_store, signals_status_store, scan_history_store,
        )
        state_store.set_digest_last_run(frequency, now)

    if fired:
        state_store.record_last_digest({"ts": now.isoformat(), "frequencies": fired})

    return fired


def run_digest_tick_for_data_dir(
    data_dir: str = "data",
    config_dir: str = "config",
    now: Optional[datetime] = None,
) -> None:
    """Real-wiring entry point: constructs every store fresh and delegates
    to ``run_digest_tick``. This is the "small function the tick calls" -
    ``src/orchestration/schedule_runner.py``'s loop calls this once per
    tick. Never raises - one broken digest run must not take the schedule
    runner down with it (same contract as ``run_due_schedules``).
    """
    try:
        run_digest_tick(
            NotificationSubscriptionsStore(data_dir=data_dir),
            NotificationStateStore(data_dir=data_dir),
            Mailer(config_dir=config_dir, data_dir=data_dir),
            PolicyStore(data_dir=data_dir),
            SignalsStatusStore(data_dir=data_dir),
            ScanHistoryStore(data_dir=data_dir),
            now=now,
        )
    except (sqlite3.Error, OSError, ValueError, KeyError) as e:
        # The realistic failure classes: store reads/writes (sqlite3.Error),
        # config file access (OSError), malformed config or state values
        # (ValueError/KeyError). The mailer swallows its own SMTP errors.
        logger.error("Notification digest tick failed: %s", e)
