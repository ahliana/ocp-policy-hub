"""Shared FastAPI dependencies - singletons for config, scan manager, etc."""

import hmac
import os
from functools import lru_cache

import yaml

from ..core.config import ConfigLoader, ConfigurationError
from ..orchestration.events import EventBroadcaster
from ..orchestration.scan_manager import ScanManager
from ..storage.store import PolicyStore

# Loopback addresses trusted when ADMIN_TOKEN is unset (see request_is_admin).
LOOPBACK_HOSTS = {"127.0.0.1", "::1"}
# Starlette's TestClient has no real socket and reports its own host as the
# literal string "testclient" - the unit test suite runs with ADMIN_TOKEN
# stripped (see tests/conftest.py) and depends on this counting as trusted.
TESTCLIENT_HOST = "testclient"


def is_loopback_client(connection) -> bool:
    """Loopback-only fallback shared by request_is_admin and any other
    ADMIN_TOKEN-unset gate (currently: the agent chat WebSocket).

    ``connection`` is anything exposing Starlette's ``.headers``/``.client``
    shape - both ``Request`` and ``WebSocket`` qualify. A forwarded header
    means the request traversed a reverse proxy, so a loopback TCP peer is
    the proxy itself, not the operator, and counts as remote.
    """
    forwarded = connection.headers.get("x-forwarded-for") or connection.headers.get("x-real-ip")
    if forwarded:
        return False
    host = connection.client.host if connection.client else ""
    return host in LOOPBACK_HOSTS or host == TESTCLIENT_HOST


def request_is_admin(request) -> bool:
    """Whether this request should be treated as an administrator.

    Mirrors AdminGateMiddleware's non-GET gate (src/api/app.py) so read
    routes that clamp public visibility (policies/coverage) can use the same
    admin/non-admin line. ADMIN_TOKEN set: only a matching X-Admin-Token
    header counts, full stop. ADMIN_TOKEN unset: same loopback-only open-mode
    semantics as the middleware - see is_loopback_client.
    """
    token = os.environ.get("ADMIN_TOKEN")
    if token:
        provided = request.headers.get("x-admin-token", "")
        return hmac.compare_digest(provided, token)

    return is_loopback_client(request)


# get_config()/get_scan_manager() used to be plain @lru_cache singletons.
# WP-8 needs to rebuild the config from disk at runtime (POST
# /api/config/reload) and swap it in atomically, so both are now a
# module-level holder dict instead - same "build once, reuse" behavior, but
# swappable. ScanManager.config is a plain attribute (no property needed): a
# successful reload also reassigns it on the already-built ScanManager
# singleton, since ScanManager captured the pre-reload ConfigLoader instance
# at construction and would otherwise keep serving stale config forever.
_config_state: dict = {"instance": None, "version": 0}
_scan_manager_state: dict = {"instance": None}


def _build_config() -> ConfigLoader:
    config_dir = os.environ.get("OCP_CONFIG_DIR", "config")
    config = ConfigLoader(config_dir=config_dir)
    try:
        config.load()
    except yaml.YAMLError as e:
        # ConfigLoader wraps some load errors (e.g. a broken domains/*.yaml
        # file) in ConfigurationError already, but a malformed settings.yaml
        # or keywords.yaml raises a bare YAMLError - normalize both to
        # ConfigurationError here so reload_config()'s caller (the
        # /api/config/reload route) has exactly one exception type to catch.
        raise ConfigurationError(f"Invalid YAML in config: {e}") from e
    return config


def get_config() -> ConfigLoader:
    if _config_state["instance"] is None:
        _config_state["instance"] = _build_config()
        _config_state["version"] += 1
    return _config_state["instance"]


def get_config_version() -> int:
    """Monotonically increasing counter, bumped on every successful build or
    reload - lets GET /health show staleness."""
    return _config_state["version"]


def reload_config() -> ConfigLoader:
    """Rebuild ``ConfigLoader`` from YAML on disk and swap it in.

    Raises ``ConfigurationError`` (propagated, uncaught) on a YAML error -
    the previous config is left untouched and keeps serving; the version
    counter is not bumped. On success, the new instance becomes what
    ``get_config()`` returns, the version bumps by one, and the already-built
    ``ScanManager`` singleton (if any) has its ``config`` attribute pointed
    at the new instance so a scan started right after a reload sees fresh
    YAML with no process restart.
    """
    new_config = _build_config()
    _config_state["instance"] = new_config
    _config_state["version"] += 1

    manager = _scan_manager_state["instance"]
    if manager is not None:
        manager.config = new_config

    return new_config


@lru_cache()
def get_broadcaster() -> EventBroadcaster:
    return EventBroadcaster()


@lru_cache()
def get_policy_store() -> PolicyStore:
    data_dir = os.environ.get("OCP_DATA_DIR", "data")
    return PolicyStore(data_dir=data_dir)


@lru_cache()
def get_lead_store():
    from ..storage.leads import LeadStore
    data_dir = os.environ.get("OCP_DATA_DIR", "data")
    return LeadStore(data_dir=data_dir)


@lru_cache()
def get_cost_settings_store():
    from ..storage.cost_settings import CostSettingsStore
    data_dir = os.environ.get("OCP_DATA_DIR", "data")
    return CostSettingsStore(data_dir=data_dir)


@lru_cache()
def get_public_visibility_store():
    from ..storage.public_visibility import PublicVisibilityStore
    data_dir = os.environ.get("OCP_DATA_DIR", "data")
    return PublicVisibilityStore(data_dir=data_dir)


@lru_cache()
def get_scan_history_store():
    from ..storage.scan_history import ScanHistoryStore
    data_dir = os.environ.get("OCP_DATA_DIR", "data")
    return ScanHistoryStore(data_dir=data_dir)


@lru_cache()
def get_domain_overrides_store():
    from ..storage.domain_overrides import DomainOverridesStore
    data_dir = os.environ.get("OCP_DATA_DIR", "data")
    return DomainOverridesStore(data_dir=data_dir)


@lru_cache()
def get_keyword_overrides_store():
    from ..storage.keyword_overrides import KeywordOverridesStore
    data_dir = os.environ.get("OCP_DATA_DIR", "data")
    return KeywordOverridesStore(data_dir=data_dir)


@lru_cache()
def get_schedules_store():
    from ..storage.schedules import SchedulesStore
    data_dir = os.environ.get("OCP_DATA_DIR", "data")
    return SchedulesStore(data_dir=data_dir)


def get_scan_manager() -> ScanManager:
    if _scan_manager_state["instance"] is None:
        config = get_config()
        broadcaster = get_broadcaster()
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        data_dir = os.environ.get("OCP_DATA_DIR", "data")
        _scan_manager_state["instance"] = ScanManager(
            config=config,
            broadcaster=broadcaster,
            api_key=api_key,
            data_dir=data_dir,
            domain_overrides_store=get_domain_overrides_store(),
        )
    return _scan_manager_state["instance"]
