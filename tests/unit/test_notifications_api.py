"""Tests for the /api/notifications routes (WP-44).

GET routes are admin-gated manually (GET requests bypass AdminGateMiddleware
- same pattern as /api/schedules, /api/signals/status). POST/PUT/DELETE are
non-GET, so AdminGateMiddleware covers those automatically; these tests
exercise them in the default (loopback-open) test mode along with their own
validation.
"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.storage.notifications import NotificationStateStore, NotificationSubscriptionsStore


def _mailer(configured: bool = False):
    mailer = MagicMock()
    mailer.smtp_configured = configured
    return mailer


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    from src.api.app import app
    from src.api import deps

    sub_store = NotificationSubscriptionsStore(data_dir=str(tmp_path))
    state_store = NotificationStateStore(data_dir=str(tmp_path))
    mailer = _mailer()

    app.dependency_overrides[deps.get_notification_subscriptions_store] = lambda: sub_store
    app.dependency_overrides[deps.get_notification_state_store] = lambda: state_store
    app.dependency_overrides[deps.get_mailer] = lambda: mailer
    yield {"sub_store": sub_store, "state_store": state_store, "mailer": mailer}
    app.dependency_overrides.clear()


@pytest.fixture
def client(env):
    from src.api.app import app
    with TestClient(app) as c:
        yield c


def _create_body(**overrides):
    body = {"email": "a@example.com", "topics": ["early_signals"], "frequency": "daily"}
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# GET /api/notifications/subscriptions - admin gate + shape
# ---------------------------------------------------------------------------

class TestListAdminGate:
    @pytest.mark.medium
    def test_non_admin_gets_403(self, env, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        from src.api.app import app
        with TestClient(app) as c:
            resp = c.get("/api/notifications/subscriptions")
            assert resp.status_code == 403

    @pytest.mark.medium
    def test_admin_with_token_succeeds(self, env, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        from src.api.app import app
        with TestClient(app) as c:
            resp = c.get(
                "/api/notifications/subscriptions", headers={"X-Admin-Token": "secret"},
            )
            assert resp.status_code == 200

    @pytest.mark.medium
    def test_local_open_mode_counts_as_admin(self, client):
        resp = client.get("/api/notifications/subscriptions")
        assert resp.status_code == 200


class TestListShape:
    @pytest.mark.medium
    def test_empty_list(self, client):
        resp = client.get("/api/notifications/subscriptions")
        assert resp.status_code == 200
        assert resp.json() == {"subscriptions": []}

    @pytest.mark.medium
    def test_lists_created_subscriptions(self, client, env):
        env["sub_store"].create(email="a@example.com", topics=["early_signals"], frequency="daily")
        resp = client.get("/api/notifications/subscriptions")
        rows = resp.json()["subscriptions"]
        assert len(rows) == 1
        assert rows[0]["email"] == "a@example.com"
        assert rows[0]["topics"] == ["early_signals"]
        assert rows[0]["frequency"] == "daily"
        assert "id" in rows[0]


# ---------------------------------------------------------------------------
# POST /api/notifications/subscriptions - create + validation
# ---------------------------------------------------------------------------

class TestCreate:
    @pytest.mark.medium
    def test_creates_subscription(self, client):
        resp = client.post("/api/notifications/subscriptions", json=_create_body())
        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == "a@example.com"
        assert body["topics"] == ["early_signals"]
        assert body["frequency"] == "daily"
        assert "id" in body

    @pytest.mark.medium
    def test_invalid_email_is_422(self, client):
        resp = client.post("/api/notifications/subscriptions", json=_create_body(email="not-an-email"))
        assert resp.status_code == 422

    @pytest.mark.medium
    def test_empty_topics_is_422(self, client):
        resp = client.post("/api/notifications/subscriptions", json=_create_body(topics=[]))
        assert resp.status_code == 422

    @pytest.mark.medium
    def test_unknown_topic_is_422(self, client):
        resp = client.post(
            "/api/notifications/subscriptions", json=_create_body(topics=["not-a-topic"]),
        )
        assert resp.status_code == 422

    @pytest.mark.medium
    def test_unknown_frequency_is_422(self, client):
        resp = client.post("/api/notifications/subscriptions", json=_create_body(frequency="hourly"))
        assert resp.status_code == 422

    @pytest.mark.medium
    def test_duplicate_email_is_400(self, client):
        client.post("/api/notifications/subscriptions", json=_create_body())
        resp = client.post(
            "/api/notifications/subscriptions",
            json=_create_body(topics=["ops_alerts"], frequency="weekly"),
        )
        assert resp.status_code == 400
        assert "a@example.com" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# PUT /api/notifications/subscriptions/{id} - update
# ---------------------------------------------------------------------------

class TestUpdate:
    @pytest.mark.medium
    def test_missing_is_404(self, client):
        resp = client.put("/api/notifications/subscriptions/nonexistent", json={"frequency": "weekly"})
        assert resp.status_code == 404

    @pytest.mark.medium
    def test_partial_update_frequency(self, client):
        created = client.post("/api/notifications/subscriptions", json=_create_body()).json()
        resp = client.put(
            f"/api/notifications/subscriptions/{created['id']}", json={"frequency": "weekly"},
        )
        assert resp.status_code == 200
        assert resp.json()["frequency"] == "weekly"
        assert resp.json()["topics"] == ["early_signals"]

    @pytest.mark.medium
    def test_partial_update_topics(self, client):
        created = client.post("/api/notifications/subscriptions", json=_create_body()).json()
        resp = client.put(
            f"/api/notifications/subscriptions/{created['id']}", json={"topics": ["ops_alerts"]},
        )
        assert resp.status_code == 200
        assert resp.json()["topics"] == ["ops_alerts"]

    @pytest.mark.medium
    def test_empty_topics_is_422(self, client):
        created = client.post("/api/notifications/subscriptions", json=_create_body()).json()
        resp = client.put(f"/api/notifications/subscriptions/{created['id']}", json={"topics": []})
        assert resp.status_code == 422

    @pytest.mark.medium
    def test_unknown_frequency_is_422(self, client):
        created = client.post("/api/notifications/subscriptions", json=_create_body()).json()
        resp = client.put(
            f"/api/notifications/subscriptions/{created['id']}", json={"frequency": "hourly"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/notifications/subscriptions/{id}
# ---------------------------------------------------------------------------

class TestDelete:
    @pytest.mark.medium
    def test_missing_is_404(self, client):
        resp = client.delete("/api/notifications/subscriptions/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.medium
    def test_deletes(self, client):
        created = client.post("/api/notifications/subscriptions", json=_create_body()).json()
        resp = client.delete(f"/api/notifications/subscriptions/{created['id']}")
        assert resp.status_code == 200
        assert client.get("/api/notifications/subscriptions").json()["subscriptions"] == []


# ---------------------------------------------------------------------------
# GET /api/notifications/status
# ---------------------------------------------------------------------------

class TestStatus:
    @pytest.mark.medium
    def test_non_admin_gets_403(self, env, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        from src.api.app import app
        with TestClient(app) as c:
            resp = c.get("/api/notifications/status")
            assert resp.status_code == 403

    @pytest.mark.medium
    def test_unconfigured_shape(self, client):
        resp = client.get("/api/notifications/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"smtp_configured": False, "last_digest": None, "last_send_error": None}

    @pytest.mark.medium
    def test_reflects_configured_mailer(self, client, env):
        env["mailer"].smtp_configured = True
        resp = client.get("/api/notifications/status")
        assert resp.json()["smtp_configured"] is True

    @pytest.mark.medium
    def test_reflects_last_send_error(self, client, env):
        env["state_store"].record_send_error("SMTP auth failed")
        resp = client.get("/api/notifications/status")
        assert resp.json()["last_send_error"] == "SMTP auth failed"

    @pytest.mark.medium
    def test_reflects_last_digest(self, client, env):
        record = {"ts": "2026-01-05T06:30:00", "frequencies": {"daily": {"skipped": "no credentials"}}}
        env["state_store"].record_last_digest(record)
        resp = client.get("/api/notifications/status")
        assert resp.json()["last_digest"] == record
