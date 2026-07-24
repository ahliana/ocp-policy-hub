"""Tests for PolicyStore pagination/sort/count — the query layer behind the
Library review view (WP-4). Sorting and pagination run in SQL (LIMIT/OFFSET,
ORDER BY), not by slicing the full Python list.
"""

import pytest

from src.core.models import Policy, PolicyType
from src.storage.store import PolicyStore


def _policy(url, name, jurisdiction, score, **overrides):
    defaults = dict(
        url=url,
        policy_name=name,
        jurisdiction=jurisdiction,
        policy_type=PolicyType.LAW,
        summary="s",
        relevance_score=score,
    )
    defaults.update(overrides)
    return Policy(**defaults)


@pytest.fixture
def store(tmp_path):
    s = PolicyStore(data_dir=str(tmp_path))
    s.add_policies([
        _policy("https://a.gov/1", "Beta Policy", "Germany", 3,
                discovered_at="2026-01-01T00:00:00"),
        _policy("https://a.gov/2", "Alpha Policy", "France", 9,
                discovered_at="2026-03-01T00:00:00"),
        _policy("https://a.gov/3", "Charlie Policy", "Austria", 5,
                discovered_at="2026-02-01T00:00:00"),
    ])
    return s


class TestSortByName:
    def test_ascending_default(self, store):
        results = store.search(sort="name")
        assert [p["policy_name"] for p in results] == [
            "Alpha Policy", "Beta Policy", "Charlie Policy",
        ]

    def test_descending(self, store):
        results = store.search(sort="name", sort_dir="desc")
        assert [p["policy_name"] for p in results] == [
            "Charlie Policy", "Beta Policy", "Alpha Policy",
        ]


class TestSortByJurisdiction:
    def test_ascending_default(self, store):
        results = store.search(sort="jurisdiction")
        assert [p["jurisdiction"] for p in results] == ["Austria", "France", "Germany"]


class TestSortByRelevance:
    def test_descending_default(self, store):
        results = store.search(sort="relevance")
        assert [p["relevance_score"] for p in results] == [9, 5, 3]

    def test_ascending(self, store):
        results = store.search(sort="relevance", sort_dir="asc")
        assert [p["relevance_score"] for p in results] == [3, 5, 9]


class TestSortByDiscoveredAt:
    def test_descending_default(self, store):
        results = store.search(sort="discovered_at")
        assert [p["url"] for p in results] == [
            "https://a.gov/2", "https://a.gov/3", "https://a.gov/1",
        ]

    def test_ascending(self, store):
        results = store.search(sort="discovered_at", sort_dir="asc")
        assert [p["url"] for p in results] == [
            "https://a.gov/1", "https://a.gov/3", "https://a.gov/2",
        ]


class TestNoSort:
    def test_defaults_to_insertion_order(self, store):
        results = store.search()
        assert [p["url"] for p in results] == [
            "https://a.gov/1", "https://a.gov/2", "https://a.gov/3",
        ]


class TestLimitOffset:
    def test_limit_only(self, store):
        results = store.search(sort="name", limit=2)
        assert len(results) == 2
        assert [p["policy_name"] for p in results] == ["Alpha Policy", "Beta Policy"]

    def test_limit_and_offset(self, store):
        results = store.search(sort="name", limit=2, offset=1)
        assert [p["policy_name"] for p in results] == ["Beta Policy", "Charlie Policy"]

    def test_offset_past_end_returns_empty(self, store):
        results = store.search(sort="name", limit=25, offset=100)
        assert results == []

    def test_offset_without_limit(self, store):
        results = store.search(sort="name", offset=1)
        assert [p["policy_name"] for p in results] == ["Beta Policy", "Charlie Policy"]


class TestCount:
    def test_count_no_filters(self, store):
        assert store.count() == 3

    def test_count_matches_filtered_search(self, store):
        assert store.count(jurisdiction="germany") == 1

    def test_count_ignores_limit_offset_not_accepted(self, store):
        # count() takes the same filter kwargs as search() minus sort/limit/
        # offset — it always returns the total regardless of any page size.
        assert store.count(min_score=4) == 2

    def test_count_composes_with_review_visibility(self, store):
        assert store.count(exclude_review_status="rejected") == 3
