"""URL normalization for dedupe keying (WP-42).

Both the news sweep's in-batch dedupe (``src.signals.news.dedupe_items``) and
the lead store's cross-week dedupe (``src.storage.leads._dedupe_key``) key on
a URL. Real-world feeds hand back the same article under enough cosmetic
variants (``http`` vs ``https``, a trailing slash, ``utm_*`` tracking params,
a Google News redirect wrapper) that keying on the raw URL leaves an easy
dedupe hole. :func:`normalize_url` collapses those variants to one canonical
form; unrelated URLs are never merged.

Deliberately best-effort: an unparseable input is returned unchanged rather
than raising - a dedupe key that fails to normalize should still work as a
(less effective) literal key, not crash the sweep.
"""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Google Analytics campaign params, plus the common click-id trackers
# ("fbclid-style") that ad platforms append. Stripping these means
# "the same article, shared from a different campaign link" normalizes
# to one key instead of one per share.
_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAM_NAMES = {
    "fbclid", "gclid", "msclkid", "twclid", "igshid", "mc_cid", "mc_eid",
}


def _is_tracking_param(name: str) -> bool:
    lower = name.lower()
    return lower.startswith(_TRACKING_PARAM_PREFIXES) or lower in _TRACKING_PARAM_NAMES


def normalize_url(url: str) -> str:
    """Canonicalize a URL for use as a dedupe key.

    - Unwraps a ``news.google.com`` redirect link to its ``url=`` target
      when present (recursively normalized), since that target is the real
      article and the wrapper's opaque ID is not a stable dedupe key.
    - Lowercases scheme and host (URLs are case-sensitive in the path, but
      never in scheme/host).
    - Strips a trailing slash from the path.
    - Drops ``utm_*``/``fbclid``-style tracking query params.
    - Drops the fragment (never part of what identifies the resource here).

    Callers must not treat the result as a redirect target to fetch or as
    the value to store - it is a key, not a URL to use for anything else.
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url

    if parts.hostname == "news.google.com":
        target = dict(parse_qsl(parts.query)).get("url")
        if target:
            return normalize_url(target)

    scheme = parts.scheme.lower()
    # The same article served over http and https is one article. Safe only
    # because this is a dedupe KEY, never a URL anything fetches or stores.
    if scheme == "http":
        scheme = "https"
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/")
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking_param(k)
    ]
    query = urlencode(query_pairs)
    return urlunsplit((scheme, netloc, path, query, ""))
