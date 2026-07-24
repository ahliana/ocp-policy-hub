"""Admin-settable public review visibility posture (WP-3).

Controls how much of the review pipeline a non-admin (public) reader sees
on the map and policy list before a human has reviewed it. Follows the same
store-class shape as src/storage/cost_settings.py, but persists to the
shared SQLite kv table (src/storage/db.py) rather than its own JSON file —
this setting is a single small blob, exactly what that table already holds
for ask/legiscan usage.

Postures:
- default_all:      readers see everything except rejected finds by
                     default; they can still switch to reviewed-only.
- default_reviewed: readers default to reviewed-only, but can switch.
- reviewed_only:    readers always see reviewed-only; the switch itself
                     is hidden (see src/api/review_visibility.py for the
                     serving-side clamp this drives).
"""

import logging
from typing import Literal

from pydantic import BaseModel

from . import db as storage_db

logger = logging.getLogger(__name__)

KV_NAME = "public_visibility"

PostureMode = Literal["default_all", "default_reviewed", "reviewed_only"]


class PublicVisibilitySettings(BaseModel):
    mode: PostureMode = "default_all"


class PublicVisibilityStore:
    """kv-table persistence for the public review visibility posture."""

    def __init__(self, data_dir: str = "data"):
        self._conn = storage_db.connect(data_dir)
        self._settings = self._load()

    def _load(self) -> PublicVisibilitySettings:
        raw = storage_db.kv_get(self._conn, KV_NAME)
        if raw is None:
            return PublicVisibilitySettings()
        try:
            return PublicVisibilitySettings(**raw)
        except Exception as e:
            logger.error(
                "Failed to load public visibility settings (%s); using defaults", e
            )
            return PublicVisibilitySettings()

    def get(self) -> PublicVisibilitySettings:
        return self._settings

    def update(self, settings: PublicVisibilitySettings) -> PublicVisibilitySettings:
        self._settings = settings
        storage_db.kv_set(self._conn, KV_NAME, settings.model_dump())
        return self._settings
