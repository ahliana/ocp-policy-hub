"""Tests for PolicyStore.update_policy_name_en (WP-35 English titles).

Mirrors the round-trip pattern in tests/unit/test_db.py's
TestMigrationFidelity: a Policy field must survive add_policies -> get_all
untouched, and the update path never clobbers an existing value.
"""

import pytest

from src.core.models import Policy, PolicyType
from src.storage.store import PolicyStore

# Every test here constructs a PolicyStore against tmp_path (a real SQLite
# file on disk), so none of them qualify as "small" (hermetic, no I/O).
pytestmark = pytest.mark.medium


def _policy(url="https://a.gov/1", **overrides):
    defaults = dict(
        url=url,
        policy_name="Energiewendegesetz",
        jurisdiction="Germany",
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


class TestRoundTrip:
    """Same pattern as test_db.py's byte-equal round-trip tests: a Policy
    field set at construction time must come back untouched from the raw
    JSON column."""

    def test_policy_name_en_round_trips_when_set(self, tmp_path):
        store = PolicyStore(data_dir=str(tmp_path))
        store.add_policies([_policy(policy_name_en="Energy Transition Act")])
        record = store.get_all()[0]
        assert record["policy_name_en"] == "Energy Transition Act"

    def test_policy_name_en_round_trips_as_none_when_unset(self, tmp_path):
        store = PolicyStore(data_dir=str(tmp_path))
        store.add_policies([_policy()])
        record = store.get_all()[0]
        assert record["policy_name_en"] is None


class TestUpdatePolicyNameEn:
    def test_sets_value_on_a_policy_missing_it(self, store):
        result = store.update_policy_name_en("https://a.gov/1", "Energy Transition Act")
        assert result is True
        assert store.get_all()[0]["policy_name_en"] == "Energy Transition Act"

    def test_persists_across_reload(self, tmp_path):
        s = PolicyStore(data_dir=str(tmp_path))
        s.add_policies([_policy()])
        s.update_policy_name_en("https://a.gov/1", "Energy Transition Act")
        reloaded = PolicyStore(data_dir=str(tmp_path))
        assert reloaded.get_all()[0]["policy_name_en"] == "Energy Transition Act"

    def test_unknown_url_returns_false(self, store):
        assert store.update_policy_name_en("https://nope.gov", "X") is False

    def test_never_overwrites_an_existing_value(self, store):
        first = store.update_policy_name_en("https://a.gov/1", "Energy Transition Act")
        second = store.update_policy_name_en("https://a.gov/1", "Something Else")
        assert first is True
        assert second is False
        assert store.get_all()[0]["policy_name_en"] == "Energy Transition Act"

    def test_second_call_is_a_no_op_after_reload(self, tmp_path):
        """A second backfill pass over an already-translated store changes nothing."""
        s = PolicyStore(data_dir=str(tmp_path))
        s.add_policies([_policy()])
        s.update_policy_name_en("https://a.gov/1", "Energy Transition Act")

        reloaded = PolicyStore(data_dir=str(tmp_path))
        result = reloaded.update_policy_name_en("https://a.gov/1", "Different Value")
        assert result is False
        assert reloaded.get_all()[0]["policy_name_en"] == "Energy Transition Act"
