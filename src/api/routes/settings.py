"""Settings endpoints for local .env API key management and cost controls."""

import hmac
import os
import re
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..deps import (
    get_config, get_cost_settings_store, get_public_visibility_store, get_scan_manager,
    request_is_admin,
)
from ...storage.cost_settings import CostSettings
from ...storage.public_visibility import PostureMode, PublicVisibilitySettings

router = APIRouter(prefix="/api/settings", tags=["settings"])

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_PATH = PROJECT_ROOT / ".env"
KEY_NAME = "ANTHROPIC_API_KEY"


class ApiKeyRequest(BaseModel):
    api_key: str


def mask_key(value: str) -> str:
    if len(value) <= 10:
        return "********"
    return f"{value[:7]}...{value[-4:]}"


def read_env_lines() -> list[str]:
    if not ENV_PATH.exists():
        return []
    return ENV_PATH.read_text(encoding="utf-8").splitlines()


def write_env_key(api_key: str) -> None:
    lines = read_env_lines()
    key_pattern = re.compile(rf"^\s*{re.escape(KEY_NAME)}\s*=")
    replaced = False
    next_lines: list[str] = []

    for line in lines:
        if key_pattern.match(line):
            next_lines.append(f"{KEY_NAME}={api_key}")
            replaced = True
        else:
            next_lines.append(line)

    if not replaced:
        if next_lines and next_lines[-1].strip():
            next_lines.append("")
        next_lines.append(f"{KEY_NAME}={api_key}")

    ENV_PATH.write_text("\n".join(next_lines) + "\n", encoding="utf-8")
    os.environ[KEY_NAME] = api_key
    get_scan_manager().api_key = api_key
    load_dotenv(ENV_PATH, override=True)


def delete_env_key() -> bool:
    if not ENV_PATH.exists():
        os.environ.pop(KEY_NAME, None)
        get_scan_manager().api_key = None
        return False

    lines = read_env_lines()
    key_pattern = re.compile(rf"^\s*{re.escape(KEY_NAME)}\s*=")
    next_lines = [line for line in lines if not key_pattern.match(line)]

    if len(next_lines) == len(lines):
        os.environ.pop(KEY_NAME, None)
        get_scan_manager().api_key = None
        return False

    if any(line.strip() for line in next_lines):
        ENV_PATH.write_text("\n".join(next_lines) + "\n", encoding="utf-8")
    else:
        ENV_PATH.unlink()

    os.environ.pop(KEY_NAME, None)
    get_scan_manager().api_key = None
    return True


@router.get("/api-key")
def get_api_key_status(request: Request):
    # Admin-only: even the masked key + "a key is configured" fact should not
    # leak to anonymous callers (matches the /settings/sheet read gate).
    if not request_is_admin(request):
        raise HTTPException(status_code=403, detail="Administrator access required")
    value = os.environ.get(KEY_NAME)

    if not value and ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=True)
        value = os.environ.get(KEY_NAME)

    return {
        "exists": bool(value),
        "masked": mask_key(value) if value else None,
        "env_file_exists": ENV_PATH.exists(),
    }


@router.post("/api-key")
def save_api_key(payload: ApiKeyRequest):
    api_key = payload.api_key.strip()

    if not api_key:
        raise HTTPException(status_code=400, detail="API key cannot be empty")

    if any(character.isspace() for character in api_key):
        raise HTTPException(status_code=400, detail="API key has invalid whitespace")

    write_env_key(api_key)

    return {
        "exists": True,
        "masked": mask_key(api_key),
        "env_file_exists": True,
    }


class CostSettingsUpdate(BaseModel):
    """Partial update — omitted fields keep their current values."""

    cost_level: Optional[Literal["low", "standard", "high"]] = None
    ask_enabled: Optional[bool] = None
    ask_rate_per_minute: Optional[int] = Field(default=None, ge=1, le=60)
    ask_daily_limit: Optional[int] = Field(default=None, ge=0, le=10000)


@router.get("/sheet")
def get_sheet_link(request: Request):
    """Google Sheet link for review ("open Staging"). Admin-only read.

    The middleware only gates mutations, but the sheet id should not leak
    to anonymous readers, so this read checks the admin token itself.
    """
    admin_token = os.environ.get("ADMIN_TOKEN")
    if admin_token:
        provided = request.headers.get("x-admin-token", "")
        if not hmac.compare_digest(provided, admin_token):
            raise HTTPException(status_code=401, detail="Administrator token required")

    sheet_id = os.environ.get("SPREADSHEET_ID", "")
    if not sheet_id:
        return {"configured": False, "url": None}
    return {
        "configured": True,
        "url": f"https://docs.google.com/spreadsheets/d/{sheet_id}",
    }


@router.get("/legiscan-usage")
def get_legiscan_usage():
    """LegiScan monthly query usage against the 30,000/month public cap.

    Read-only and open, so the reader UI can show remaining budget. Returns
    configured=false when no LegiScan key is set.
    """
    if not os.environ.get("LEGISCAN_API_KEY"):
        return {"configured": False}
    from ...sources.legiscan import monthly_usage
    return {"configured": True, **monthly_usage()}


@router.get("/costs")
def get_cost_settings(cost_store=Depends(get_cost_settings_store)):
    """Current cost level, reader-question limits, and resolved models."""
    settings = cost_store.get()
    return {
        **settings.model_dump(),
        "models": cost_store.resolved_models(),
    }


@router.put("/costs")
def update_cost_settings(
    payload: CostSettingsUpdate,
    cost_store=Depends(get_cost_settings_store),
    config=Depends(get_config),
):
    """Update cost controls (admin-gated by the middleware).

    Applies the chosen level's models to the live config immediately,
    so the next scan — API, agent chat, or cron-triggered — runs at
    the new cost level without a restart.
    """
    current = cost_store.get()
    changes = payload.model_dump(exclude_none=True)
    updated = cost_store.update(CostSettings(**{**current.model_dump(), **changes}))
    cost_store.apply_to_config(config)
    return {
        **updated.model_dump(),
        "models": cost_store.resolved_models(),
    }


class PublicVisibilityUpdate(BaseModel):
    mode: PostureMode


@router.get("/public-visibility")
def get_public_visibility(store=Depends(get_public_visibility_store)):
    """Current public review visibility posture (see storage/public_visibility.py)."""
    return {"mode": store.get().mode}


@router.put("/public-visibility")
def update_public_visibility(
    payload: PublicVisibilityUpdate,
    store=Depends(get_public_visibility_store),
):
    """Set the posture (admin-gated by the middleware). Invalid modes 422 via
    the Literal type before this body ever runs."""
    updated = store.update(PublicVisibilitySettings(mode=payload.mode))
    return {"mode": updated.mode}


@router.delete("/api-key")
def remove_api_key():
    deleted = delete_env_key()

    return {
        "deleted": deleted,
        "exists": False,
        "masked": None,
        "env_file_exists": ENV_PATH.exists(),
    }
