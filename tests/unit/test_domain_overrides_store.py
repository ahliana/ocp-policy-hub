"""Tests for the domain-enabled override store (src/storage/domain_overrides.py, WP-8).

Mirrors src/storage/public_visibility.py's store pattern: a small blob in the
shared SQLite kv table, keyed by domain id -> {"enabled": bool}.
"""

from src.storage.domain_overrides import DomainOverridesStore


class TestFreshStore:
    def test_fresh_store_has_no_overrides(self, tmp_path):
        store = DomainOverridesStore(data_dir=str(tmp_path))
        assert store.get_all() == {}

    def test_unknown_domain_get_returns_none(self, tmp_path):
        store = DomainOverridesStore(data_dir=str(tmp_path))
        assert store.get("nope") is None


class TestSetEnabled:
    def test_set_false_persists(self, tmp_path):
        store = DomainOverridesStore(data_dir=str(tmp_path))
        store.set_enabled("dom1", False)
        assert store.get("dom1") == {"enabled": False}
        assert store.get_all() == {"dom1": {"enabled": False}}

    def test_set_true_persists(self, tmp_path):
        store = DomainOverridesStore(data_dir=str(tmp_path))
        store.set_enabled("dom1", True)
        assert store.get("dom1") == {"enabled": True}

    def test_set_none_clears_existing_override(self, tmp_path):
        store = DomainOverridesStore(data_dir=str(tmp_path))
        store.set_enabled("dom1", False)
        store.set_enabled("dom1", None)
        assert store.get("dom1") is None
        assert store.get_all() == {}

    def test_set_none_on_absent_domain_is_a_noop(self, tmp_path):
        store = DomainOverridesStore(data_dir=str(tmp_path))
        store.set_enabled("nope", None)
        assert store.get_all() == {}

    def test_persists_across_fresh_store_instance(self, tmp_path):
        store = DomainOverridesStore(data_dir=str(tmp_path))
        store.set_enabled("dom1", False)

        reloaded = DomainOverridesStore(data_dir=str(tmp_path))
        assert reloaded.get("dom1") == {"enabled": False}

    def test_multiple_domains_independent(self, tmp_path):
        store = DomainOverridesStore(data_dir=str(tmp_path))
        store.set_enabled("dom1", False)
        store.set_enabled("dom2", True)
        assert store.get_all() == {
            "dom1": {"enabled": False},
            "dom2": {"enabled": True},
        }

    def test_stored_in_kv_table_under_expected_name(self, tmp_path):
        from src.storage import db as storage_db

        store = DomainOverridesStore(data_dir=str(tmp_path))
        store.set_enabled("dom1", False)

        conn = storage_db.connect(str(tmp_path))
        raw = storage_db.kv_get(conn, "domain_overrides")
        assert raw == {"dom1": {"enabled": False}}
