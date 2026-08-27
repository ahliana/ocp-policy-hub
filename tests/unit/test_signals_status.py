"""Tests for SignalsStatusStore (WP-42/WP-43 sweep-summary persistence)."""

import pytest

from src.storage.signals_status import FeedFailure, SignalsStatusStore, SweepSummary


@pytest.fixture
def store(tmp_path):
    return SignalsStatusStore(data_dir=str(tmp_path))


class TestSignalsStatusStore:
    @pytest.mark.medium
    def test_get_returns_empty_dict_when_absent(self, store):
        assert store.get() == {}

    @pytest.mark.medium
    def test_record_then_get_roundtrips(self, store):
        summary = SweepSummary(
            feeds_tried=3, feeds_ok=2, feeds_failed=1,
            items_found=10, items_kept=4, leads_added=3,
            failures=[FeedFailure(name="feed:DCD", detail="HTTP 404")],
        )
        store.record(summary)
        record = store.get()
        assert record["feeds_tried"] == 3
        assert record["feeds_ok"] == 2
        assert record["feeds_failed"] == 1
        assert record["items_found"] == 10
        assert record["items_kept"] == 4
        assert record["leads_added"] == 3
        assert record["failures"] == [{"name": "feed:DCD", "detail": "HTTP 404"}]
        assert "ts" in record

    @pytest.mark.medium
    def test_record_overwrites_previous_summary(self, store):
        store.record(SweepSummary(feeds_tried=1, feeds_ok=1))
        store.record(SweepSummary(feeds_tried=5, feeds_ok=4, feeds_failed=1))
        record = store.get()
        assert record["feeds_tried"] == 5
        assert record["feeds_failed"] == 1

    @pytest.mark.medium
    def test_persists_across_store_instances(self, tmp_path):
        SignalsStatusStore(data_dir=str(tmp_path)).record(
            SweepSummary(feeds_tried=2, feeds_ok=2, leads_added=1)
        )
        reloaded = SignalsStatusStore(data_dir=str(tmp_path))
        assert reloaded.get()["leads_added"] == 1

    @pytest.mark.medium
    def test_no_failures_defaults_to_empty_list(self, store):
        store.record(SweepSummary(feeds_tried=1, feeds_ok=1))
        assert store.get()["failures"] == []
