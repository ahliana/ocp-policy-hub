"""Tests for the keyword overlay store (src/storage/keyword_overrides.py, WP-10).

Mirrors src/storage/domain_overrides.py's store pattern: a small blob in the
shared SQLite kv table. Shape:
{"categories": {category: {language: {"added": [...], "removed": [...]}}},
 "thresholds": {"minimum_keyword_score": float, "minimum_matches": int}}
"""

from src.storage.keyword_overrides import KeywordOverridesStore


class TestFreshStore:
    def test_fresh_store_has_empty_overlay(self, tmp_path):
        store = KeywordOverridesStore(data_dir=str(tmp_path))
        assert store.get() == {"categories": {}, "thresholds": {}}


class TestUpdate:
    def test_update_persists_categories(self, tmp_path):
        store = KeywordOverridesStore(data_dir=str(tmp_path))
        overrides = {
            "categories": {"subject": {"en": {"added": ["heat pump"], "removed": []}}},
            "thresholds": {},
        }
        result = store.update(overrides)
        assert result == overrides
        assert store.get() == overrides

    def test_update_persists_thresholds(self, tmp_path):
        store = KeywordOverridesStore(data_dir=str(tmp_path))
        overrides = {"categories": {}, "thresholds": {"minimum_keyword_score": 4.0}}
        store.update(overrides)
        assert store.get()["thresholds"] == {"minimum_keyword_score": 4.0}

    def test_update_with_empty_dicts_clears(self, tmp_path):
        store = KeywordOverridesStore(data_dir=str(tmp_path))
        store.update({
            "categories": {"subject": {"en": {"added": ["x"], "removed": []}}},
            "thresholds": {"minimum_matches": 3},
        })
        store.update({"categories": {}, "thresholds": {}})
        assert store.get() == {"categories": {}, "thresholds": {}}

    def test_persists_across_fresh_store_instance(self, tmp_path):
        store = KeywordOverridesStore(data_dir=str(tmp_path))
        overrides = {
            "categories": {"subject": {"en": {"added": ["heat pump"], "removed": []}}},
            "thresholds": {},
        }
        store.update(overrides)

        reloaded = KeywordOverridesStore(data_dir=str(tmp_path))
        assert reloaded.get() == overrides

    def test_missing_keys_in_update_default_empty(self, tmp_path):
        store = KeywordOverridesStore(data_dir=str(tmp_path))
        store.update({})
        assert store.get() == {"categories": {}, "thresholds": {}}

    def test_stored_in_kv_table_under_expected_name(self, tmp_path):
        from src.storage import db as storage_db

        store = KeywordOverridesStore(data_dir=str(tmp_path))
        overrides = {"categories": {}, "thresholds": {"minimum_matches": 1}}
        store.update(overrides)

        conn = storage_db.connect(str(tmp_path))
        raw = storage_db.kv_get(conn, "keyword_overrides")
        assert raw == overrides


class TestClear:
    def test_clear_resets_to_empty(self, tmp_path):
        store = KeywordOverridesStore(data_dir=str(tmp_path))
        store.update({
            "categories": {"subject": {"en": {"added": ["x"], "removed": []}}},
            "thresholds": {"minimum_matches": 3},
        })
        result = store.clear()
        assert result == {"categories": {}, "thresholds": {}}
        assert store.get() == {"categories": {}, "thresholds": {}}
