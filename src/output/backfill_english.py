"""Backfill English titles for policies missing ``policy_name_en`` (WP-35).

New policies get ``policy_name_en`` from the analysis LLM call directly
(see ``src/core/llm.py``). This tool catches up policies discovered before
that field existed.

Usage:
    python -m src.output.backfill_english              # translate missing titles
    python -m src.output.backfill_english --dry-run     # preview + cost estimate, no API calls
    python -m src.output.backfill_english --data-dir /srv/policypulse/data
    python -m src.output.backfill_english --limit 20

``--dry-run`` makes zero API calls: it only reads the store and prices the
Haiku calls a real run would make (see ``config/pricing.yaml``). A real run
refuses to start without ANTHROPIC_API_KEY, translates one policy title at a
time with the configured screening model, and never overwrites an existing
``policy_name_en`` (``PolicyStore.update_policy_name_en`` enforces this) - a
second full pass finds nothing left to do.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import anthropic

from ..core.config import ConfigLoader
from ..core.pricing import PricingLoader
from ..storage.store import PolicyStore
from .. import aispend

logger = logging.getLogger(__name__)

# A title-translation call is far smaller than a full-page analysis call -
# these are rough per-policy assumptions for the dry-run cost estimate, not
# measurements (the same caveat config/pricing.yaml's own estimator section
# carries for its full-page numbers).
ESTIMATED_INPUT_TOKENS_PER_POLICY = 200
ESTIMATED_OUTPUT_TOKENS_PER_POLICY = 50

TRANSLATE_PROMPT = """Translate this government policy title to English.
If it is already in English, repeat it exactly unchanged.

Title: {policy_name}

Respond with JSON only: {{"policy_name_en": "the English title"}}
"""


def _missing_policy_name_en(policy: dict) -> bool:
    return not (policy.get("policy_name_en") or "").strip()


@dataclass
class BackfillSummary:
    candidates: int = 0
    translated: int = 0
    skipped_already_had: int = 0
    failed: int = 0
    failed_urls: list[str] = field(default_factory=list)


def select_candidates(store: PolicyStore, limit: Optional[int] = None) -> list[dict]:
    """Policies whose ``policy_name_en`` is missing or blank, capped at ``limit``."""
    candidates = [p for p in store.get_all() if _missing_policy_name_en(p)]
    if limit is not None:
        candidates = candidates[:limit]
    return candidates


def estimate_cost_usd(count: int, pricing: PricingLoader, model: str) -> float:
    """Estimated USD for ``count`` title-translation calls against ``model``."""
    if count <= 0:
        return 0.0
    price = pricing.pricing_for(model)
    return round(
        price.cost_usd(
            count * ESTIMATED_INPUT_TOKENS_PER_POLICY,
            count * ESTIMATED_OUTPUT_TOKENS_PER_POLICY,
        ),
        4,
    )


def dry_run_report(
    store: PolicyStore, config_dir: str = "config", limit: Optional[int] = None,
) -> BackfillSummary:
    """Preview what a real run would translate. Makes zero API calls."""
    candidates = select_candidates(store, limit)
    model = ConfigLoader(config_dir=config_dir).settings.analysis.screening_model
    pricing = PricingLoader(config_dir=config_dir)

    for policy in candidates:
        language = policy.get("source_language") or "Unknown"
        print(f"would translate: {policy.get('policy_name', '')} ({language})")

    cost = estimate_cost_usd(len(candidates), pricing, model)
    plural = "y" if len(candidates) == 1 else "ies"
    print(f"Total: {len(candidates)} polic{plural} to translate")
    print(f"Estimated cost: ${cost:.4f} (model={model})")
    return BackfillSummary(candidates=len(candidates))


def _parse_translation(raw: str) -> str:
    """Extract policy_name_en from a Haiku JSON response."""
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"no JSON object in response: {raw[:200]!r}")
    data = json.loads(raw[start:end + 1])
    translated = data.get("policy_name_en")
    if not translated or not str(translated).strip():
        raise ValueError("model returned an empty policy_name_en")
    return str(translated).strip()


async def _translate_title(
    client: anthropic.AsyncAnthropic, model: str, policy_name: str,
) -> str:
    """One Haiku call: translate a policy title to English."""
    response = await aispend.acreate(
        client, label="backfill_english",
        model=model,
        max_tokens=200,
        temperature=0.0,
        messages=[{
            "role": "user",
            "content": TRANSLATE_PROMPT.format(policy_name=policy_name),
        }],
    )
    return _parse_translation(response.content[0].text)


async def run_backfill(
    store: PolicyStore, api_key: str, config_dir: str = "config",
    limit: Optional[int] = None,
) -> BackfillSummary:
    """Translate titles for policies missing ``policy_name_en``.

    A per-policy failure (API error, unparseable response) is logged and
    skipped - it never aborts the batch. ``PolicyStore.update_policy_name_en``
    refuses to overwrite an existing value, so a policy translated by a
    concurrent run (or an earlier partial run) lands in
    ``skipped_already_had`` instead of being re-translated.
    """
    model = ConfigLoader(config_dir=config_dir).settings.analysis.screening_model
    candidates = select_candidates(store, limit)
    summary = BackfillSummary(candidates=len(candidates))

    client = anthropic.AsyncAnthropic(api_key=api_key)
    try:
        for policy in candidates:
            url = policy.get("url", "")
            try:
                translated = await _translate_title(client, model, policy.get("policy_name", ""))
            except (anthropic.AnthropicError, ValueError, KeyError, TimeoutError) as e:
                # The realistic per-policy failures: any SDK/API error
                # (AnthropicError is the SDK's base, covering connection,
                # rate-limit, and server errors), a malformed response
                # (ValueError/KeyError), or a timeout. One bad title never
                # aborts the batch; anything else is a real bug and raises.
                logger.warning("Translation failed for %s: %s", url, e)
                summary.failed += 1
                summary.failed_urls.append(url)
                continue

            if store.update_policy_name_en(url, translated):
                summary.translated += 1
            else:
                summary.skipped_already_had += 1
    finally:
        await client.close()

    return summary


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill English titles (policy_name_en) for policies "
        "that don't have one yet."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="preview what would be translated and its estimated cost; makes no API calls",
    )
    parser.add_argument(
        "--data-dir", default=None,
        help="override the policy store directory (default: $OCP_DATA_DIR or 'data')",
    )
    parser.add_argument(
        "--config-dir", default=os.environ.get("OCP_CONFIG_DIR", "config"),
        help="config directory for settings.yaml/pricing.yaml (default: config)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="translate at most this many policies",
    )
    args = parser.parse_args(argv)

    # Policy titles are non-English source text (Danish ø/å, German umlauts,
    # French accents, ...); Windows consoles default stdout to a cp1252-style
    # codepage that raises UnicodeEncodeError on them. Force UTF-8 so this
    # never crashes regardless of the terminal's codepage.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=True)

    data_dir = args.data_dir or os.environ.get("OCP_DATA_DIR", "data")
    store = PolicyStore(data_dir=data_dir)

    if args.dry_run:
        dry_run_report(store, config_dir=args.config_dir, limit=args.limit)
        return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY is not set.")
        print("A real backfill run needs it to call the translation model.")
        print("Use --dry-run to preview without an API key.")
        return 1

    summary = asyncio.run(
        run_backfill(store, api_key, config_dir=args.config_dir, limit=args.limit)
    )
    print(f"Translated: {summary.translated}")
    print(f"Skipped (already had policy_name_en): {summary.skipped_already_had}")
    print(f"Failed: {summary.failed}")
    if summary.failed_urls:
        print(f"Failed URLs: {', '.join(summary.failed_urls)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
