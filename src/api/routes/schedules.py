"""In-app scheduled scans (WP-11) - GET/POST/PUT/DELETE /api/schedules,
POST /api/schedules/{id}/run-now.

GET is admin-gated here (a GET, so ``AdminGateMiddleware`` doesn't cover
it - same pattern as GET /api/cost-projection, /api/sources/status,
/api/scans/history). The non-GET routes are covered automatically by that
middleware.

Every schedule row in every response is enriched with a per-run estimate
and an expected per-month cost, computed by reusing
``src.api.routes.cost_projection``'s blend helpers (``RUNS_PER_MONTH``,
``_project_group``) rather than re-implementing the estimate/actuals blend
here - a schedule's cadence type ("weekly"/"monthly") maps directly onto
that module's cadence keys, and its ``domains`` scope string is exactly
what ``ScanHistoryStore.stats()``/``ScanManager.estimate_cost()`` already
key on.
"""

import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .cost_projection import RUNS_PER_MONTH, _project_group
from ..deps import (
    get_config, get_scan_history_store, get_scan_manager, get_schedules_store, request_is_admin,
)
from ...core.config import ConfigLoader, ConfigurationError
from ...core.models import VALID_SCAN_CHANNELS
from ...orchestration.schedule_runner import fire_schedule
from ...orchestration.scan_manager import ScanManager
from ...storage.scan_history import ScanHistoryStore
from ...storage.schedules import SchedulesStore, compute_next_run

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


def _validate_cadence(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    # Raises InvalidCadenceError (a ValueError subclass), which pydantic
    # turns into a 422 automatically.
    compute_next_run(value, datetime.now(timezone.utc))
    return value


def _validate_channels(value: Optional[list[str]]) -> Optional[list[str]]:
    if value is None:
        return value
    # An explicit [] would persist and display as "no channels" but silently
    # scan crawl anyway at fire time - reject it so what's stored matches what
    # runs. (None, meaning "field omitted", still defaults to ["crawl"].)
    if not value:
        raise ValueError("At least one channel must be selected.")
    invalid = sorted(set(value) - VALID_SCAN_CHANNELS)
    if invalid:
        raise ValueError(
            f"Invalid channel(s): {invalid}. Valid values: {sorted(VALID_SCAN_CHANNELS)}"
        )
    return value


class ScheduleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    domains: str = Field(min_length=1)
    channels: list[str] = Field(default_factory=lambda: ["crawl"])
    deep: bool = False
    topic: Optional[str] = None
    cadence: str
    monthly_ceiling_usd: Optional[float] = Field(default=None, ge=0)

    _check_cadence = field_validator("cadence")(_validate_cadence)
    _check_channels = field_validator("channels")(_validate_channels)


class ScheduleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    domains: Optional[str] = Field(default=None, min_length=1)
    channels: Optional[list[str]] = None
    deep: Optional[bool] = None
    topic: Optional[str] = None
    cadence: Optional[str] = None
    enabled: Optional[bool] = None
    monthly_ceiling_usd: Optional[float] = Field(default=None, ge=0)

    _check_cadence = field_validator("cadence")(_validate_cadence)
    _check_channels = field_validator("channels")(_validate_channels)


def _with_cost(schedule: dict, manager: ScanManager, history: ScanHistoryStore) -> dict:
    """One schedule row plus its per-run estimate and expected per-month cost.

    A schedule is scope-validated at creation, but its scope can later become
    unresolvable (a domain/group renamed or removed via a config edit or
    POST /api/config/reload). estimate_cost would then raise
    ConfigurationError; catch it and return null cost for just that row rather
    than 500-ing the whole schedules panel - the admin still needs the list to
    see, edit, or delete the now-broken schedule.
    """
    cadence_type = schedule["cadence"].split(":", 1)[0]
    runs_per_month = RUNS_PER_MONTH.get(cadence_type, RUNS_PER_MONTH["monthly"])

    try:
        estimate = manager.estimate_cost(
            schedule["domains"], deep=schedule["deep"],
            channels=schedule.get("channels") or None,
        )
    except ConfigurationError:
        return {**schedule, "estimate_usd": None, "history": None, "per_month_usd": None}

    stats = history.stats(schedule["domains"])
    projection = _project_group(schedule["domains"], estimate, stats, runs_per_month)

    return {
        **schedule,
        "estimate_usd": projection["estimate_usd"],
        "history": projection["history"],
        "per_month_usd": projection["per_month_usd"],
    }


def _resolve_scope_or_400(config: ConfigLoader, domains: str) -> None:
    try:
        config.get_enabled_domains(domains)
    except ConfigurationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("")
def list_schedules(
    request: Request,
    store: SchedulesStore = Depends(get_schedules_store),
    manager: ScanManager = Depends(get_scan_manager),
    history: ScanHistoryStore = Depends(get_scan_history_store),
):
    if not request_is_admin(request):
        raise HTTPException(status_code=403, detail="Administrator access required")
    return {"schedules": [_with_cost(s, manager, history) for s in store.list()]}


@router.post("")
def create_schedule(
    payload: ScheduleCreate,
    config: ConfigLoader = Depends(get_config),
    store: SchedulesStore = Depends(get_schedules_store),
    manager: ScanManager = Depends(get_scan_manager),
    history: ScanHistoryStore = Depends(get_scan_history_store),
):
    _resolve_scope_or_400(config, payload.domains)

    schedule = store.create(
        name=payload.name,
        domains=payload.domains,
        channels=payload.channels,
        deep=payload.deep,
        topic=payload.topic,
        cadence=payload.cadence,
        monthly_ceiling_usd=payload.monthly_ceiling_usd,
    )
    return _with_cost(schedule, manager, history)


@router.put("/{schedule_id}")
def update_schedule(
    schedule_id: str,
    payload: ScheduleUpdate,
    config: ConfigLoader = Depends(get_config),
    store: SchedulesStore = Depends(get_schedules_store),
    manager: ScanManager = Depends(get_scan_manager),
    history: ScanHistoryStore = Depends(get_scan_history_store),
):
    existing = store.get(schedule_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' not found")

    fields = payload.model_dump(exclude_unset=True)
    if "domains" in fields:
        _resolve_scope_or_400(config, fields["domains"])

    updated = store.update(schedule_id, **fields)
    return _with_cost(updated, manager, history)


@router.delete("/{schedule_id}")
def delete_schedule(
    schedule_id: str,
    store: SchedulesStore = Depends(get_schedules_store),
):
    if not store.delete(schedule_id):
        raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' not found")
    return {"status": "deleted", "id": schedule_id}


@router.post("/{schedule_id}/run-now")
async def run_schedule_now(
    schedule_id: str,
    store: SchedulesStore = Depends(get_schedules_store),
    manager: ScanManager = Depends(get_scan_manager),
    history: ScanHistoryStore = Depends(get_scan_history_store),
):
    """Fire a schedule immediately - the same busy/ceiling-checked path the
    background runner uses (see src/orchestration/schedule_runner.py),
    just triggered by hand instead of by next_run_at."""
    schedule = store.get(schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' not found")

    data_dir = os.environ.get("OCP_DATA_DIR", "data")
    await fire_schedule(manager, store, schedule, data_dir, datetime.utcnow())

    return _with_cost(store.get(schedule_id), manager, history)
