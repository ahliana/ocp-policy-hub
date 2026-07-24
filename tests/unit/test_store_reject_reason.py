"""Tests for PolicyStore.update_review_status's optional ``note`` param —
the reject-reason feature (WP-4 Library). The reason lives only in the raw
JSON as ``review_note`` (no new typed column); it is set when rejecting with
a reason, and cleared whenever the status moves to anything else.
"""

import pytest

from src.core.models import Policy, PolicyType
from src.storage.store import PolicyStore


def _policy(url="https://a.gov/1", **overrides):
    defaults = dict(
        url=url,
        policy_name="Test Policy",
        jurisdiction="US",
        policy_type=PolicyType.LAW,
        summary="s",
        relevance_score=7,
    )
    defaults.update(overrides)
    return Policy(**defaults)


@pytest.fixture
def store(tmp_path):
    s = PolicyStore(data_dir=str(tmp_path))
    s.add_policies([_policy()])
    return s


class TestRejectWithReason:
    def test_reason_stored_in_raw_as_review_note(self, store):
        store.update_review_status(
            "https://a.gov/1", "rejected", note="Duplicate of another entry",
        )
        record = store.get_all()[0]
        assert record["review_status"] == "rejected"
        assert record["review_note"] == "Duplicate of another entry"

    def test_reason_persists_across_reload(self, tmp_path):
        s = PolicyStore(data_dir=str(tmp_path))
        s.add_policies([_policy()])
        s.update_review_status("https://a.gov/1", "rejected", note="Not relevant")
        reloaded = PolicyStore(data_dir=str(tmp_path))
        assert reloaded.get_all()[0]["review_note"] == "Not relevant"

    def test_no_note_provided_leaves_no_review_note(self, store):
        store.update_review_status("https://a.gov/1", "rejected")
        record = store.get_all()[0]
        assert "review_note" not in record or not record["review_note"]


class TestRestoreClearsReason:
    def test_restoring_to_new_clears_review_note(self, store):
        store.update_review_status("https://a.gov/1", "rejected", note="Bad link")
        store.update_review_status("https://a.gov/1", "new")
        record = store.get_all()[0]
        assert "review_note" not in record or not record["review_note"]

    def test_promoting_clears_review_note(self, store):
        store.update_review_status("https://a.gov/1", "rejected", note="Bad link")
        store.update_review_status("https://a.gov/1", "promoted")
        record = store.get_all()[0]
        assert "review_note" not in record or not record["review_note"]


class TestBackwardCompatible:
    def test_no_note_arg_still_works(self, store):
        assert store.update_review_status("https://a.gov/1", "reviewed") is True
        assert store.get_all()[0]["review_status"] == "reviewed"

    def test_unknown_url_returns_false(self, store):
        assert store.update_review_status("https://nope.gov", "rejected", note="x") is False
