"""News tripwire: headlines name a policy weeks before ministries publish it.

Sweeps GDELT (machine-translated global news), Google News RSS per
language, and trade press feeds; deduplicates; optionally triages with
the cheap screening model; and writes surviving items to the lead queue
for a human-gated chase. No expensive analysis happens here.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote_plus

import anthropic
import httpx
from pydantic import BaseModel

from ..core.models import DEFAULT_SCREENING_MODEL
from ..core.urls import normalize_url
from ..notifications.mailer import notify_immediate
from ..storage.leads import Lead, LeadStore
from ..storage.signals_status import FeedFailure, SignalsStatusStore, SweepSummary

logger = logging.getLogger(__name__)

GDELT_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_DELAY_SECONDS = 5.0  # unpublished rate limit; stay polite
USER_AGENT = "OCP-PolicyPulse/1.0"
# A legitimate RSS/GDELT payload is kilobytes, not megabytes. Anything past
# this is treated as a bulkhead failure (logged, zero items) rather than
# handed to the regex parser - bounds worst-case parse time on a hostile or
# broken feed without needing a streaming fetch.
MAX_FEED_BODY_CHARS = 2_000_000

_RSS_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.IGNORECASE | re.DOTALL)
_RSS_FIELD_RES = {
    "title": re.compile(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>",
                        re.IGNORECASE | re.DOTALL),
    "link": re.compile(r"<link>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>",
                       re.IGNORECASE | re.DOTALL),
    "description": re.compile(
        r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>",
        re.IGNORECASE | re.DOTALL),
}

TRIAGE_PROMPT = """You screen news items for a policy-tracking tool.
For each numbered item, decide: does it describe a GOVERNMENT POLICY ACTION
(bill, law, regulation, consultation, mandate, incentive, ruling) related to
heat reuse, waste heat, district heating, or data center energy?

Items:
{items}

RESPOND WITH JSON ONLY - a list, one entry per RELEVANT item:
[{{"index": 0, "relevant": true, "policy_name": "best guess or empty",
   "jurisdiction": "country/region or empty"}}]
Omit irrelevant items entirely.
"""


class NewsItem(BaseModel):
    title: str
    url: str
    snippet: str = ""
    origin_query: str = ""
    jurisdiction_guess: str = ""


def parse_gdelt(payload: str, origin_query: str) -> list[NewsItem]:
    """Parse a GDELT DOC 2.0 artlist JSON payload."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        logger.warning("GDELT payload was not JSON for query %s", origin_query)
        return []
    items = []
    for article in data.get("articles", []):
        url = article.get("url", "")
        title = article.get("title", "")
        if not url or not title:
            continue
        items.append(NewsItem(
            title=title,
            url=url,
            snippet=article.get("sourcecountry", ""),
            origin_query=origin_query,
            jurisdiction_guess=article.get("sourcecountry", ""),
        ))
    return items


def parse_rss(payload: str, origin_query: str) -> list[NewsItem]:
    """Parse RSS <item> entries with regex (no XML parser attack surface).

    Any malformed input (truncated XML, an HTML error page served instead of
    a feed, garbled encoding) degrades to an empty list rather than raising -
    the regex either matches ``<item>...</item>`` blocks or it doesn't. An
    empty body specifically is logged as a warning (WP-42): unlike "no items
    today" (a well-formed feed with none), a zero-length response usually
    means the feed broke.
    """
    if not (payload or "").strip():
        logger.warning("RSS payload was empty for %s", origin_query)
        return []
    items = []
    for block in _RSS_ITEM_RE.findall(payload or ""):
        fields = {}
        for name, pattern in _RSS_FIELD_RES.items():
            match = pattern.search(block)
            fields[name] = (match.group(1).strip() if match else "")
        if not fields["title"] or not fields["link"]:
            continue
        items.append(NewsItem(
            title=fields["title"],
            url=fields["link"],
            snippet=fields["description"][:300],
            origin_query=origin_query,
        ))
    return items


def dedupe_items(items: list[NewsItem]) -> list[NewsItem]:
    """Drop repeats by normalized URL and by normalized title.

    URL comparison goes through ``normalize_url`` (WP-42) so http/https,
    a trailing slash, tracking params, and Google News redirect wrappers
    collapse to the same key within a batch; the stored item keeps its
    original URL untouched.
    """
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    unique = []
    for item in items:
        url_key = normalize_url(item.url)
        title_key = " ".join(item.title.lower().split())
        if url_key in seen_urls or title_key in seen_titles:
            continue
        seen_urls.add(url_key)
        seen_titles.add(title_key)
        unique.append(item)
    return unique


class _FeedFetchError(Exception):
    """Internal: a feed responded but not usably (bad status, oversized body).

    Kept separate from httpx's transport-level exceptions so the per-feed
    bulkhead can catch exactly "this feed did not work", never a broader
    ``Exception`` that would also swallow real bugs in this module.
    """


async def _fetch_feed_text(client: httpx.AsyncClient, url: str) -> str:
    """Fetch one feed URL. Raises on anything a bulkhead caller should treat
    as this feed's failure, never as a signal to stop the sweep."""
    resp = await client.get(url)
    if resp.status_code >= 400:
        raise _FeedFetchError(f"HTTP {resp.status_code}")
    text = resp.text
    if len(text) > MAX_FEED_BODY_CHARS:
        raise _FeedFetchError(f"body exceeded {MAX_FEED_BODY_CHARS} chars, skipped")
    return text


async def _collect(config: dict) -> tuple[list[NewsItem], dict]:
    """Fetch every configured source. Returns ``(items, sweep_stats)``.

    Each query/feed is its own bulkhead (WP-42): a transport failure, a
    non-2xx status, or an oversized body is caught here, logged, and
    recorded in ``sweep_stats['failures']`` - it never propagates, so one
    bad feed can never stop the rest of the sweep from running.
    """
    items: list[NewsItem] = []
    feeds_tried = 0
    feeds_ok = 0
    failures: list[dict] = []
    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        gdelt_cfg = config.get("gdelt", {})
        if gdelt_cfg.get("enabled"):
            for i, query in enumerate(gdelt_cfg.get("queries", [])):
                if i:
                    await asyncio.sleep(GDELT_DELAY_SECONDS)
                q = query.get("q", "")
                url = (
                    f"{GDELT_ENDPOINT}?query={quote_plus(q)}&mode=artlist"
                    f"&format=json&timespan={query.get('timespan', '1w')}"
                )
                feeds_tried += 1
                try:
                    text = await _fetch_feed_text(client, url)
                    items.extend(parse_gdelt(text, origin_query=q))
                    feeds_ok += 1
                except (httpx.HTTPError, _FeedFetchError) as e:
                    logger.warning("GDELT fetch failed for %s: %s", q, e)
                    failures.append({"name": f"gdelt:{q}", "detail": str(e)})

        gn_cfg = config.get("google_news", {})
        if gn_cfg.get("enabled"):
            for query in gn_cfg.get("queries", []):
                q, hl = query.get("q", ""), query.get("hl", "en")
                gl = query.get("gl", "US")
                url = (
                    "https://news.google.com/rss/search?"
                    f"q={quote_plus(q)}&hl={hl}&gl={gl}&ceid={gl}:{hl}"
                )
                feeds_tried += 1
                try:
                    text = await _fetch_feed_text(client, url)
                    items.extend(parse_rss(text, origin_query=q))
                    feeds_ok += 1
                except (httpx.HTTPError, _FeedFetchError) as e:
                    logger.warning("Google News fetch failed for %s: %s", q, e)
                    failures.append({"name": f"google_news:{q}", "detail": str(e)})

        for feed in config.get("rss_feeds", []):
            feed_name = feed.get("name", feed.get("url", "?"))
            feeds_tried += 1
            try:
                text = await _fetch_feed_text(client, feed["url"])
                items.extend(parse_rss(text, origin_query=f"feed:{feed_name}"))
                feeds_ok += 1
            except (httpx.HTTPError, _FeedFetchError) as e:
                logger.warning("Feed fetch failed for %s: %s", feed_name, e)
                failures.append({"name": f"feed:{feed_name}", "detail": str(e)})

    sweep_stats = {
        "feeds_tried": feeds_tried,
        "feeds_ok": feeds_ok,
        "feeds_failed": feeds_tried - feeds_ok,
        "failures": failures,
    }
    return items, sweep_stats


async def _triage(
    items: list[NewsItem], api_key: str, model: str,
) -> list[NewsItem]:
    """Keep only items the screening model marks as policy actions."""
    numbered = "\n".join(
        f"{i}. {item.title} - {item.snippet[:120]}"
        for i, item in enumerate(items)
    )
    client = anthropic.AsyncAnthropic(api_key=api_key)
    try:
        response = await client.messages.create(
            model=model,
            max_tokens=1500,
            temperature=0.0,
            messages=[{
                "role": "user",
                "content": TRIAGE_PROMPT.format(items=numbered),
            }],
        )
        raw = response.content[0].text
        start, end = raw.find("["), raw.rfind("]")
        verdicts = json.loads(raw[start:end + 1]) if start >= 0 else []
    except Exception as e:
        logger.warning("News triage failed (%s); keeping all items", e)
        return items

    kept = []
    for verdict in verdicts:
        try:
            index = int(verdict.get("index"))
        except (TypeError, ValueError):
            continue
        if not verdict.get("relevant") or not (0 <= index < len(items)):
            continue
        item = items[index]
        if verdict.get("jurisdiction"):
            item.jurisdiction_guess = verdict["jurisdiction"]
        if verdict.get("policy_name"):
            item.snippet = f"{verdict['policy_name']} - {item.snippet}"
        kept.append(item)
    return kept


async def run_news_signals(
    config: dict, lead_store: LeadStore, api_key: Optional[str],
    model: str = DEFAULT_SCREENING_MODEL,
) -> dict:
    """Run one news sweep. Returns a summary dict for logs/notifications.

    Also persists the same summary to the kv table (name=``signals_last_sweep``,
    see ``src.storage.signals_status``) for WP-43's status surface, and logs
    it as one structured line - the only per-run log line this function emits.
    """
    if not config.get("enabled"):
        return {"enabled": False, "items_seen": 0, "leads_added": 0}

    raw_items, sweep_stats = await _collect(config)
    items = dedupe_items(raw_items)
    seen = len(items)

    if items and api_key:
        items = await _triage(items, api_key, model)

    cap = config.get("max_leads_per_run", 50)
    items = items[:cap]

    leads = [
        Lead(
            title=item.title,
            source_url=item.url,
            snippet=item.snippet,
            jurisdiction_guess=item.jurisdiction_guess,
            origin="news",
        )
        for item in items
    ]
    added = lead_store.add_leads(leads)

    logger.info(
        "News sweep complete: feeds_tried=%d feeds_ok=%d feeds_failed=%d "
        "items_found=%d items_kept=%d leads_added=%d",
        sweep_stats["feeds_tried"], sweep_stats["feeds_ok"], sweep_stats["feeds_failed"],
        len(raw_items), len(items), added,
    )

    summary = SweepSummary(
        ts=datetime.now(timezone.utc).isoformat(),
        feeds_tried=sweep_stats["feeds_tried"],
        feeds_ok=sweep_stats["feeds_ok"],
        feeds_failed=sweep_stats["feeds_failed"],
        items_found=len(raw_items),
        items_kept=len(items),
        leads_added=added,
        failures=[FeedFailure(**f) for f in sweep_stats["failures"]],
    )
    SignalsStatusStore(data_dir=str(lead_store.data_dir)).record(summary)

    if summary.feeds_failed > 0:
        lines = [f"The news sweep had {summary.feeds_failed} feed failure(s):", ""]
        lines += [f"- {f.name}: {f.detail}" for f in summary.failures]
        lines += ["", "Open the admin page to act on these."]
        notify_immediate(
            "ops_alerts",
            "PolicyPulse: news sweep had feed failures",
            "\n".join(lines),
            data_dir=str(lead_store.data_dir),
        )

    # Built from the persisted summary's own fields so the returned dict,
    # the kv record, and the /api/signals/status payload cannot drift apart.
    return {
        "enabled": True,
        "items_seen": seen,
        "leads_added": summary.leads_added,
        "feeds_tried": summary.feeds_tried,
        "feeds_ok": summary.feeds_ok,
        "feeds_failed": summary.feeds_failed,
        "items_found": summary.items_found,
        "items_kept": summary.items_kept,
        "failures": sweep_stats["failures"],
    }
