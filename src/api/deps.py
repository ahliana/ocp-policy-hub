"""Shared FastAPI dependencies — singletons for config, scan manager, etc."""

import hmac
import os
from functools import lru_cache

from ..core.config import ConfigLoader
from ..orchestration.events import EventBroadcaster
from ..orchestration.scan_manager import ScanManager
from ..storage.store import PolicyStore

# Loopback addresses trusted when ADMIN_TOKEN is unset (see request_is_admin).
LOOPBACK_HOSTS = {"127.0.0.1", "::1"}
# Starlette's TestClient has no real socket and reports its own host as the
# literal string "testclient" — the unit test suite runs with ADMIN_TOKEN
# stripped (see tests/conftest.py) and depends on this counting as trusted.
TESTCLIENT_HOST = "testclient"


def request_is_admin(request) -> bool:
    """Whether this request should be treated as an administrator.

    Mirrors AdminGateMiddleware's non-GET gate (src/api/app.py) so read
    routes that clamp public visibility (policies/coverage) can use the same
    admin/non-admin line. ADMIN_TOKEN set: only a matching X-Admin-Token
    header counts, full stop. ADMIN_TOKEN unset: same loopback-only open-mode
    semantics as the middleware — a forwarded header means the request
    traversed a reverse proxy, so a loopback TCP peer is the proxy itself,
    not the operator, and counts as remote.
    """
    token = os.environ.get("ADMIN_TOKEN")
    if token:
        provided = request.headers.get("x-admin-token", "")
        return hmac.compare_digest(provided, token)

    forwarded = request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip")
    if forwarded:
        return False
    host = request.client.host if request.client else ""
    return host in LOOPBACK_HOSTS or host == TESTCLIENT_HOST


@lru_cache()
def get_config() -> ConfigLoader:
    config_dir = os.environ.get("OCP_CONFIG_DIR", "config")
    config = ConfigLoader(config_dir=config_dir)
    config.load()
    return config


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
def get_scan_manager() -> ScanManager:
    config = get_config()
    broadcaster = get_broadcaster()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    data_dir = os.environ.get("OCP_DATA_DIR", "data")
    return ScanManager(
        config=config,
        broadcaster=broadcaster,
        api_key=api_key,
        data_dir=data_dir,
    )
