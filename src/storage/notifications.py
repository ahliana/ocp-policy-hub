"""Email notification subscriptions and small kv-backed sending state (WP-44).

Subscriptions live in their own typed table (``notification_subscriptions``)
rather than the shared kv blob: CRUD by id plus a UNIQUE constraint on email
both want real SQL rather than load-the-whole-blob-every-write. The sending
state that ``src.notifications.mailer``/``src.notifications.digest`` need
(last digest run per frequency, per-topic immediate-send timestamps for
rate limiting, the last send error, and the last digest run's summary) is
small and answer-shaped, so it stays in the shared kv table - same tradeoff
as ``DomainOverridesStore``/``PublicVisibilityStore``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import db as storage_db

logger = logging.getLogger(__name__)

TOPICS = ("early_signals", "ops_alerts")
FREQUENCIES = ("immediate", "daily", "weekly")

KV_DIGEST_RUNS = "notifications_digest_runs"
KV_IMMEDIATE_SENDS = "notifications_immediate_sends"
KV_SEND_ERROR = "notifications_send_error"
KV_LAST_DIGEST = "notifications_last_digest"


def parse_naive_utc(value: str) -> datetime:
    """Parse an ISO datetime string to a naive UTC datetime, so it can be
    compared directly against ``datetime.utcnow()`` regardless of whether
    the stored value carries a timezone offset (e.g. ``SweepSummary.ts``,
    which uses ``datetime.now(timezone.utc)``) or is already naive UTC (the
    ``datetime.utcnow()`` convention most of this codebase uses).
    """
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class InvalidTopicsError(ValueError):
    """Raised for an empty topics list or a topic outside TOPICS."""


class InvalidFrequencyError(ValueError):
    """Raised for a frequency outside FREQUENCIES."""


class DuplicateEmailError(ValueError):
    """Raised by create() for an email that is already subscribed."""


def _validate_topics(topics: list[str]) -> list[str]:
    if not topics:
        raise InvalidTopicsError("At least one topic must be selected.")
    invalid = sorted(set(topics) - set(TOPICS))
    if invalid:
        raise InvalidTopicsError(
            f"Invalid topic(s): {invalid}. Valid values: {sorted(TOPICS)}"
        )
    return list(topics)


def _validate_frequency(frequency: str) -> str:
    if frequency not in FREQUENCIES:
        raise InvalidFrequencyError(
            f"Invalid frequency {frequency!r}. Valid values: {sorted(FREQUENCIES)}"
        )
    return frequency


def _row_to_dict(row: tuple) -> dict:
    id_, email, topics, frequency, created_at = row
    return {
        "id": id_,
        "email": email,
        "topics": json.loads(topics) if topics else [],
        "frequency": frequency,
        "created_at": created_at,
    }


class NotificationSubscriptionsStore:
    """SQLite-backed CRUD for the ``notification_subscriptions`` table.

    ``topics``/``frequency`` are validated here too (not just at the API
    boundary) so any direct caller - a script, a test, a future non-HTTP
    entry point - gets the same guarantees the route gives an HTTP client.
    """

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self._conn: sqlite3.Connection = storage_db.connect(self.data_dir)

    def create(self, email: str, topics: list[str], frequency: str) -> dict:
        topics = _validate_topics(topics)
        frequency = _validate_frequency(frequency)

        existing = self._conn.execute(
            "SELECT 1 FROM notification_subscriptions WHERE email = ?", (email,)
        ).fetchone()
        if existing is not None:
            raise DuplicateEmailError(f"'{email}' is already subscribed.")

        subscription_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO notification_subscriptions "
            "(id, email, topics, frequency, created_at) VALUES (?, ?, ?, ?, ?)",
            (subscription_id, email, json.dumps(topics), frequency, created_at),
        )
        self._conn.commit()
        return self.get(subscription_id)

    def get(self, subscription_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT id, email, topics, frequency, created_at "
            "FROM notification_subscriptions WHERE id = ?",
            (subscription_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def list(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, email, topics, frequency, created_at "
            "FROM notification_subscriptions ORDER BY created_at, rowid"
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def update(
        self,
        subscription_id: str,
        topics: Optional[list[str]] = None,
        frequency: Optional[str] = None,
    ) -> Optional[dict]:
        """Partial update. ``None`` (the default) for either field means
        "leave unchanged" - raises InvalidTopicsError/InvalidFrequencyError
        for a value that is present but invalid.
        """
        existing = self.get(subscription_id)
        if existing is None:
            return None

        set_clauses: list[str] = []
        params: list = []
        if topics is not None:
            set_clauses.append("topics = ?")
            params.append(json.dumps(_validate_topics(topics)))
        if frequency is not None:
            set_clauses.append("frequency = ?")
            params.append(_validate_frequency(frequency))

        if not set_clauses:
            return existing

        params.append(subscription_id)
        self._conn.execute(
            f"UPDATE notification_subscriptions SET {', '.join(set_clauses)} WHERE id = ?",
            params,
        )
        self._conn.commit()
        return self.get(subscription_id)

    def delete(self, subscription_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM notification_subscriptions WHERE id = ?", (subscription_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0


class NotificationStateStore:
    """kv-table persistence for notification sending state.

    Four small, independently-written records (see the KV_* names above)
    rather than one shared blob - a digest run, an immediate send, and a
    send error can all happen close together and must not clobber each
    other's write.
    """

    def __init__(self, data_dir: str = "data"):
        self._conn = storage_db.connect(data_dir)

    @staticmethod
    def _ts(value) -> str:
        return value.isoformat() if hasattr(value, "isoformat") else value

    def get_digest_last_run(self, frequency: str) -> Optional[str]:
        runs = storage_db.kv_get(self._conn, KV_DIGEST_RUNS) or {}
        return runs.get(frequency)

    def set_digest_last_run(self, frequency: str, ts) -> None:
        runs = storage_db.kv_get(self._conn, KV_DIGEST_RUNS) or {}
        runs[frequency] = self._ts(ts)
        storage_db.kv_set(self._conn, KV_DIGEST_RUNS, runs)

    def get_immediate_last_sent(self, topic: str) -> Optional[str]:
        sends = storage_db.kv_get(self._conn, KV_IMMEDIATE_SENDS) or {}
        return sends.get(topic)

    def set_immediate_last_sent(self, topic: str, ts) -> None:
        sends = storage_db.kv_get(self._conn, KV_IMMEDIATE_SENDS) or {}
        sends[topic] = self._ts(ts)
        storage_db.kv_set(self._conn, KV_IMMEDIATE_SENDS, sends)

    def record_send_error(self, error: str, ts=None) -> None:
        ts = ts or datetime.now(timezone.utc)
        storage_db.kv_set(
            self._conn, KV_SEND_ERROR, {"error": error, "ts": self._ts(ts)}
        )

    def get_last_send_error(self) -> Optional[dict]:
        return storage_db.kv_get(self._conn, KV_SEND_ERROR)

    def record_last_digest(self, record: dict) -> None:
        storage_db.kv_set(self._conn, KV_LAST_DIGEST, record)

    def get_last_digest(self) -> Optional[dict]:
        return storage_db.kv_get(self._conn, KV_LAST_DIGEST)
