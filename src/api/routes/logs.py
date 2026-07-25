"""Log access endpoints for the React frontend and debugging.

Provides read-only access to structured agent logs and the crash-safe
audit trail.  All log data is returned as JSON arrays — no filesystem
access is needed by the frontend.

Endpoints:
    GET /api/logs         — Recent agent log entries (filterable)
    GET /api/logs/audit   — Audit trail events (scan start/complete, policies)
    GET /api/logs/info    — Log file paths, sizes, and session info
"""

import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from ..deps import request_is_admin
from ...core.log_setup import (
    SESSION_ID,
    get_log_file_paths,
    read_audit_log,
    read_logs,
)

router = APIRouter(prefix="/api/logs", tags=["logs"])


def _require_admin(request: Request) -> None:
    # Logs carry internal diagnostics (session ids, domain config, file
    # paths) that must not reach anonymous callers - same admin gate the
    # other diagnostic GETs use.
    if not request_is_admin(request):
        raise HTTPException(status_code=403, detail="Administrator access required")


@router.get("")
def get_logs(
    request: Request,
    lines: int = Query(50, ge=1, le=500, description="Number of entries to return"),
    level: Optional[str] = Query(
        None, description="Minimum level: debug, info, warning, error"
    ),
    scan_id: Optional[str] = Query(None, description="Filter by scan ID"),
    session_id: Optional[str] = Query(None, description="Filter by session ID"),
):
    _require_admin(request)
    """Return recent structured log entries, newest first.

    Reads from ``data/logs/agent.log`` (JSON-lines format).  Each entry
    includes timestamp, level, event name, and any bound context fields
    (scan_id, domain_id, session_id, etc.).

    Use ``level=warning`` to see only warnings and errors.
    Use ``scan_id`` to isolate logs from a specific scan run.
    """
    data_dir = os.environ.get("OCP_DATA_DIR", "data")
    entries = read_logs(
        data_dir,
        lines=lines,
        level=level,
        scan_id=scan_id,
        session_id=session_id,
    )
    return {"entries": entries, "count": len(entries)}


@router.get("/audit")
def get_audit_logs(
    request: Request,
    lines: int = Query(50, ge=1, le=500, description="Number of entries to return"),
    event_type: Optional[str] = Query(
        None,
        description="Filter by event type (scan_started, scan_completed, policy_found)",
    ),
    scan_id: Optional[str] = Query(None, description="Filter by scan ID"),
):
    _require_admin(request)
    """Return recent audit trail events, newest first.

    The audit log (``data/logs/audit.jsonl``) records critical events:
    scan starts, scan completions, policy discoveries, and cost summaries.
    Each entry is fsync'd to disk for crash safety.

    Use ``event_type=policy_found`` to see only policy discoveries.
    """
    data_dir = os.environ.get("OCP_DATA_DIR", "data")
    entries = read_audit_log(
        data_dir,
        lines=lines,
        event_type=event_type,
        scan_id=scan_id,
    )
    return {"entries": entries, "count": len(entries)}


@router.get("/info")
def get_log_info(request: Request):
    """Return log file paths, sizes, and current session info.

    Useful for the frontend to show log status and for debugging.
    The ``session_id`` identifies this API server process — use it
    to filter logs to just this session.
    """
    _require_admin(request)
    data_dir = os.environ.get("OCP_DATA_DIR", "data")
    paths = get_log_file_paths(data_dir)

    # Add file sizes
    from pathlib import Path

    info = {
        "session_id": SESSION_ID,
        "log_directory": paths["log_directory"],
        "agent_log": None,
        "audit_log": None,
    }

    if paths["agent_log"]:
        agent_path = Path(paths["agent_log"])
        if agent_path.exists():
            info["agent_log"] = {
                "path": paths["agent_log"],
                "size_bytes": agent_path.stat().st_size,
            }

    if paths["audit_log"]:
        audit_path = Path(paths["audit_log"])
        if audit_path.exists():
            info["audit_log"] = {
                "path": paths["audit_log"],
                "size_bytes": audit_path.stat().st_size,
            }

    return info
