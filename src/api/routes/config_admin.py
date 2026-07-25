"""POST /api/config/reload (WP-8) — rebuild config from YAML on disk and
swap it in for subsequent requests, with no process restart.
"""

from fastapi import APIRouter, HTTPException

from ..deps import get_config_version, reload_config
from ...core.config import ConfigurationError

router = APIRouter(prefix="/api/config", tags=["config"])


@router.post("/reload")
def reload_config_route():
    """Rebuild and swap the ConfigLoader singleton (admin-gated by
    AdminGateMiddleware — this is a non-GET /api route).

    A YAML error leaves the previously-loaded config serving traffic and
    returns 422 with the error message instead of a 500 or a half-swapped
    config — see src/api/deps.py:reload_config.
    """
    try:
        config = reload_config()
    except ConfigurationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {
        "reloaded": True,
        "config_version": get_config_version(),
        "domain_count": len(config.domains_config.get("domains", [])),
    }
