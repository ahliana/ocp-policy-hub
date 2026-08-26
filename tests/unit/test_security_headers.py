"""Tests for the baseline security-headers middleware (src/api/app.py).

Every response - success or error, any route - should carry a fixed set of
hardening headers. HSTS is normally a Caddy (reverse-proxy) concern; it's
set here too as belt-and-braces in case the app is ever reached directly.
"""

from fastapi.testclient import TestClient


def test_security_headers_present_on_success_response():
    from src.api.app import app

    with TestClient(app) as c:
        resp = c.get("/")

    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert resp.headers["strict-transport-security"] == (
        "max-age=31536000; includeSubDomains"
    )
    csp = resp.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "style-src 'self' 'unsafe-inline'" in csp
    assert "img-src 'self' data:" in csp
    assert "connect-src 'self'" in csp


def test_security_headers_present_on_error_response(monkeypatch):
    """AdminGateMiddleware's short-circuit 403 must carry the same headers -
    it returns a response directly, bypassing the route handlers."""
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    from src.api.app import app

    with TestClient(app, client=("203.0.113.5", 12345)) as c:
        resp = c.patch("/api/policies/review", json={})

    assert resp.status_code == 403
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in resp.headers["content-security-policy"]
