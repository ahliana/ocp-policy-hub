"""Keyword matching with weighted scoring across 7 categories and 20 languages.

Features:
- Compound language substring matching (DE, NL, SV, DA, NO, FI, IS, HU, JA, KO, AR)
- Boost/penalty keywords
- URL-based scoring bonuses (gov TLDs, legislation paths, bill numbers)
- Required category combinations
- Near-miss tracking
"""

import re
from typing import Optional
from urllib.parse import urlparse

from .models import KeywordResult, KeywordMatch

# The 7 categories x 20 languages keywords.yaml ships with today. Used to
# validate PUT /api/keywords/overrides payloads (src/api/routes/keywords_admin.py)
# so an admin can't add terms under a language KeywordMatcher's compound-word
# handling doesn't know about.
VALID_KEYWORD_LANGUAGES: frozenset[str] = frozenset({
    "ar", "cs", "da", "de", "el", "en", "es", "fi", "fr", "hu",
    "is", "it", "ja", "ko", "nl", "no", "pl", "pt", "ro", "sv",
})

# Languages that use compound words (skip word boundaries)
COMPOUND_LANGUAGES: set[str] = {"de", "nl", "sv", "da", "no", "fi", "is", "hu", "ja", "ko", "ar"}

# Default URL bonus values
_DEFAULT_GOV_TLD_BONUS = 1.0
_DEFAULT_BILL_PATH_BONUS = 1.5
_DEFAULT_BILL_NUMBER_BONUS = 1.0
_DEFAULT_GOV_TLD_PATTERNS = [".gov", ".gov.uk"]
_DEFAULT_BILL_PATH_PATTERNS = [
    r"/bill[s]?[-/]", r"/legislation/", r"/act[s]?/",
    r"/statute[s]?/", r"/measure[s]?/", r"/resolution[s]?/",
    r"/legp\d+\.exe",
]
_DEFAULT_BILL_NUMBER_PATTERN = (
    r"[/=](H\.?B\.?|S\.?B\.?|H\.?R\.?|S\.?R\.?|H\.?J\.?R\.?|S\.?J\.?R\.?)\s*\d+"
)


class KeywordMatcher:
    """Keyword matcher with configurable strictness levels."""

    def __init__(self, keywords_config: dict):
        self.keywords = keywords_config.get("keywords", {})
        self.thresholds = keywords_config.get("thresholds", {})
        self.exclusions = keywords_config.get("exclusions", [])
        self.stricter = keywords_config.get("stricter_requirements", {})

        # URL bonus config
        url_cfg = keywords_config.get("url_bonuses", {})
        self._gov_tld_bonus = url_cfg.get("gov_tld_bonus", _DEFAULT_GOV_TLD_BONUS)
        self._gov_tld_patterns = url_cfg.get("gov_tld_patterns", _DEFAULT_GOV_TLD_PATTERNS)
        self._bill_path_bonus = url_cfg.get("bill_path_bonus", _DEFAULT_BILL_PATH_BONUS)
        self._bill_path_patterns = url_cfg.get("bill_path_patterns", _DEFAULT_BILL_PATH_PATTERNS)
        self._bill_number_bonus = url_cfg.get("bill_number_bonus", _DEFAULT_BILL_NUMBER_BONUS)
        bill_pat = url_cfg.get("bill_number_pattern", _DEFAULT_BILL_NUMBER_PATTERN)
        self._bill_number_pattern = re.compile(bill_pat, re.IGNORECASE)

        self._patterns: dict[str, re.Pattern] = {}
        self._boost_patterns: list[tuple[str, re.Pattern]] = []
        self._penalty_patterns: list[tuple[str, re.Pattern]] = []

        self._compile_patterns()
        self._compile_boost_penalty()

    def _compile_patterns(self) -> None:
        for category, config in self.keywords.items():
            terms = config.get("terms", {})
            for lang, keyword_list in terms.items():
                for keyword in keyword_list:
                    if lang in COMPOUND_LANGUAGES:
                        pattern = re.compile(re.escape(keyword), re.IGNORECASE)
                    else:
                        pattern = re.compile(
                            r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE,
                        )
                    self._patterns[f"{category}:{lang}:{keyword}"] = pattern

    def _compile_boost_penalty(self) -> None:
        boost_cfg = self.stricter.get("boost_keywords", {})
        if boost_cfg.get("enabled", False):
            for term in boost_cfg.get("terms", []):
                self._boost_patterns.append(
                    (term, re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE))
                )
        penalty_cfg = self.stricter.get("penalty_keywords", {})
        if penalty_cfg.get("enabled", False):
            for term in penalty_cfg.get("terms", []):
                self._penalty_patterns.append(
                    (term, re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE))
                )

    def url_bonus(self, url: str) -> float:
        """Calculate bonus score from URL patterns."""
        bonus = 0.0
        parsed = urlparse(url)
        hostname = parsed.netloc.lower()
        path = parsed.path.lower()
        full = f"{path}?{parsed.query}" if parsed.query else path

        for suffix in self._gov_tld_patterns:
            if hostname.endswith(suffix.lower()):
                bonus += self._gov_tld_bonus
                break

        for pattern in self._bill_path_patterns:
            if re.search(pattern, full, re.IGNORECASE):
                bonus += self._bill_path_bonus
                break

        if self._bill_number_pattern.search(full):
            bonus += self._bill_number_bonus

        return bonus

    def match(self, text: str) -> KeywordResult:
        """Match keywords in text and return scored result."""
        text_lower = text.lower()

        # Check exclusions first
        for exclusion in self.exclusions:
            if exclusion.lower() in text_lower:
                return KeywordResult(score=0.0, is_excluded=True)

        matches = []
        for key, pattern in self._patterns.items():
            category, lang, keyword = key.split(":", 2)
            weight = self.keywords[category].get("weight", 1.0)
            found = pattern.findall(text)
            if found:
                matches.append(KeywordMatch(
                    term=keyword, category=category, weight=weight, language=lang,
                ))

        score = sum(m.weight for m in matches)
        categories = list({m.category for m in matches})

        # Boost keywords
        boost = 0.0
        boost_cfg = self.stricter.get("boost_keywords", {})
        if boost_cfg.get("enabled", False):
            boost_amount = boost_cfg.get("boost_amount", 3.0)
            for term, pattern in self._boost_patterns:
                if pattern.search(text):
                    boost += boost_amount

        # Penalty keywords
        penalty = 0.0
        penalty_cfg = self.stricter.get("penalty_keywords", {})
        if penalty_cfg.get("enabled", False):
            penalty_amount = penalty_cfg.get("penalty_amount", 2.0)
            for term, pattern in self._penalty_patterns:
                if pattern.search(text):
                    penalty += penalty_amount

        final_score = max(0.0, score + boost - penalty)

        return KeywordResult(
            score=final_score,
            matches=matches,
            categories_matched=categories,
        )

    def is_relevant(
        self, result: KeywordResult, content_length: int = 0,
        url: str = "", min_score_override: Optional[float] = None,
    ) -> bool:
        """Check if a match result indicates relevant content."""
        min_score = min_score_override if min_score_override is not None else self.thresholds.get("minimum_keyword_score", 5.0)
        min_matches = self.thresholds.get("minimum_matches", 2)

        effective_score = result.score
        if url:
            bonus = self.url_bonus(url)
            effective_score += bonus
            result.url_bonus = bonus

        if effective_score < min_score:
            return False
        if len(result.matches) < min_matches:
            return False

        # Check required combinations
        combo_cfg = self.stricter.get("required_combinations", {})
        if combo_cfg.get("enabled", False):
            combinations = combo_cfg.get("combinations", [])
            min_per_cat = combo_cfg.get("min_matches_per_category", 1)
            satisfied = False
            for combo in combinations:
                primary = combo.get("primary", "")
                secondary = combo.get("secondary", "")
                p_count = sum(1 for m in result.matches if m.category == primary)
                s_count = sum(1 for m in result.matches if m.category == secondary)
                if p_count >= min_per_cat and s_count >= min_per_cat:
                    satisfied = True
                    break
            if not satisfied:
                return False

        result.passes_threshold = True
        return True

    def check_near_miss(
        self, result: KeywordResult, url: str = "",
        min_score_override: Optional[float] = None,
    ) -> bool:
        """Check if result is a near miss (≥60% of threshold)."""
        min_score = min_score_override if min_score_override is not None else self.thresholds.get("minimum_keyword_score", 5.0)
        effective_score = result.score
        if url:
            effective_score += self.url_bonus(url)
        threshold_60 = min_score * 0.6
        return effective_score >= threshold_60 and not result.passes_threshold


def build_keyword_matcher(config, data_dir: str) -> "KeywordMatcher":
    """The single seam every KeywordMatcher construction site should use.

    Merges the kv-backed keyword overlay (src/storage/keyword_overrides.py)
    onto ``config.keywords_config`` (see
    src/core/overrides.py:apply_keyword_overrides) before constructing the
    matcher, so an admin's added/removed terms and threshold overrides reach
    every consumer — scanner, /api/analyze, agent tools, mcp server — with
    no restart. Call sites already have a ``data_dir`` (ScanManager,
    PolicyStore, etc. all take one), so this takes that rather than a
    pre-built store, matching the "construct a store per call" pattern
    already used for PolicyStore/CostSettingsStore elsewhere in this
    codebase — cheap, since it's a local SQLite connection.
    """
    from ..storage.keyword_overrides import KeywordOverridesStore
    from .overrides import apply_keyword_overrides

    store = KeywordOverridesStore(data_dir=data_dir)
    merged = apply_keyword_overrides(config.keywords_config, store.get())
    return KeywordMatcher(merged)
