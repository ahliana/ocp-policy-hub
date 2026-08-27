"""Persisted record of the most recent news-signals sweep (WP-42/WP-43).

The news sweep (``src.signals.news.run_news_signals``) runs unattended on a
schedule with no one watching its logs. WP-43's "Last sweep" surface in the
Tips inbox needs somewhere to read that state back from without parsing log
files, so the sweep also writes one small summary record here after every
run - same store-class-over-the-shared-kv-table shape as
``PublicVisibilityStore``/``DomainOverridesStore`` (see ``src/storage/db.py``
for the kv table itself).
"""

import logging
import sqlite3
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from . import db as storage_db

logger = logging.getLogger(__name__)

KV_NAME = "signals_last_sweep"


class FeedFailure(BaseModel):
    """One feed/query that failed during a sweep, with a reason an admin
    can act on (fix the URL, or disable the feed) - WP-43's user-correctable
    error class."""

    name: str
    detail: str


class SweepSummary(BaseModel):
    ts: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    feeds_tried: int = 0
    feeds_ok: int = 0
    feeds_failed: int = 0
    items_found: int = 0
    items_kept: int = 0
    leads_added: int = 0
    failures: list[FeedFailure] = Field(default_factory=list)


class SignalsStatusStore:
    """kv-table persistence for the latest news-sweep summary."""

    def __init__(self, data_dir: str = "data"):
        self._conn = storage_db.connect(data_dir)

    def record(self, summary: SweepSummary) -> None:
        """Persist a sweep summary. Never raises - a failure to persist the
        status record must not fail the sweep that already succeeded at its
        real job (collecting and storing leads)."""
        try:
            storage_db.kv_set(self._conn, KV_NAME, summary.model_dump())
        except sqlite3.Error as e:
            logger.exception("Failed to persist signals sweep summary: %s", e)

    def get(self) -> dict:
        """The most recent sweep summary, or {} if no sweep has ever run."""
        return storage_db.kv_get(self._conn, KV_NAME) or {}
