"""Tests for the keyword overlay merge seam (WP-10).

``apply_keyword_overrides`` (src/core/overrides.py) is a pure merge of the
kv-backed overlay onto a loaded keywords.yaml dict.
``build_keyword_matcher`` (src/core/keywords.py) is the single factory every
KeywordMatcher construction site (scanner, /api/analyze, agent tools, mcp
server) should call instead of ``KeywordMatcher(config.keywords_config)``
directly, so an admin's added/removed terms and threshold overrides reach
every consumer without a restart.
"""

from src.core.keywords import KeywordMatcher, build_keyword_matcher
from src.core.overrides import apply_keyword_overrides
from src.storage.keyword_overrides import KeywordOverridesStore


def _keywords_config():
    return {
        "keywords": {
            "subject": {
                "weight": 3.0,
                "description": "Core subject",
                "terms": {"en": ["waste heat"], "de": ["Abwärme"]},
            },
            "context": {
                "weight": 1.0,
                "description": "Context",
                "terms": {"en": ["data center"]},
            },
        },
        "thresholds": {"minimum_keyword_score": 5.0, "minimum_matches": 2},
        "exclusions": ["job opening"],
        "url_bonuses": {"gov_tld_bonus": 1.0},
        "stricter_requirements": {},
    }


# ---------------------------------------------------------------------------
# apply_keyword_overrides (pure)
# ---------------------------------------------------------------------------

class TestApplyKeywordOverridesNoOp:
    def test_empty_overrides_leaves_config_unchanged(self):
        config = _keywords_config()
        merged = apply_keyword_overrides(config, {"categories": {}, "thresholds": {}})
        assert merged == config

    def test_does_not_mutate_input(self):
        config = _keywords_config()
        apply_keyword_overrides(config, {
            "categories": {"subject": {"en": {"added": ["heat pump"], "removed": []}}},
            "thresholds": {},
        })
        assert config["keywords"]["subject"]["terms"]["en"] == ["waste heat"]


class TestApplyKeywordOverridesAdd:
    def test_added_term_appended_to_existing_language(self):
        config = _keywords_config()
        merged = apply_keyword_overrides(config, {
            "categories": {"subject": {"en": {"added": ["heat pump"], "removed": []}}},
            "thresholds": {},
        })
        assert merged["keywords"]["subject"]["terms"]["en"] == ["waste heat", "heat pump"]

    def test_added_term_for_new_language(self):
        config = _keywords_config()
        merged = apply_keyword_overrides(config, {
            "categories": {"subject": {"fr": {"added": ["chaleur perdue"], "removed": []}}},
            "thresholds": {},
        })
        assert merged["keywords"]["subject"]["terms"]["fr"] == ["chaleur perdue"]

    def test_duplicate_added_term_not_repeated(self):
        config = _keywords_config()
        merged = apply_keyword_overrides(config, {
            "categories": {"subject": {"en": {"added": ["waste heat"], "removed": []}}},
            "thresholds": {},
        })
        assert merged["keywords"]["subject"]["terms"]["en"] == ["waste heat"]


class TestApplyKeywordOverridesRemove:
    def test_removed_term_dropped(self):
        config = _keywords_config()
        merged = apply_keyword_overrides(config, {
            "categories": {"subject": {"en": {"added": [], "removed": ["waste heat"]}}},
            "thresholds": {},
        })
        assert merged["keywords"]["subject"]["terms"]["en"] == []

    def test_removed_then_readded_nets_present(self):
        config = _keywords_config()
        merged = apply_keyword_overrides(config, {
            "categories": {
                "subject": {"en": {"added": ["waste heat"], "removed": ["waste heat"]}},
            },
            "thresholds": {},
        })
        # removed applies to the YAML list before added is appended, so a
        # term that's both removed and re-added ends up present, once.
        assert merged["keywords"]["subject"]["terms"]["en"] == ["waste heat"]


class TestApplyKeywordOverridesThresholds:
    def test_overrides_replace_given_keys_only(self):
        config = _keywords_config()
        merged = apply_keyword_overrides(config, {
            "categories": {}, "thresholds": {"minimum_keyword_score": 2.0},
        })
        assert merged["thresholds"]["minimum_keyword_score"] == 2.0
        assert merged["thresholds"]["minimum_matches"] == 2

    def test_null_threshold_value_leaves_yaml_value(self):
        config = _keywords_config()
        merged = apply_keyword_overrides(config, {
            "categories": {}, "thresholds": {"minimum_keyword_score": None},
        })
        assert merged["thresholds"]["minimum_keyword_score"] == 5.0


# ---------------------------------------------------------------------------
# build_keyword_matcher factory + KeywordMatcher.match integration
# ---------------------------------------------------------------------------

class _FakeConfig:
    def __init__(self, keywords_config):
        self.keywords_config = keywords_config


class TestBuildKeywordMatcher:
    def test_no_overrides_behaves_like_plain_construction(self, tmp_path):
        config = _FakeConfig(_keywords_config())
        matcher = build_keyword_matcher(config, str(tmp_path))
        plain = KeywordMatcher(_keywords_config())
        assert matcher.match("waste heat").score == plain.match("waste heat").score

    def test_added_term_makes_matcher_hit_it(self, tmp_path):
        store = KeywordOverridesStore(data_dir=str(tmp_path))
        store.update({
            "categories": {"subject": {"en": {"added": ["thermal recycling widget"], "removed": []}}},
            "thresholds": {},
        })
        config = _FakeConfig(_keywords_config())
        matcher = build_keyword_matcher(config, str(tmp_path))

        result = matcher.match("this page is about a thermal recycling widget")
        assert any(m.term == "thermal recycling widget" for m in result.matches)

    def test_removed_term_stops_matching(self, tmp_path):
        store = KeywordOverridesStore(data_dir=str(tmp_path))
        store.update({
            "categories": {"subject": {"en": {"added": [], "removed": ["waste heat"]}}},
            "thresholds": {},
        })
        config = _FakeConfig(_keywords_config())
        matcher = build_keyword_matcher(config, str(tmp_path))

        result = matcher.match("this page discusses waste heat recovery")
        assert not any(m.term == "waste heat" for m in result.matches)

    def test_threshold_override_respected_by_is_relevant(self, tmp_path):
        store = KeywordOverridesStore(data_dir=str(tmp_path))
        store.update({"categories": {}, "thresholds": {"minimum_keyword_score": 1.0, "minimum_matches": 1}})
        config = _FakeConfig(_keywords_config())
        matcher = build_keyword_matcher(config, str(tmp_path))

        result = matcher.match("data center")  # context weight 1.0, one match
        assert matcher.is_relevant(result) is True
