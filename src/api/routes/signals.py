"""News-signals sweep status endpoint (WP-43) - an error surface, not a
new pipeline: it just exposes WP-42's persisted sweep summary so an admin
can see "did the last sweep run, and which feeds failed" without reading
log files.
"""

from fastapi import APIRouter, Depends, HTTPException, Request

from ..deps import get_signals_status_store, request_is_admin
from ...storage.signals_status import SignalsStatusStore

router = APIRouter(prefix="/api", tags=["signals"])


@router.get("/signals/status")
def signals_status(
    request: Request,
    store: SignalsStatusStore = Depends(get_signals_status_store),
):
    """The most recent news-signals sweep summary, or {} if none has run yet.

    Admin-only, mirroring GET /api/scans/history: a GET request bypasses
    AdminGateMiddleware (that only gates non-GET requests), so the check
    happens here instead.
    """
    if not request_is_admin(request):
        raise HTTPException(status_code=403, detail="Administrator access required")
    return store.get()
