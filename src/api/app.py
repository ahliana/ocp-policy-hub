"""FastAPI application — REST API + WebSocket for OCP CE HR Policy Searcher."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from ..core.log_setup import setup_logging
from .deps import get_config_version, get_public_visibility_store, request_is_admin
from .routes import (
    domains, scans, policies, analysis, agent, ask, coverage, cost_projection,
    config_admin, keywords_admin, leads, logs, search, settings, sources_admin,
)
from .static_site import mount_frontend

# Resolve .env from project root (2 levels up from src/api/app.py)
# so credentials load regardless of the process working directory.
_project_root = Path(__file__).resolve().parents[2]
load_dotenv(_project_root / ".env", override=True)

if not os.environ.get("OCP_DATA_DIR"):
    os.environ["OCP_DATA_DIR"] = str(_project_root / "data")

# Structured logging: JSON to file, JSON to console (API/production mode).
# Uses the same unified config as the CLI agent.
data_dir = os.environ["OCP_DATA_DIR"]
setup_logging(data_dir, json_console=True, console_level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    logging.getLogger("ocp").info("OCP CE HR Policy Searcher starting")
    # Apply the admin's saved cost level so scans started after a restart
    # (including cron-triggered ones) run on the chosen models.
    from .deps import get_config, get_cost_settings_store
    get_cost_settings_store().apply_to_config(get_config())
    yield
    logging.getLogger("ocp").info("OCP CE HR Policy Searcher shutting down")


app = FastAPI(
    title="OCP CE HR Policy Searcher",
    description=(
        "API for scanning government websites to discover data center "
        "heat reuse policies. Supports parallel domain scanning, "
        "real-time WebSocket progress, and LLM-powered policy extraction."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


def admin_token_configured() -> bool:
    return bool(os.environ.get("ADMIN_TOKEN"))


# Non-GET routes that stay open when admin mode is active:
# community tip submission and reader questions are the point of the
# reader-facing app. Both have their own rate and daily spend limits
# (/api/tips: TIPS_RATE_PER_MINUTE/TIPS_DAILY_LIMIT; /api/ask: cost settings).
_ADMIN_EXEMPT = {("POST", "/api/tips"), ("POST", "/api/ask")}


class AdminGateMiddleware(BaseHTTPMiddleware):
    """Shared-token gate for state-changing endpoints.

    When ADMIN_TOKEN is set, every non-GET /api request (except explicit
    exemptions) must carry a matching X-Admin-Token header. Reading stays
    open; scanning, chatting, settings, and review actions become
    admin-only — the access model agreed at the 2026-07-07 OCP call.

    When ADMIN_TOKEN is unset, the server is assumed to be a local,
    single-user deployment: non-GET /api requests are only accepted from
    loopback clients. A public deploy that forgot to set ADMIN_TOKEN would
    otherwise let any visitor start paid scans or replace the stored API
    key; a remote caller instead gets a 403 telling the operator to set
    ADMIN_TOKEN.

    The admin/non-admin line itself is ``request_is_admin`` (src/api/deps.py),
    shared with read routes that clamp what a non-admin sees (public review
    visibility) rather than reimplemented here.
    """

    async def dispatch(self, request, call_next):
        if (
            request.url.path.startswith("/api")
            and request.method not in ("GET", "HEAD", "OPTIONS")
            and (request.method, request.url.path) not in _ADMIN_EXEMPT
        ):
            if not request_is_admin(request):
                if os.environ.get("ADMIN_TOKEN"):
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Administrator token required"},
                    )
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": (
                            "This server has no ADMIN_TOKEN configured, so "
                            "admin actions are restricted to local requests. "
                            "Set the ADMIN_TOKEN environment variable to "
                            "allow this action remotely."
                        ),
                    },
                )
        return await call_next(request)


app.add_middleware(AdminGateMiddleware)

# CORS — allow React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register route modules
app.include_router(domains.router)
app.include_router(scans.router)
app.include_router(policies.router)
app.include_router(analysis.router)
app.include_router(agent.router)
app.include_router(ask.router)
app.include_router(coverage.router)
app.include_router(cost_projection.router)
app.include_router(config_admin.router)
app.include_router(sources_admin.router)
app.include_router(keywords_admin.router)
app.include_router(leads.router)
app.include_router(logs.router)
app.include_router(search.router)
app.include_router(settings.router)


@app.get("/")
def root():
    return {
        "service": "OCP CE HR Policy Searcher",
        "version": "1.0.0",
        "docs": "/docs",
        "endpoints": {
            "domains": "/api/domains",
            "scans": "/api/scans",
            "policies": "/api/policies",
            "analyze": "/api/analyze",
            "agent": "/api/agent",
            "tips": "/api/tips",
            "logs": "/api/logs",
        },
    }


@app.get("/health")
def health(visibility_store=Depends(get_public_visibility_store)):
    return {
        "status": "ok",
        "admin_required": admin_token_configured(),
        "public_review_visibility": visibility_store.get().mode,
        "config_version": get_config_version(),
    }


# Serve the built React app (frontend/build) from this same process, if it
# exists. In every current dev/test setup it doesn't, so this is a no-op —
# behavior above is unchanged.
mount_frontend(
    app,
    os.environ.get("OCP_STATIC_DIR", str(_project_root / "frontend" / "build")),
)
