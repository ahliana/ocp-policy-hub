"""Tests for PATCH /api/policies/review's optional ``reason`` field (WP-4
Library reject-reason) and the audit event it emits.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from src.core.models import Policy, PolicyType
from src.storage.store import PolicyStore


def _policy(url: str, review_status: str = "new") -> Policy:
    return Policy(
        url=url,
        policy_name=f"Policy {url[-1]}",
        jurisdiction="Sweden",
        policy_type=PolicyType.LAW,
        summary="s",
        relevance_score=7,
        review_status=review_status,
    )


@pytest.fixture
def store(tmp_path):
    s = PolicyStore(data_dir=str(tmp_path))
    s.add_policies([_policy("https://a.gov/1", "new")])
    return s


@pytest.fixture
def client(store, tmp_path, monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("OCP_DATA_DIR", str(tmp_path))

    from src.api.app import app
    from src.api import deps

    manager = MagicMock()
    manager.get_all_policies.return_value = []
    app.dependency_overrides[deps.get_policy_store] = lambda: store
    app.dependency_overrides[deps.get_scan_manager] = lambda: manager
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestRejectReason:
    def test_reason_stored_as_review_note(self, client, store):
        resp = client.patch(
            "/api/policies/review",
            json={"url": "https://a.gov/1", "review_status": "rejected", "reason": "Duplicate"},
        )
        assert resp.status_code == 200
        record = {p["url"]: p for p in store.get_all()}["https://a.gov/1"]
        assert record["review_note"] == "Duplicate"

    def test_reason_omitted_is_still_valid(self, client):
        resp = client.patch(
            "/api/policies/review",
            json={"url": "https://a.gov/1", "review_status": "rejected"},
        )
        assert resp.status_code == 200

    def test_restore_clears_reason(self, client, store):
        client.patch(
            "/api/policies/review",
            json={"url": "https://a.gov/1", "review_status": "rejected", "reason": "Duplicate"},
        )
        resp = client.patch(
            "/api/policies/review",
            json={"url": "https://a.gov/1", "review_status": "new"},
        )
        assert resp.status_code == 200
        record = {p["url"]: p for p in store.get_all()}["https://a.gov/1"]
        assert not record.get("review_note")

    def test_reason_over_500_chars_rejected(self, client):
        resp = client.patch(
            "/api/policies/review",
            json={
                "url": "https://a.gov/1",
                "review_status": "rejected",
                "reason": "x" * 501,
            },
        )
        assert resp.status_code == 422

    def test_reason_exactly_500_chars_accepted(self, client):
        resp = client.patch(
            "/api/policies/review",
            json={
                "url": "https://a.gov/1",
                "review_status": "rejected",
                "reason": "x" * 500,
            },
        )
        assert resp.status_code == 200


class TestAuditEvent:
    def test_status_change_logs_audit_event(self, client, tmp_path):
        from src.core.log_setup import read_audit_log

        resp = client.patch(
            "/api/policies/review",
            json={"url": "https://a.gov/1", "review_status": "reviewed"},
        )
        assert resp.status_code == 200
        entries = read_audit_log(data_dir=str(tmp_path), event_type="review_status_changed")
        assert len(entries) == 1
        entry = entries[0]
        assert entry["url"] == "https://a.gov/1"
        assert entry["old_status"] == "new"
        assert entry["new_status"] == "reviewed"
        assert entry["has_reason"] is False

    def test_audit_event_records_reason_presence_not_text(self, client, tmp_path):
        from src.core.log_setup import read_audit_log

        client.patch(
            "/api/policies/review",
            json={
                "url": "https://a.gov/1",
                "review_status": "rejected",
                "reason": "Secret internal note",
            },
        )
        entries = read_audit_log(data_dir=str(tmp_path), event_type="review_status_changed")
        assert len(entries) == 1
        entry = entries[0]
        assert entry["has_reason"] is True
        assert "Secret internal note" not in str(entry)
