"""Persisted scan schedules (WP-11) - the ``schedules`` table.

An in-app alternative to a server crontab entry: an admin defines a scope
(the same scope string ``ScanManager.start_scan``/``estimate_cost`` already
accept), a set of channels, deep/standard mode, and a simplified cadence,
and ``src.orchestration.schedule_runner`` fires it through the exact same
``ScanManager.start_scan`` path a manual scan uses.

Cadence format
--------------
Deliberately not raw cron - two shapes only, both UTC:

- ``"weekly:<dow>:<HH:MM>"`` - ``dow`` is 0 (Monday) through 6 (Sunday),
  matching Python's ``datetime.weekday()``.
- ``"monthly:<dom>:<HH:MM>"`` - ``dom`` is 1-31. A shorter month clamps to
  its last day (31 in February runs on the 28th, or the 29th in a leap
  year).

:func:`compute_next_run` is a pure function (no I/O) so the cadence math is
unit-testable without a store or database.
"""

import calendar
import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from . import db as storage_db

_WEEKLY_RE = re.compile(r"^weekly:(\d+):(\d+):(\d+)$")
_MONTHLY_RE = re.compile(r"^monthly:(\d+):(\d+):(\d+)$")


class InvalidCadenceError(ValueError):
    """Raised for a cadence string that isn't a valid weekly/monthly form."""


def _clamp_day(year: int, month: int, day: int) -> int:
    last_day = calendar.monthrange(year, month)[1]
    return min(day, last_day)


def _next_weekly(now: datetime, dow: int, hour: int, minute: int) -> datetime:
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    days_ahead = (dow - now.weekday()) % 7
    candidate += timedelta(days=days_ahead)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def _next_monthly(now: datetime, dom: int, hour: int, minute: int) -> datetime:
    year, month = now.year, now.month
    day = _clamp_day(year, month, dom)
    candidate = now.replace(year=year, month=month, day=day, hour=hour, minute=minute,
                            second=0, microsecond=0)
    if candidate <= now:
        month += 1
        if month > 12:
            month = 1
            year += 1
        day = _clamp_day(year, month, dom)
        candidate = candidate.replace(year=year, month=month, day=day)
    return candidate


def compute_next_run(cadence: str, now: datetime) -> datetime:
    """Next UTC run time strictly after ``now`` for a schedule's cadence.

    Raises :class:`InvalidCadenceError` for anything that isn't a
    well-formed ``weekly:<dow>:<HH:MM>`` or ``monthly:<dom>:<HH:MM>``
    string, including out-of-range components (dow > 6, hour > 23, etc).
    """
    weekly_match = _WEEKLY_RE.match(cadence)
    if weekly_match:
        dow, hour, minute = (int(g) for g in weekly_match.groups())
        if not (0 <= dow <= 6 and 0 <= hour <= 23 and 0 <= minute <= 59):
            raise InvalidCadenceError(f"Invalid weekly cadence: {cadence!r}")
        return _next_weekly(now, dow, hour, minute)

    monthly_match = _MONTHLY_RE.match(cadence)
    if monthly_match:
        dom, hour, minute = (int(g) for g in monthly_match.groups())
        if not (1 <= dom <= 31 and 0 <= hour <= 23 and 0 <= minute <= 59):
            raise InvalidCadenceError(f"Invalid monthly cadence: {cadence!r}")
        return _next_monthly(now, dom, hour, minute)

    raise InvalidCadenceError(
        f"Invalid cadence {cadence!r}: expected 'weekly:<dow>:<HH:MM>' or "
        "'monthly:<dom>:<HH:MM>'"
    )


_COLUMNS = (
    "id, name, domains, channels, deep, topic, cadence, enabled, "
    "monthly_ceiling_usd, paused_reason, last_run_at, last_scan_id, "
    "next_run_at, created_at"
)

_EDITABLE_FIELDS = (
    "name", "domains", "channels", "deep", "topic", "cadence", "enabled",
    "monthly_ceiling_usd", "paused_reason",
)


def _row_to_dict(row: tuple) -> dict:
    (
        id_, name, domains, channels, deep, topic, cadence, enabled,
        monthly_ceiling_usd, paused_reason, last_run_at, last_scan_id,
        next_run_at, created_at,
    ) = row
    return {
        "id": id_,
        "name": name,
        "domains": domains,
        "channels": json.loads(channels) if channels else [],
        "deep": bool(deep),
        "topic": topic,
        "cadence": cadence,
        "enabled": bool(enabled),
        "monthly_ceiling_usd": monthly_ceiling_usd,
        "paused_reason": paused_reason,
        "last_run_at": last_run_at,
        "last_scan_id": last_scan_id,
        "next_run_at": next_run_at,
        "created_at": created_at,
    }


class SchedulesStore:
    """SQLite-backed persistence for the ``schedules`` table."""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self._conn: sqlite3.Connection = storage_db.connect(self.data_dir)

    def create(
        self,
        name: str,
        domains: str,
        channels: list[str],
        deep: bool,
        topic: Optional[str],
        cadence: str,
        monthly_ceiling_usd: Optional[float] = None,
    ) -> dict:
        """Insert a new schedule. Raises InvalidCadenceError for a bad cadence."""
        now = datetime.utcnow()
        next_run_at = compute_next_run(cadence, now)
        schedule_id = uuid.uuid4().hex

        self._conn.execute(
            f"INSERT INTO schedules ({_COLUMNS}) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                schedule_id, name, domains, json.dumps(list(channels or [])),
                int(bool(deep)), topic, cadence, 1, monthly_ceiling_usd,
                None, None, None, next_run_at.isoformat(), now.isoformat(),
            ),
        )
        self._conn.commit()
        return self.get(schedule_id)

    def get(self, schedule_id: str) -> Optional[dict]:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM schedules WHERE id = ?", (schedule_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    def list(self) -> list[dict]:
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM schedules ORDER BY created_at, rowid"
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def update(self, schedule_id: str, **fields) -> Optional[dict]:
        """Partial update. Only keys present in ``fields`` are changed.

        Raises InvalidCadenceError if ``cadence`` is provided and invalid.
        Changing ``cadence`` recomputes ``next_run_at`` from now - the old
        next_run_at was computed against the previous cadence and no
        longer means anything once the schedule shape changes.
        """
        existing = self.get(schedule_id)
        if existing is None:
            return None

        unknown = set(fields) - set(_EDITABLE_FIELDS)
        if unknown:
            raise ValueError(f"Unknown schedule field(s): {sorted(unknown)}")

        set_clauses = []
        params: list = []

        if "cadence" in fields:
            next_run_at = compute_next_run(fields["cadence"], datetime.utcnow())
            set_clauses.append("next_run_at = ?")
            params.append(next_run_at.isoformat())

        for field in _EDITABLE_FIELDS:
            if field not in fields:
                continue
            value = fields[field]
            if field == "channels":
                value = json.dumps(list(value or []))
            elif field == "deep":
                value = int(bool(value))
            elif field == "enabled":
                value = int(bool(value))
            set_clauses.append(f"{field} = ?")
            params.append(value)

        if not set_clauses:
            return existing

        params.append(schedule_id)
        self._conn.execute(
            f"UPDATE schedules SET {', '.join(set_clauses)} WHERE id = ?", params,
        )
        self._conn.commit()
        return self.get(schedule_id)

    def delete(self, schedule_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def claim_due(
        self, schedule_id: str, observed_next_run_at: str, new_next_run_at: str,
    ) -> bool:
        """Atomically advance ``next_run_at`` iff it still equals what the
        caller observed - the cross-process guard against duplicate firing.

        When several uvicorn workers each run their own ScheduleRunner (the
        README documents ``--workers``), all of them see the same due
        schedule on a tick. This conditional UPDATE lets exactly one win: the
        first worker's commit moves ``next_run_at`` forward, so every other
        worker's ``WHERE next_run_at = observed`` matches zero rows. Returns
        True only for the worker that should actually fire.
        """
        cur = self._conn.execute(
            "UPDATE schedules SET next_run_at = ? "
            "WHERE id = ? AND next_run_at = ? AND enabled = 1",
            (new_next_run_at, schedule_id, observed_next_run_at),
        )
        self._conn.commit()
        return cur.rowcount == 1

    def set_next_run_at(self, schedule_id: str, next_run_at_iso: str) -> None:
        """Unconditionally set next_run_at. Used by the runner to release a
        claim it took but did not fire (busy/ceiling), restoring the original
        due time so the schedule retries on the next tick. Unconditional is
        safe here: the caller already won claim_due, so it owns the row for
        this tick and no other worker will act on it."""
        self._conn.execute(
            "UPDATE schedules SET next_run_at = ? WHERE id = ?",
            (next_run_at_iso, schedule_id),
        )
        self._conn.commit()

    def mark_ran(
        self, schedule_id: str, scan_id: str, ran_at: datetime, next_run_at: datetime,
    ) -> Optional[dict]:
        """Record a fired run: last_run_at/last_scan_id/next_run_at."""
        self._conn.execute(
            "UPDATE schedules SET last_run_at = ?, last_scan_id = ?, next_run_at = ? "
            "WHERE id = ?",
            (ran_at.isoformat(), scan_id, next_run_at.isoformat(), schedule_id),
        )
        self._conn.commit()
        return self.get(schedule_id)

    def month_spend(self, domains: str, now: datetime) -> float:
        """Sum of completed scans' cost_usd for ``domains`` in ``now``'s
        UTC calendar month - the input to the monthly-ceiling pause check."""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM scans "
            "WHERE domain_group = ? AND status = 'completed' "
            "AND strftime('%Y-%m', completed_at) = strftime('%Y-%m', ?)",
            (domains, now.isoformat()),
        ).fetchone()
        return float(row[0])
