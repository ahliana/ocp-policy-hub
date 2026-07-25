"""Overlay-application helpers — the boundary where admin-set overrides
(domain enable/disable, keyword additions/removals) merge onto pure YAML
config, without ``ConfigLoader`` itself knowing overrides exist.

Both functions here are pure: they take already-loaded config data plus an
overlay dict and return new data, so they're trivial to unit test and safe
to call from several places (ScanManager, API routes) without any of them
needing to know about kv storage.
"""

import copy


def apply_domain_overrides(domains: list[dict], overrides: dict[str, dict]) -> list[dict]:
    """Drop any domain the overlay has explicitly disabled.

    ``overrides`` is ``DomainOverridesStore.get_all()``'s shape:
    ``{domain_id: {"enabled": bool}}``. A domain with no override entry, or
    an override of ``enabled: True``, passes through unchanged. ``domains``
    is expected to already be YAML-enabled (e.g.
    ``ConfigLoader.get_enabled_domains()``'s output) — the overlay can only
    remove a domain here, never re-add one the YAML itself disabled.
    """
    return [
        d for d in domains
        if overrides.get(d["id"], {}).get("enabled") is not False
    ]


def apply_keyword_overrides(keywords_config: dict, overrides: dict) -> dict:
    """Merge a keyword-overrides overlay onto a loaded ``keywords.yaml`` dict.

    Pure and non-mutating (deep-copies ``keywords_config`` before editing).
    ``overrides`` shape (``KeywordOverridesStore.get()``)::

        {"categories": {category: {language: {"added": [...], "removed": [...]}}},
         "thresholds": {"minimum_keyword_score": float, "minimum_matches": int}}

    Added terms are appended (post-removal, deduplicated) to
    ``keywords[category].terms[language]``; removed terms are filtered out of
    the YAML list first. Threshold overrides replace only the given keys
    under ``thresholds``, leaving ``minimum_llm_relevance`` and anything else
    untouched.
    """
    merged = copy.deepcopy(keywords_config)

    categories = merged.setdefault("keywords", {})
    for category, by_language in overrides.get("categories", {}).items():
        cat_cfg = categories.setdefault(category, {"weight": 1.0, "terms": {}})
        terms = cat_cfg.setdefault("terms", {})
        for language, delta in by_language.items():
            existing = list(terms.get(language, []))
            removed = set(delta.get("removed", []))
            kept = [t for t in existing if t not in removed]
            added = [t for t in delta.get("added", []) if t not in kept]
            terms[language] = kept + added

    threshold_overrides = overrides.get("thresholds", {})
    if threshold_overrides:
        thresholds = merged.setdefault("thresholds", {})
        for key in ("minimum_keyword_score", "minimum_matches"):
            value = threshold_overrides.get(key)
            if value is not None:
                thresholds[key] = value

    return merged
