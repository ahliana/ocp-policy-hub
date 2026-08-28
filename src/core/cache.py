"""URL result cache with TTL expiry, content-hash change detection, and periodic saves."""

import hashlib
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from .rules_version import rules_fingerprint as compute_rules_fingerprint

logger = logging.getLogger(__name__)


class CacheEntry(BaseModel):
    """A cached analysis result for a URL."""
    url: str
    is_relevant: bool
    relevance_score: int = 0
    content_hash: str = ""
    analyzed_date: str = ""
    expires_date: str = ""
    policy_type: str = ""
    # Which rules produced this verdict. Empty means the entry predates
    # fingerprinting, which is treated as stale rather than as a match:
    # an unknown rule set is exactly what this exists to catch.
    rules_fingerprint: str = ""

    def is_expired(self) -> bool:
        if not self.expires_date:
            return True
        try:
            expires = datetime.fromisoformat(self.expires_date)
            return datetime.now(timezone.utc) >= expires
        except (ValueError, TypeError):
            return True

    def matches_content(self, content_hash: str) -> bool:
        if not self.content_hash or not content_hash:
            return False
        return self.content_hash == content_hash

    def matches_rules(self, fingerprint: str) -> bool:
        """Whether this verdict was produced by the rules now in force."""
        if not fingerprint:
            return True  # fingerprinting disabled: behave as before
        return self.rules_fingerprint == fingerprint


class CacheStats(BaseModel):
    """Cache usage statistics."""
    total_entries: int = 0
    hits: int = 0
    misses: int = 0
    expired: int = 0
    content_changed: int = 0
    # Entries skipped because the rules that produced them have changed.
    # Counted separately from expiry so a tuning run can report how much of
    # its work was caused by the rule change rather than by time passing.
    rules_changed: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    def reset_session(self):
        self.hits = 0
        self.misses = 0
        self.expired = 0
        self.content_changed = 0


class URLCache:
    """Cache for URL analysis results with TTL and content-hash support."""

    DEFAULT_EXPIRY_DAYS = 30
    # Negative verdicts expire sooner: a wrong screening rejection should
    # not be frozen for a month while prompts and keywords improve.
    NEGATIVE_EXPIRY_DAYS = 7
    DEFAULT_CACHE_PATH = Path("data/url_cache.json")
    SAVE_INTERVAL = 50  # Auto-save every N sets

    def __init__(
        self,
        expiry_days: int = DEFAULT_EXPIRY_DAYS,
        cache_path: Optional[Path] = None,
        rules_fingerprint: Optional[str] = None,
    ):
        self.expiry_days = expiry_days
        self.cache_path = cache_path or self.DEFAULT_CACHE_PATH
        # None means "work it out from the live config". An explicit empty
        # string switches the check off, which is what a test wants when it
        # is exercising expiry or content hashing rather than rules.
        self.rules_fingerprint = (
            compute_rules_fingerprint() if rules_fingerprint is None
            else rules_fingerprint
        )
        self._entries: dict[str, CacheEntry] = {}
        self.stats = CacheStats()
        self._sets_since_save = 0

    def get(self, url: str, content_hash: str = "") -> Optional[CacheEntry]:
        """Get cached entry. Returns None if missing, expired, or content changed."""
        entry = self._entries.get(url)

        if entry is None:
            self.stats.misses += 1
            return None

        if entry.is_expired():
            self.stats.expired += 1
            self.stats.misses += 1
            return None

        if content_hash and not entry.matches_content(content_hash):
            self.stats.content_changed += 1
            self.stats.misses += 1
            return None

        if not entry.matches_rules(self.rules_fingerprint):
            self.stats.rules_changed += 1
            self.stats.misses += 1
            return None

        self.stats.hits += 1
        return entry

    def set(
        self,
        url: str,
        is_relevant: bool,
        relevance_score: int = 0,
        content_hash: str = "",
        policy_type: str = "",
    ) -> CacheEntry:
        """Cache an analysis result."""
        now = datetime.now(timezone.utc)
        expiry_days = (
            self.expiry_days if is_relevant
            else min(self.expiry_days, self.NEGATIVE_EXPIRY_DAYS)
        )
        expires = now + timedelta(days=expiry_days)

        entry = CacheEntry(
            url=url,
            is_relevant=is_relevant,
            relevance_score=relevance_score,
            content_hash=content_hash,
            analyzed_date=now.isoformat(),
            expires_date=expires.isoformat(),
            policy_type=policy_type,
            rules_fingerprint=self.rules_fingerprint,
        )

        self._entries[url] = entry
        self.stats.total_entries = len(self._entries)

        # Periodic auto-save
        self._sets_since_save += 1
        if self._sets_since_save >= self.SAVE_INTERVAL:
            self.save()
            self._sets_since_save = 0

        return entry

    def contains(self, url: str) -> bool:
        return url in self._entries

    def clean_expired(self) -> int:
        """Remove expired entries. Returns count removed."""
        expired = [url for url, e in self._entries.items() if e.is_expired()]
        for url in expired:
            del self._entries[url]
        self.stats.total_entries = len(self._entries)
        return len(expired)

    def save(self, path: Optional[Path] = None) -> bool:
        """Save cache to disk with atomic write."""
        target = path or self.cache_path
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            data = {
                "expiry_days": self.expiry_days,
                "entries": {url: e.model_dump() for url, e in self._entries.items()},
                "metadata": {
                    "version": 1,
                    "last_saved": datetime.now(timezone.utc).isoformat(),
                    "total_entries": len(self._entries),
                },
            }
            tmp = target.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp.replace(target)
            return True
        except (IOError, TypeError) as e:
            logger.warning(f"Failed to save cache: {e}")
            return False

    @classmethod
    def load(cls, cache_path: Optional[Path] = None) -> "URLCache":
        """Load cache from disk. Returns empty cache on error."""
        path = cache_path or cls.DEFAULT_CACHE_PATH
        if not path.exists():
            return cls(cache_path=path)

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            expiry = data.get("expiry_days", cls.DEFAULT_EXPIRY_DAYS)
            cache = cls(expiry_days=expiry, cache_path=path)
            for url, entry_data in data.get("entries", {}).items():
                cache._entries[url] = CacheEntry(**entry_data)
            cache.stats.total_entries = len(cache._entries)
            return cache
        except json.JSONDecodeError as e:
            logger.error(
                "Cache file %s is corrupted (JSON error: %s) — starting fresh. "
                "Previous cache data is lost. This is a performance impact only, "
                "no policy data is affected.",
                path, e,
            )
            return cls(cache_path=path)
        except Exception as e:
            logger.error(
                "Failed to load cache from %s: %s — starting fresh", path, e,
            )
            return cls(cache_path=path)


def compute_content_hash(content: str) -> str:
    """Hash the full content for change detection.

    Hashing only a head sample missed statute amendments that appear late
    in the document, serving stale cached verdicts for changed pages.
    """
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()[:16]


def stale_summary(cache: "URLCache") -> dict:
    """How many cached verdicts the rules now in force would not accept.

    The number a person needs before agreeing to a rescan: every stale
    entry is a page the next scan will fetch and judge again, and judging
    costs money. Reported before anything is fetched, never after.
    """
    fingerprint = cache.rules_fingerprint
    total = len(cache._entries)
    expired = sum(1 for e in cache._entries.values() if e.is_expired())
    stale_rules = sum(
        1 for e in cache._entries.values()
        if not e.is_expired() and not e.matches_rules(fingerprint)
    )
    return {
        "rules_fingerprint": fingerprint,
        "total": total,
        "expired": expired,
        "stale_rules": stale_rules,
        "fresh": total - expired - stale_rules,
    }


def _print_stats() -> None:
    """`python -m src.core.cache` — what a rule change would cost."""
    cache = URLCache.load()
    summary = stale_summary(cache)
    print(f"rules fingerprint: {summary['rules_fingerprint']}")
    print(f"cached verdicts:   {summary['total']}")
    print(f"  already expired: {summary['expired']}")
    print(f"  stale rules:     {summary['stale_rules']}")
    print(f"  still usable:    {summary['fresh']}")
    print(f"  skipped so far this session for changed rules: "
          f"{cache.stats.rules_changed}")
    print()
    rescan = summary["expired"] + summary["stale_rules"]
    if not summary["total"]:
        print("The cache is empty, so a scan would judge every page it finds.")
    elif not rescan:
        print("Nothing would be re-judged: every cached verdict was produced "
              "by the rules now in force and none has expired.")
    else:
        print(f"A scan would re-judge {rescan} of the {summary['total']} pages "
              f"it has seen before, because their verdicts either expired or "
              f"were produced by different rules. The rest would be free.")


if __name__ == "__main__":  # pragma: no cover - thin CLI wrapper
    _print_stats()
