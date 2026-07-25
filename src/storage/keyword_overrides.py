"""Admin-settable keyword overlay (WP-10) — additions/removals per
category+language plus optional threshold overrides, layered onto
``config/keywords.yaml`` at KeywordMatcher-construction time (see
``src/core/keywords.py:build_keyword_matcher`` and
``src/core/overrides.py:apply_keyword_overrides``).

``config/keywords.yaml`` itself is never written — it ships inside the
container image, so a write wouldn't survive a redeploy and would diverge
from git. All edits are overlay-only, kv-persisted like
``src/storage/domain_overrides.py``/``src/storage/public_visibility.py``.
"""

import logging

from . import db as storage_db

logger = logging.getLogger(__name__)

KV_NAME = "keyword_overrides"

_EMPTY = {"categories": {}, "thresholds": {}}


class KeywordOverridesStore:
    """kv-table persistence for the keyword overlay.

    Shape::

        {"categories": {category: {language: {"added": [...], "removed": [...]}}},
         "thresholds": {"minimum_keyword_score": float, "minimum_matches": int}}
    """

    def __init__(self, data_dir: str = "data"):
        self._conn = storage_db.connect(data_dir)
        self._overrides: dict = self._load()

    def _load(self) -> dict:
        raw = storage_db.kv_get(self._conn, KV_NAME)
        if raw is None:
            return {"categories": {}, "thresholds": {}}
        if not isinstance(raw, dict):
            logger.error("keyword_overrides kv payload was not a dict; resetting")
            return {"categories": {}, "thresholds": {}}
        return {
            "categories": raw.get("categories", {}),
            "thresholds": raw.get("thresholds", {}),
        }

    def get(self) -> dict:
        return self._overrides

    def update(self, overrides: dict) -> dict:
        """Replace the overlay wholesale — the route validates shape/content
        before calling this, so no re-validation happens here."""
        self._overrides = {
            "categories": overrides.get("categories", {}),
            "thresholds": overrides.get("thresholds", {}),
        }
        storage_db.kv_set(self._conn, KV_NAME, self._overrides)
        return self._overrides

    def clear(self) -> dict:
        return self.update(dict(_EMPTY))
