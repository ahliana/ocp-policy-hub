"""Tests for the rules fingerprint and the cache invalidation it drives.

The defect these pin: a page judged irrelevant under one keyword file
stayed judged irrelevant after that file changed, so the first measurement
after a tuning change mixed old and new rules together.
"""

import pytest

from src.core.cache import CacheEntry, URLCache
from src.core.rules_version import _significant_lines, rules_fingerprint


class TestFingerprint:
    @pytest.mark.small
    def test_the_same_rules_give_the_same_fingerprint(self):
        assert rules_fingerprint(["a", "b"]) == rules_fingerprint(["a", "b"])

    @pytest.mark.small
    def test_changed_rules_give_a_different_fingerprint(self):
        assert rules_fingerprint(["a", "b"]) != rules_fingerprint(["a", "c"])

    @pytest.mark.small
    def test_the_parts_are_separated(self):
        """Without a separator, moving a character between two parts would
        hash the same and a real rule change could slip through."""
        assert rules_fingerprint(["ab", "c"]) != rules_fingerprint(["a", "bc"])

    @pytest.mark.small
    def test_a_comment_change_does_not_change_the_rules(self):
        """Explaining a keyword must not throw away a month of analysis."""
        before = _significant_lines("subject:\n  - \"waste heat\"  # the core term\n")
        after = _significant_lines(
            "# Added 2026-08-28, see the plan\nsubject:\n  - \"waste heat\"\n")
        assert rules_fingerprint([before]) == rules_fingerprint([after])

    @pytest.mark.small
    def test_a_real_keyword_change_does_change_the_rules(self):
        before = _significant_lines('subject:\n  - "waste heat"\n')
        after = _significant_lines('subject:\n  - "waste heat"\n  - "excess heat"\n')
        assert rules_fingerprint([before]) != rules_fingerprint([after])

    @pytest.mark.small
    def test_blank_lines_are_not_rules(self):
        assert _significant_lines("a:\n\n\n  - b\n") == "a:\n  - b"


class TestCacheInvalidation:
    @pytest.mark.small
    def test_an_entry_written_under_other_rules_is_a_miss(self):
        """FAILS ON OLD BEHAVIOR. The cache had no notion of which rules
        produced a verdict, so this entry was returned as a hit and the
        rule change had no effect on it."""
        cache = URLCache(rules_fingerprint="rules-v1")
        cache.set("https://example.gov/a", is_relevant=False, content_hash="h1")

        after_change = URLCache(rules_fingerprint="rules-v2")
        after_change._entries = cache._entries

        assert after_change.get("https://example.gov/a", "h1") is None
        assert after_change.stats.rules_changed == 1

    @pytest.mark.small
    def test_the_same_rules_still_hit(self):
        cache = URLCache(rules_fingerprint="rules-v1")
        cache.set("https://example.gov/a", is_relevant=True, content_hash="h1")
        assert cache.get("https://example.gov/a", "h1") is not None
        assert cache.stats.hits == 1

    @pytest.mark.small
    def test_an_entry_from_before_fingerprinting_is_stale(self):
        """An unknown rule set is exactly the case this exists to catch, so
        it must not be treated as a match."""
        cache = URLCache(rules_fingerprint="rules-v1")
        cache._entries["https://example.gov/old"] = CacheEntry(
            url="https://example.gov/old",
            is_relevant=True,
            content_hash="h1",
            expires_date="2099-01-01T00:00:00+00:00",
        )
        assert cache.get("https://example.gov/old", "h1") is None

    @pytest.mark.small
    def test_an_empty_fingerprint_switches_the_check_off(self):
        """So a test exercising expiry or content hashing is not forced to
        care about rules, and so the feature has an off position."""
        cache = URLCache(rules_fingerprint="")
        cache._entries["https://example.gov/old"] = CacheEntry(
            url="https://example.gov/old",
            is_relevant=True,
            content_hash="h1",
            expires_date="2099-01-01T00:00:00+00:00",
        )
        assert cache.get("https://example.gov/old", "h1") is not None

    @pytest.mark.small
    def test_a_stale_entry_is_counted_apart_from_expiry(self):
        """A tuning run should be able to say how much of its work the rule
        change caused, rather than folding it into time passing."""
        cache = URLCache(rules_fingerprint="rules-v1")
        cache.set("https://example.gov/a", is_relevant=True, content_hash="h1")
        cache.rules_fingerprint = "rules-v2"
        cache.get("https://example.gov/a", "h1")
        assert cache.stats.rules_changed == 1
        assert cache.stats.expired == 0
