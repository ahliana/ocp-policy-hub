"""Source-intake validation harness (WP-41).

Gates promotion of a candidate domain from a research draft into
``config/domains/``. A candidate must pass every check here before it is
promoted; a failing candidate stays in draft with its failure reasons
attached - never silently promoted, never silently dropped.

Checks, matching ``config/domains/_template.yaml``'s documented shape and
the research branch's verification steps:

- ``schema_shape``: the template's required fields (name, id, base_url,
  start_paths) are present and typed correctly (validated against
  :class:`~src.core.models.DomainConfig`).
- ``jurisdiction``: every slug in ``region`` resolves in the jurisdiction
  registry (``src.core.jurisdictions``).
- ``fetch_start_path``: the start path resolves without a redirect loop.
- ``content_type``: the response is HTML (a policy page, not a PDF/JSON API
  response - those are legitimate for other domains but not for a page a
  human is meant to read via crawling).
- ``non_trivial_text``: the page carries real visible text once tags are
  stripped, not an effectively blank/error page.
- ``not_js_shell``: the page is not an unrendered SPA shell (would need
  ``requires_playwright: true`` to ever produce content).
- ``robots_txt``: robots.txt is reachable (the site responds at all).
- ``language_detect``: the page's text yields a plausible language code.

Fetching is injected (the ``fetcher`` parameter) so tests run against fixture
responses only - no real network. CLI use (``python -m src.sources.intake
path/to/candidate.yaml``) defaults to a real httpx-backed fetcher.
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urljoin

import httpx
import yaml
from langdetect import LangDetectException, detect
from pydantic import ValidationError

from ..core.jurisdictions import get as get_jurisdiction
from ..core.models import DomainConfig

# Matches the JS-shell fallback threshold AsyncCrawler already uses in
# production (src.core.crawler.AsyncCrawler._visible_text_len) - a page
# under this many visible characters is treated the same way a real crawl
# would treat it.
MIN_VISIBLE_TEXT_CHARS = 200

REQUIRED_FIELDS = ("name", "id", "base_url", "start_paths")

# Common markers of an unrendered JS SPA shell. Not exhaustive - a
# false-negative here just means the liveness check falls through to the
# generic "non_trivial_text" failure instead, which still blocks promotion.
_JS_SHELL_MARKERS = (
    'id="root"', "id='root'", 'id="app"', "id='app'",
    "__next_data__", "data-reactroot", "ng-app",
)


class FetchError(Exception):
    """Raised by a fetcher when a URL could not be reached at all."""


class RedirectLoopError(FetchError):
    """Raised when following redirects exceeds a sane loop limit."""


@dataclass
class FetchResponse:
    status_code: int
    text: str
    final_url: str
    content_type: str = ""


Fetcher = Callable[[str], FetchResponse]


def default_fetcher(url: str) -> FetchResponse:
    """Real-network fetcher for CLI use. Never used by the test suite."""
    try:
        with httpx.Client(follow_redirects=True, timeout=10.0, max_redirects=10) as client:
            resp = client.get(url)
    except httpx.TooManyRedirects as e:
        raise RedirectLoopError(f"too many redirects fetching {url}") from e
    except httpx.HTTPError as e:
        raise FetchError(f"could not fetch {url}: {e}") from e
    return FetchResponse(
        status_code=resp.status_code,
        text=resp.text,
        final_url=str(resp.url),
        content_type=resp.headers.get("content-type", ""),
    )


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


@dataclass
class ValidationResult:
    passed: bool
    checks: list[Check] = field(default_factory=list)


def _visible_text(html: str) -> str:
    """Rough visible-text extraction: strip script/style blocks, then tags."""
    if not html:
        return ""
    stripped = re.sub(
        r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.IGNORECASE | re.DOTALL,
    )
    stripped = re.sub(r"<[^>]+>", " ", stripped)
    return " ".join(stripped.split())


def _looks_like_js_shell(html: str) -> bool:
    lower = (html or "").lower()
    return any(marker in lower for marker in _JS_SHELL_MARKERS)


def _check_schema_shape(domain: dict) -> Check:
    missing = []
    for name in REQUIRED_FIELDS:
        value = domain.get(name)
        if name == "start_paths":
            if not isinstance(value, list) or not value:
                missing.append(name)
        elif not isinstance(value, str) or not value:
            missing.append(name)
    if missing:
        return Check(
            "schema_shape", False,
            f"missing required field(s): {', '.join(missing)}",
        )
    try:
        DomainConfig.model_validate(domain)
    except ValidationError as e:
        return Check("schema_shape", False, f"schema validation failed: {e}")
    return Check("schema_shape", True, "all required fields present and typed correctly")


def _check_jurisdiction(domain: dict) -> Check:
    region = domain.get("region") or []
    if not region:
        return Check("jurisdiction", False, "no region specified")
    unresolved = [slug for slug in region if get_jurisdiction(slug) is None]
    if unresolved:
        return Check(
            "jurisdiction", False,
            f"unknown region slug(s): {', '.join(unresolved)}",
        )
    return Check("jurisdiction", True, f"region resolves: {', '.join(region)}")


def _start_url(domain: dict) -> Optional[str]:
    base_url = domain.get("base_url")
    start_paths = domain.get("start_paths")
    if not base_url or not isinstance(start_paths, list) or not start_paths:
        return None
    return urljoin(base_url, start_paths[0])


def _skipped(name: str, reason: str) -> Check:
    return Check(name, False, f"skipped: {reason}")


def validate_candidate(domain: dict, fetcher: Optional[Fetcher] = None) -> ValidationResult:
    """Run every WP-41 check against one candidate domain dict.

    ``fetcher`` defaults to a real-network httpx client; tests must always
    inject a fixture fetcher instead.
    """
    fetch = fetcher or default_fetcher
    checks: list[Check] = [
        _check_schema_shape(domain),
        _check_jurisdiction(domain),
    ]

    start_url = _start_url(domain)
    if start_url is None:
        checks.append(_skipped("fetch_start_path", "base_url/start_paths missing"))
        checks.append(_skipped("content_type", "base_url/start_paths missing"))
        checks.append(_skipped("non_trivial_text", "base_url/start_paths missing"))
        checks.append(_skipped("not_js_shell", "base_url/start_paths missing"))
        checks.append(_skipped("language_detect", "base_url/start_paths missing"))
    else:
        try:
            response = fetch(start_url)
        except FetchError as e:
            # RedirectLoopError is a FetchError subclass - one branch covers
            # both "could not reach it" and "reached it but in a loop".
            checks.append(Check("fetch_start_path", False, str(e)))
            reason = f"could not fetch start path ({e})"
            checks.append(_skipped("content_type", reason))
            checks.append(_skipped("non_trivial_text", reason))
            checks.append(_skipped("not_js_shell", reason))
            checks.append(_skipped("language_detect", reason))
        else:
            if response.status_code >= 400:
                checks.append(Check(
                    "fetch_start_path", False,
                    f"start path returned HTTP {response.status_code}",
                ))
                reason = f"start path returned HTTP {response.status_code}"
                checks.append(_skipped("content_type", reason))
                checks.append(_skipped("non_trivial_text", reason))
                checks.append(_skipped("not_js_shell", reason))
                checks.append(_skipped("language_detect", reason))
            else:
                checks.append(Check(
                    "fetch_start_path", True,
                    f"resolved to {response.final_url} (HTTP {response.status_code})",
                ))
                if "html" in (response.content_type or "").lower():
                    checks.append(Check(
                        "content_type", True, f"content-type: {response.content_type}",
                    ))
                else:
                    checks.append(Check(
                        "content_type", False,
                        f"expected HTML, got content-type: {response.content_type!r}",
                    ))

                text = _visible_text(response.text)
                text_len = len(text)
                if text_len >= MIN_VISIBLE_TEXT_CHARS:
                    checks.append(Check(
                        "non_trivial_text", True, f"{text_len} visible characters",
                    ))
                else:
                    checks.append(Check(
                        "non_trivial_text", False,
                        f"only {text_len} visible characters (minimum "
                        f"{MIN_VISIBLE_TEXT_CHARS}) - page looks empty",
                    ))

                is_shell = _looks_like_js_shell(response.text) and text_len < MIN_VISIBLE_TEXT_CHARS
                if is_shell:
                    checks.append(Check(
                        "not_js_shell", False,
                        "page looks like an unrendered JS shell "
                        "(needs requires_playwright: true)",
                    ))
                else:
                    checks.append(Check("not_js_shell", True, "no JS-shell markers detected"))

                if not text_len:
                    checks.append(_skipped("language_detect", "no visible text to detect"))
                else:
                    try:
                        code = detect(text)
                    except LangDetectException:
                        checks.append(Check(
                            "language_detect", False, "could not detect a language",
                        ))
                    else:
                        checks.append(Check(
                            "language_detect", True, f"detected language: {code}",
                        ))

    robots_url = None
    base_url = domain.get("base_url")
    if base_url:
        robots_url = urljoin(base_url, "/robots.txt")
    if robots_url is None:
        checks.append(_skipped("robots_txt", "base_url missing"))
    else:
        try:
            fetch(robots_url)
        except FetchError as e:
            checks.append(Check("robots_txt", False, f"robots.txt unreachable: {e}"))
        else:
            checks.append(Check("robots_txt", True, "robots.txt reachable"))

    return ValidationResult(passed=all(c.passed for c in checks), checks=checks)


def _load_candidates(path: Path) -> list[dict]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if isinstance(raw, dict) and "domains" in raw:
        return list(raw["domains"])
    if isinstance(raw, dict):
        return [raw]
    raise ValueError(f"{path} does not contain a domain mapping or a 'domains' list")


def _print_result(name: str, result: ValidationResult) -> None:
    print(f"Candidate: {name}")
    for check in result.checks:
        status = "PASS" if check.passed else "FAIL"
        print(f"  [{status}] {check.name}: {check.detail}")
    print(f"Result: {'PASS' if result.passed else 'FAIL'}")
    print()


def main(argv: Optional[list[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("Usage: python -m src.sources.intake path/to/candidate.yaml", file=sys.stderr)
        return 2

    path = Path(argv[0])
    try:
        candidates = _load_candidates(path)
    except (OSError, yaml.YAMLError, ValueError) as e:
        print(f"Could not load {path}: {e}", file=sys.stderr)
        return 2

    all_passed = True
    for candidate in candidates:
        name = candidate.get("id") or candidate.get("name") or "(unnamed)"
        result = validate_candidate(candidate)
        _print_result(name, result)
        all_passed = all_passed and result.passed

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
