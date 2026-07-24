"""GET /api/cost-projection (WP-7) — a funding-conversation number.

Blends the static per-scan ``ScanManager.estimate_cost()`` formula with real
outcomes recorded by ``ScanHistoryStore`` (WP-5) into a monthly/weekly/
quarterly budget figure per scope group.

Blend rule
----------
Once a domain_group has at least **2** completed scan-history runs, its mean
actual cost (``ScanHistoryStore.stats()['mean_cost_usd']``) is a better
predictor of what the next run will cost than the static formula, so
``per_month_usd`` is computed from the mean. Below that threshold (0 or 1
completed runs) there isn't enough signal to trust an average over a single
data point, so the static per-scan estimate is used instead. ``history`` in
the response is still populated whenever there is at least one completed
run — even when the blend itself falls back to the estimate — so a caller
can see "this ran once, here's what it actually cost" rather than nothing.
"""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..deps import get_scan_history_store, get_scan_manager, request_is_admin
from ...core.config import ConfigurationError
from ...orchestration.scan_manager import ScanManager
from ...storage.scan_history import ScanHistoryStore

router = APIRouter(prefix="/api", tags=["cost-projection"])

Cadence = Literal["monthly", "weekly", "quarterly"]

# Average scan runs per calendar month at each cadence. Weekly/quarterly are
# not whole numbers (52/12 and 12/12/3 respectively) since "per month" has to
# average out a schedule that doesn't divide evenly into months.
RUNS_PER_MONTH: dict[str, float] = {
    "monthly": 1.0,
    "weekly": 4.33,
    "quarterly": 1 / 3,
}


def _project_group(group: str, estimate: dict, stats: dict, runs_per_month: float) -> dict:
    """One scope group's row — see the blend rule in the module docstring."""
    has_actuals = stats["runs"] >= 2
    per_run_cost = stats["mean_cost_usd"] if has_actuals else estimate["estimated_cost_usd"]

    history_payload: Optional[dict] = None
    if stats["runs"] > 0:
        history_payload = {
            "runs": stats["runs"],
            "mean_cost_usd": stats["mean_cost_usd"],
            "last_cost_usd": stats["last_cost_usd"],
        }

    return {
        "group": group,
        "estimate_usd": estimate["estimated_cost_usd"],
        "history": history_payload,
        "per_month_usd": round(runs_per_month * per_run_cost, 2),
    }


@router.get("/cost-projection")
def cost_projection(
    request: Request,
    groups: str = Query(..., description="Comma-separated scope strings (groups/regions/ids)"),
    cadence: Cadence = Query("monthly"),
    deep: bool = Query(False),
    manager: ScanManager = Depends(get_scan_manager),
    history: ScanHistoryStore = Depends(get_scan_history_store),
):
    """Per-group cost projection, admin-only.

    404/403 semantics mirror GET /api/policies/library and GET
    /api/scans/history: this is a GET, so AdminGateMiddleware doesn't cover
    it, and the check happens here instead.
    """
    if not request_is_admin(request):
        raise HTTPException(status_code=403, detail="Administrator access required")

    group_list = [g.strip() for g in groups.split(",") if g.strip()]
    if not group_list:
        raise HTTPException(status_code=400, detail="groups must include at least one scope")

    runs_per_month = RUNS_PER_MONTH[cadence]

    items = []
    total_per_month = 0.0
    for group in group_list:
        try:
            estimate = manager.estimate_cost(group, deep=deep)
        except ConfigurationError as e:
            raise HTTPException(status_code=400, detail=str(e))

        stats = history.stats(group)
        item = _project_group(group, estimate, stats, runs_per_month)
        items.append(item)
        total_per_month += item["per_month_usd"]

    return {
        "items": items,
        "cadence": cadence,
        "total_per_month_usd": round(total_per_month, 2),
    }
