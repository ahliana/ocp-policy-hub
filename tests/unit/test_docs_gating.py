"""Tests for gating /docs, /redoc, and /openapi.json behind ADMIN_TOKEN.

When ADMIN_TOKEN is set (production mode), the route map shouldn't be
public. When unset (local/dev), keep the interactive docs. FastAPI bakes
docs_url/openapi_url in at construction time, so exercising both states
means reloading src.api.app with the env var set beforehand - same
pattern as test_static_frontend.py's OCP_STATIC_DIR reload tests.
"""

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_module():
    import src.api.app as module
    yield module


class TestDocsGatedByAdminToken:
    def test_admin_token_set_hides_openapi_and_docs(self, monkeypatch, app_module):
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        importlib.reload(app_module)
        try:
            client = TestClient(app_module.app)
            assert client.get("/openapi.json").status_code == 404
            assert client.get("/docs").status_code == 404
            assert client.get("/redoc").status_code == 404
        finally:
            monkeypatch.delenv("ADMIN_TOKEN", raising=False)
            importlib.reload(app_module)

    def test_admin_token_unset_keeps_docs(self, monkeypatch, app_module):
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        importlib.reload(app_module)
        try:
            client = TestClient(app_module.app)
            assert client.get("/openapi.json").status_code == 200
            assert client.get("/docs").status_code == 200
            assert client.get("/redoc").status_code == 200
        finally:
            importlib.reload(app_module)

    def test_root_listing_regression_still_works_after_reload(self, monkeypatch, app_module):
        """app.openapi() (used by test_api.py's route-listing check) must
        keep working regardless of docs_url/openapi_url - it's a plain
        method call, not a route."""
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        importlib.reload(app_module)
        try:
            assert "/health" in app_module.app.openapi()["paths"]
        finally:
            importlib.reload(app_module)
