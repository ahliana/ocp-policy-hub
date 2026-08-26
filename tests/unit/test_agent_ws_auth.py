"""Tests for the agent chat WebSocket's admin gate (/api/agent/ws).

Before this fix, `if admin_token: <check>` meant an unset ADMIN_TOKEN left
the endpoint (which spends ANTHROPIC budget) fully open to any remote
caller - contradicting AdminGateMiddleware's own model, where unset means
loopback-only. The fix reuses deps.is_loopback_client (the same fallback
request_is_admin uses) instead of duplicating the loopback logic.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.api.deps import is_loopback_client

# Policy-violation close code the gate must use on rejection (distinguishes
# a gate rejection from the unrelated "no ANTHROPIC_API_KEY" close below,
# which uses the default code).
POLICY_VIOLATION = 1008


class _Client:
    def __init__(self, host):
        self.host = host


def _connection(host, headers=None):
    return SimpleNamespace(client=_Client(host), headers=headers or {})


class TestIsLoopbackClientPredicate:
    def test_loopback_ipv4_is_loopback(self):
        assert is_loopback_client(_connection("127.0.0.1")) is True

    def test_loopback_ipv6_is_loopback(self):
        assert is_loopback_client(_connection("::1")) is True

    def test_testclient_host_is_loopback(self):
        assert is_loopback_client(_connection("testclient")) is True

    def test_remote_host_is_not_loopback(self):
        assert is_loopback_client(_connection("203.0.113.5")) is False

    def test_forwarded_header_defeats_loopback_peer(self):
        conn = _connection("127.0.0.1", headers={"x-forwarded-for": "203.0.113.5"})
        assert is_loopback_client(conn) is False


class TestAgentWebSocketAdminGate:
    @pytest.fixture(autouse=True)
    def _no_real_api_key(self, monkeypatch):
        # Isolate these tests to the admin gate: without this, a developer's
        # real ANTHROPIC_API_KEY in .env would let a wrongly-accepted
        # connection reach the agent loop and hang waiting for a client
        # message that never arrives.
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def test_tokenless_remote_client_rejected(self, monkeypatch):
        """The fail-open bug: ADMIN_TOKEN unset used to accept ANY remote
        caller. It must now be rejected the same way AdminGateMiddleware
        would reject a non-GET request from this same client - before ever
        reaching the ANTHROPIC_API_KEY check."""
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        from src.api.app import app

        with TestClient(app, client=("203.0.113.5", 12345)) as c:
            with c.websocket_connect("/api/agent/ws") as ws:
                data = ws.receive_json()
                assert data["type"] == "error"
                assert "ADMIN_TOKEN" in data["content"]
                with pytest.raises(WebSocketDisconnect) as exc_info:
                    ws.receive_json()
        assert exc_info.value.code == POLICY_VIOLATION

    def test_tokenless_loopback_client_accepted(self, monkeypatch):
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        from src.api.app import app

        with TestClient(app) as c:
            with c.websocket_connect("/api/agent/ws") as ws:
                data = ws.receive_json()
        # Passed the admin gate; rejected downstream for a different reason.
        assert data == {"type": "error", "content": "ANTHROPIC_API_KEY not set"}

    def test_token_set_missing_token_rejected(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        from src.api.app import app

        with TestClient(app, client=("203.0.113.5", 12345)) as c:
            with c.websocket_connect("/api/agent/ws") as ws:
                data = ws.receive_json()
                assert data == {"type": "error", "content": "Administrator token required"}
                with pytest.raises(WebSocketDisconnect) as exc_info:
                    ws.receive_json()
        assert exc_info.value.code == POLICY_VIOLATION

    def test_token_set_wrong_token_rejected(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        from src.api.app import app

        with TestClient(app, client=("203.0.113.5", 12345)) as c:
            with c.websocket_connect("/api/agent/ws?token=wrong") as ws:
                data = ws.receive_json()
        assert data == {"type": "error", "content": "Administrator token required"}

    def test_token_set_correct_token_accepted_even_remote(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        from src.api.app import app

        with TestClient(app, client=("203.0.113.5", 12345)) as c:
            with c.websocket_connect("/api/agent/ws?token=secret") as ws:
                data = ws.receive_json()
        assert data == {"type": "error", "content": "ANTHROPIC_API_KEY not set"}
