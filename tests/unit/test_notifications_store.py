"""Tests for src.storage.notifications (WP-44): subscription CRUD +
validation, and the small kv-backed sending state.
"""

from datetime import datetime, timezone

import pytest

from src.storage.notifications import (
    DuplicateEmailError, InvalidFrequencyError, InvalidTopicsError,
    NotificationStateStore, NotificationSubscriptionsStore, parse_naive_utc,
)


@pytest.fixture
def store(tmp_path):
    return NotificationSubscriptionsStore(data_dir=str(tmp_path))


@pytest.fixture
def state(tmp_path):
    return NotificationStateStore(data_dir=str(tmp_path))


class TestParseNaiveUtc:
    @pytest.mark.small
    def test_naive_string_passes_through(self):
        assert parse_naive_utc("2026-01-05T06:00:00") == datetime(2026, 1, 5, 6, 0, 0)

    @pytest.mark.small
    def test_aware_string_is_converted_to_naive_utc(self):
        dt = parse_naive_utc("2026-01-05T06:00:00+00:00")
        assert dt == datetime(2026, 1, 5, 6, 0, 0)
        assert dt.tzinfo is None

    @pytest.mark.small
    def test_non_utc_offset_is_normalized(self):
        # +02:00 06:00 local is 04:00 UTC.
        dt = parse_naive_utc("2026-01-05T06:00:00+02:00")
        assert dt == datetime(2026, 1, 5, 4, 0, 0)


class TestSubscriptionsCreate:
    @pytest.mark.medium
    def test_create_returns_full_record(self, store):
        sub = store.create(
            email="a@example.com", topics=["early_signals"], frequency="daily",
        )
        assert sub["email"] == "a@example.com"
        assert sub["topics"] == ["early_signals"]
        assert sub["frequency"] == "daily"
        assert sub["id"]
        assert "created_at" in sub

    @pytest.mark.medium
    def test_duplicate_email_raises(self, store):
        store.create(email="a@example.com", topics=["early_signals"], frequency="daily")
        with pytest.raises(DuplicateEmailError):
            store.create(email="a@example.com", topics=["ops_alerts"], frequency="weekly")

    @pytest.mark.medium
    def test_empty_topics_raises(self, store):
        with pytest.raises(InvalidTopicsError):
            store.create(email="a@example.com", topics=[], frequency="daily")

    @pytest.mark.medium
    def test_unknown_topic_raises(self, store):
        with pytest.raises(InvalidTopicsError):
            store.create(email="a@example.com", topics=["not-a-topic"], frequency="daily")

    @pytest.mark.medium
    def test_unknown_frequency_raises(self, store):
        with pytest.raises(InvalidFrequencyError):
            store.create(email="a@example.com", topics=["early_signals"], frequency="hourly")

    @pytest.mark.medium
    def test_both_topics_accepted(self, store):
        sub = store.create(
            email="a@example.com", topics=["early_signals", "ops_alerts"], frequency="immediate",
        )
        assert sorted(sub["topics"]) == ["early_signals", "ops_alerts"]


class TestSubscriptionsRead:
    @pytest.mark.medium
    def test_get_missing_returns_none(self, store):
        assert store.get("nope") is None

    @pytest.mark.medium
    def test_list_empty(self, store):
        assert store.list() == []

    @pytest.mark.medium
    def test_list_returns_created_subscriptions(self, store):
        store.create(email="a@example.com", topics=["early_signals"], frequency="daily")
        store.create(email="b@example.com", topics=["ops_alerts"], frequency="weekly")
        emails = sorted(s["email"] for s in store.list())
        assert emails == ["a@example.com", "b@example.com"]


class TestSubscriptionsUpdate:
    @pytest.mark.medium
    def test_missing_id_returns_none(self, store):
        assert store.update("nope", frequency="weekly") is None

    @pytest.mark.medium
    def test_update_frequency_only(self, store):
        sub = store.create(email="a@example.com", topics=["early_signals"], frequency="daily")
        updated = store.update(sub["id"], frequency="weekly")
        assert updated["frequency"] == "weekly"
        assert updated["topics"] == ["early_signals"]

    @pytest.mark.medium
    def test_update_topics_only(self, store):
        sub = store.create(email="a@example.com", topics=["early_signals"], frequency="daily")
        updated = store.update(sub["id"], topics=["ops_alerts"])
        assert updated["topics"] == ["ops_alerts"]
        assert updated["frequency"] == "daily"

    @pytest.mark.medium
    def test_update_with_no_fields_is_a_noop(self, store):
        sub = store.create(email="a@example.com", topics=["early_signals"], frequency="daily")
        updated = store.update(sub["id"])
        assert updated == sub

    @pytest.mark.medium
    def test_update_invalid_topics_raises(self, store):
        sub = store.create(email="a@example.com", topics=["early_signals"], frequency="daily")
        with pytest.raises(InvalidTopicsError):
            store.update(sub["id"], topics=[])

    @pytest.mark.medium
    def test_update_invalid_frequency_raises(self, store):
        sub = store.create(email="a@example.com", topics=["early_signals"], frequency="daily")
        with pytest.raises(InvalidFrequencyError):
            store.update(sub["id"], frequency="hourly")


class TestSubscriptionsDelete:
    @pytest.mark.medium
    def test_missing_id_returns_false(self, store):
        assert store.delete("nope") is False

    @pytest.mark.medium
    def test_deletes_existing(self, store):
        sub = store.create(email="a@example.com", topics=["early_signals"], frequency="daily")
        assert store.delete(sub["id"]) is True
        assert store.get(sub["id"]) is None


class TestNotificationStateStore:
    @pytest.mark.medium
    def test_digest_last_run_defaults_to_none(self, state):
        assert state.get_digest_last_run("daily") is None

    @pytest.mark.medium
    def test_digest_last_run_roundtrips_per_frequency(self, state):
        state.set_digest_last_run("daily", datetime(2026, 1, 5, 6, 30))
        state.set_digest_last_run("weekly", datetime(2026, 1, 5, 6, 30))
        assert state.get_digest_last_run("daily") == "2026-01-05T06:30:00"
        assert state.get_digest_last_run("weekly") == "2026-01-05T06:30:00"

    @pytest.mark.medium
    def test_setting_one_frequency_does_not_disturb_the_other(self, state):
        state.set_digest_last_run("daily", datetime(2026, 1, 5, 6, 30))
        state.set_digest_last_run("weekly", datetime(2026, 1, 6, 6, 30))
        assert state.get_digest_last_run("daily") == "2026-01-05T06:30:00"
        assert state.get_digest_last_run("weekly") == "2026-01-06T06:30:00"

    @pytest.mark.medium
    def test_immediate_last_sent_defaults_to_none(self, state):
        assert state.get_immediate_last_sent("ops_alerts") is None

    @pytest.mark.medium
    def test_immediate_last_sent_roundtrips_per_topic(self, state):
        state.set_immediate_last_sent("ops_alerts", datetime(2026, 1, 5, 6, 30))
        state.set_immediate_last_sent("early_signals", datetime(2026, 1, 5, 7, 0))
        assert state.get_immediate_last_sent("ops_alerts") == "2026-01-05T06:30:00"
        assert state.get_immediate_last_sent("early_signals") == "2026-01-05T07:00:00"

    @pytest.mark.medium
    def test_send_error_defaults_to_none(self, state):
        assert state.get_last_send_error() is None

    @pytest.mark.medium
    def test_send_error_roundtrips_with_timestamp(self, state):
        state.record_send_error("boom", ts=datetime(2026, 1, 5, 6, 30, tzinfo=timezone.utc))
        error = state.get_last_send_error()
        assert error["error"] == "boom"
        assert error["ts"] == "2026-01-05T06:30:00+00:00"

    @pytest.mark.medium
    def test_send_error_overwrites_previous(self, state):
        state.record_send_error("first")
        state.record_send_error("second")
        assert state.get_last_send_error()["error"] == "second"

    @pytest.mark.medium
    def test_last_digest_defaults_to_none(self, state):
        assert state.get_last_digest() is None

    @pytest.mark.medium
    def test_last_digest_roundtrips(self, state):
        record = {"ts": "2026-01-05T06:30:00", "frequencies": {"daily": {"skipped": None}}}
        state.record_last_digest(record)
        assert state.get_last_digest() == record

    @pytest.mark.medium
    def test_state_persists_across_store_instances(self, tmp_path):
        NotificationStateStore(data_dir=str(tmp_path)).set_digest_last_run(
            "daily", datetime(2026, 1, 5, 6, 30),
        )
        reloaded = NotificationStateStore(data_dir=str(tmp_path))
        assert reloaded.get_digest_last_run("daily") == "2026-01-05T06:30:00"
