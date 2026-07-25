"""GET /api/sources/status and PUT /api/sources/{id}/enabled (WP-9).

Admin-only visibility into every configured domain — the raw YAML
``enabled:`` flag, the WP-8 enabled overlay, the effective (merged) state,
and (for the structured connectors) whether their required API key env var
is set. Reuses ``src.sources.check.source_key_status()`` for key readiness
rather than duplicating that logic — no key values ever leave this module.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..deps import get_config, get_domain_overrides_store, request_is_admin
from ...core.config import ConfigLoader
from ...sources.check import source_key_status
from ...storage.domain_overrides import DomainOverridesStore

router = APIRouter(prefix="/api/sources", tags=["sources"])


def build_source_rows(domains: list[dict], overrides: dict[str, dict]) -> list[dict]:
    """Pure: one row per configured domain, with overlay + key readiness.

    ``domains`` is the *full* domain list (``config.domains_config["domains"]``),
    not the YAML-enabled-only subset ``get_enabled_domains`` returns — the
    admin needs to see (and re-enable) a YAML-disabled domain too.
    """
    key_rows = {row["id"]: row for row in source_key_status()}
    rows = []
    for domain in domains:
        domain_id = domain["id"]
        source_type = domain.get("source_type", "crawl")
        enabled_in_yaml = domain.get("enabled", True)
        override = overrides.get(domain_id, {}).get("enabled")
        effective_enabled = enabled_in_yaml and (override is not False)

        key_status = None
        if source_type != "crawl":
            key_row = key_rows.get(source_type)
            if key_row is not None:
                key_status = {
                    "required_env": key_row["api_key_env"],
                    "configured": key_row["key_present"],
                }

        rows.append({
            "id": domain_id,
            "name": domain.get("name", domain_id),
            "type": source_type,
            "region": domain.get("region", []),
            "enabled_in_yaml": enabled_in_yaml,
            "enabled_override": override,
            "effective_enabled": effective_enabled,
            "key_status": key_status,
        })
    return rows


@router.get("/status")
def get_sources_status(
    request: Request,
    config: ConfigLoader = Depends(get_config),
    overrides_store: DomainOverridesStore = Depends(get_domain_overrides_store),
):
    """Every configured domain/source, admin-only.

    This is a GET, so AdminGateMiddleware doesn't cover it (mirrors GET
    /api/policies/library and GET /api/cost-projection) — checked here.
    """
    if not request_is_admin(request):
        raise HTTPException(status_code=403, detail="Administrator access required")

    domains = config.domains_config.get("domains", [])
    rows = build_source_rows(domains, overrides_store.get_all())
    return {"sources": rows, "count": len(rows)}


class SourceEnabledUpdate(BaseModel):
    enabled: Optional[bool] = None


@router.put("/{domain_id}/enabled")
def update_source_enabled(
    domain_id: str,
    payload: SourceEnabledUpdate,
    config: ConfigLoader = Depends(get_config),
    overrides_store: DomainOverridesStore = Depends(get_domain_overrides_store),
):
    """Set (``enabled`` true/false) or clear (``enabled`` null) this domain's
    override. Admin-gated by AdminGateMiddleware (non-GET /api route)."""
    known_ids = {d["id"] for d in config.domains_config.get("domains", [])}
    if domain_id not in known_ids:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_id}' not found")

    overrides_store.set_enabled(domain_id, payload.enabled)
    return {"id": domain_id, "enabled_override": payload.enabled}
