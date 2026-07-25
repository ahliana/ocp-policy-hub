"""Admin-settable domain-enabled overrides (WP-8 foundation for WP-9).

Lets an admin turn a domain/source off — or explicitly back on — without
touching ``config/domains/**.yaml``, whose ``enabled:`` flag stays the
single source of truth for the shipped default. The override is applied at
the API/ScanManager boundary (``src/core/overrides.py:apply_domain_overrides``),
never inside ``ConfigLoader`` itself, so the loader stays pure YAML.

Persists to the shared SQLite kv table (see ``src/storage/db.py``), same
store shape as ``src/storage/public_visibility.py``.
"""

import logging
from typing import Optional

from . import db as storage_db

logger = logging.getLogger(__name__)

KV_NAME = "domain_overrides"


class DomainOverridesStore:
    """kv-table persistence for per-domain enabled overrides.

    Shape: ``{domain_id: {"enabled": bool}}``. A domain with no entry has no
    override (YAML decides). ``set_enabled(id, None)`` clears an entry.
    """

    def __init__(self, data_dir: str = "data"):
        self._conn = storage_db.connect(data_dir)
        self._overrides: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        raw = storage_db.kv_get(self._conn, KV_NAME)
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            logger.error("domain_overrides kv payload was not a dict; resetting")
            return {}
        return raw

    def get_all(self) -> dict[str, dict]:
        return dict(self._overrides)

    def get(self, domain_id: str) -> Optional[dict]:
        return self._overrides.get(domain_id)

    def set_enabled(self, domain_id: str, enabled: Optional[bool]) -> None:
        """Set (``enabled`` is a bool) or clear (``enabled`` is None) the
        override for one domain, persisting the whole table each time —
        it's a small blob, same tradeoff as public_visibility.py."""
        if enabled is None:
            self._overrides.pop(domain_id, None)
        else:
            self._overrides[domain_id] = {"enabled": enabled}
        storage_db.kv_set(self._conn, KV_NAME, self._overrides)
