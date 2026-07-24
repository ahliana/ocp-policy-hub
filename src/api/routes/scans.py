"""Scan endpoints — start/stop/status + WebSocket progress."""

import os
from typing import Optional

from fastapi import (
    APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, Query,
)

from ...agent.discovery import build_discovery_prompt
from ...agent.orchestrator import PolicyAgent
from ..deps import (
    get_cost_settings_store, get_scan_manager, get_broadcaster, get_policy_store,
    get_scan_history_store, request_is_admin,
)
from ...core.config import ConfigurationError
from ...core.models import ScanRequest
from ...orchestration.events import EventBroadcaster
from ...orchestration.scan_manager import ScanManager
from ...storage.scan_history import ScanHistoryStore
from ...storage.store import PolicyStore

router = APIRouter(prefix="/api", tags=["scans"])


@router.post("/scans")
async def start_scan(
    request: ScanRequest,
    manager: ScanManager = Depends(get_scan_manager),
    store: PolicyStore = Depends(get_policy_store),
):
    """Start a new parallel scan. Returns immediately with scan_id.

    With discover=true, runs the agent discovery workflow instead and
    returns its result synchronously (scan_id is null).
    """
    if request.discover:
        return await _run_discovery(request)

    manager.api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not request.skip_llm and not manager.api_key:
        raise HTTPException(
            status_code=400,
            detail="ANTHROPIC_API_KEY is not configured. Add an API key or enable skip_llm.",
        )

    job = await manager.start_scan(
        domains_group=request.domains,
        max_concurrent=request.max_concurrent,
        skip_llm=request.skip_llm,
        dry_run=request.dry_run,
        deep=request.deep,
        category=request.category,
        tags=request.tags,
        policy_type=request.policy_type,
        channels=request.channels,
        source_params=request.source_params,
    )
    return {
        "scan_id": job.scan_id,
        "status": job.status.value,
        "domain_count": job.domain_count,
        "options": job.options,
    }


async def _run_discovery(request: ScanRequest):
    """Run the agent discovery workflow used by `python -m src.agent --discover`."""
    country = request.domains.strip()
    if not country:
        raise HTTPException(status_code=400, detail="discover requires a domains/country value")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY environment variable is not set")

    # The admin's cost level decides which model the discovery agent runs on.
    analysis_model = get_cost_settings_store().resolved_models()["analysis_model"]
    agent = PolicyAgent(
        api_key=api_key,
        model=analysis_model,
        config_dir=os.environ.get("OCP_CONFIG_DIR", "config"),
        data_dir=os.environ.get("OCP_DATA_DIR", "data"),
    )

    if request.deep:
        agent.scan_manager.config.settings.crawl.max_depth = 5
        agent.scan_manager.config.settings.crawl.max_pages_per_domain = 500
        agent.scan_manager.config.settings.analysis.min_keyword_score = 2.0

    tools_called: list[str] = []

    def on_tool_call(name: str, input_data: dict):
        tools_called.append(name)

    try:
        response_text = await agent.run(
            build_discovery_prompt(country),
            on_tool_call=on_tool_call,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        await agent.close()

    return {
        "scan_id": None,
        "status": "completed",
        "domain_count": 0,
        "discover": True,
        "deep": request.deep,
        "response": response_text,
        "tools_called": tools_called,
    }


@router.get("/scans")
def list_scans(manager: ScanManager = Depends(get_scan_manager)):
    """List all scans."""
    return [
        {
            "scan_id": job.scan_id,
            "status": job.status.value,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "domain_count": job.domain_count,
            "policy_count": job.policy_count,
        }
        for job in manager.jobs.values()
    ]


@router.get("/scans/history")
def scan_history(
    request: Request,
    domain_group: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    history: ScanHistoryStore = Depends(get_scan_history_store),
):
    """Persisted scan run history (WP-5) — an admin-only review surface.

    Because it's a GET, ``AdminGateMiddleware`` doesn't gate it (that
    middleware only covers non-GET requests), so the admin check happens
    here instead — a non-admin caller gets 403, mirroring
    GET /api/policies/library.

    Declared ahead of GET /scans/{scan_id} so "/api/scans/history" always
    resolves here rather than being captured as scan_id="history".

    Response includes ``total`` (all rows matching domain_group/status,
    ignoring limit/offset) alongside the page — the same shape as
    GET /api/policies/library, so a paginated UI never needs a second
    round trip just to know how many pages there are.
    """
    if not request_is_admin(request):
        raise HTTPException(status_code=403, detail="Administrator access required")
    scans = history.list(domain_group=domain_group, status=status, limit=limit, offset=offset)
    total = history.count(domain_group=domain_group, status=status)
    return {"scans": scans, "total": total, "limit": limit, "offset": offset}


@router.get("/scans/{scan_id}")
def get_scan(
    scan_id: str,
    manager: ScanManager = Depends(get_scan_manager),
):
    """Get detailed scan status including per-domain progress."""
    job = manager.jobs.get(scan_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Scan '{scan_id}' not found")

    policies = manager.get_policies(scan_id)

    return {
        "scan_id": job.scan_id,
        "status": job.status.value,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "domain_count": job.domain_count,
        "policy_count": job.policy_count,
        "progress": {
            "total": job.progress.total_domains,
            "completed": job.progress.completed_domains,
            "running": job.progress.running_domains,
            "domains": [dp.model_dump() for dp in job.progress.domains],
        },
        "policies": [p.model_dump(mode="json") for p in policies],
        "cost": job.cost.model_dump() if job.cost else None,
        "audit_advisory": job.audit_advisory,
    }


@router.delete("/scans/{scan_id}")
async def cancel_scan(
    scan_id: str,
    manager: ScanManager = Depends(get_scan_manager),
):
    """Cancel a running scan."""
    success = await manager.stop_scan(scan_id)
    if success:
        return {"status": "cancelled", "scan_id": scan_id}
    raise HTTPException(status_code=404, detail=f"Scan '{scan_id}' not running or not found")


@router.websocket("/scans/{scan_id}/ws")
async def scan_websocket(
    websocket: WebSocket,
    scan_id: str,
    broadcaster: EventBroadcaster = Depends(get_broadcaster),
):
    """WebSocket endpoint for real-time scan progress."""
    await broadcaster.connect(scan_id, websocket)
    try:
        while True:
            # Keep connection alive, listen for client messages
            await websocket.receive_text()
    except WebSocketDisconnect:
        broadcaster.disconnect(scan_id, websocket)


@router.post("/cost-estimate")
def estimate_cost(
    domains: str = Query("quick"),
    deep: bool = Query(False),
    manager: ScanManager = Depends(get_scan_manager),
):
    """Estimate API costs for a scan."""
    try:
        return manager.estimate_cost(domains, deep=deep)
    except ConfigurationError as e:
        raise HTTPException(status_code=400, detail=str(e))
