"""Place-first search planning.

Admins think "waste heat rules in California", not "run the law_apis channel
against the us_states group". This module translates a place (plus optional
topic terms) into a concrete, reviewable scan plan: which sources will be
searched, what it will roughly cost, and why — before any money is spent.

The plan's `targets` string is directly usable as ScanRequest.domains
(comma-separated union) and `source_params` flows to structured sources.
"""

import os
import re
from functools import lru_cache
from typing import Optional

from src.agent.domain_generator import US_STATE_ABBREVS

# Region keys that name a group of places rather than one place.
_GROUP_KEYS = {
    "europe", "nordic", "eu_central", "eu_west", "eu_south", "eu_east",
    "us_states", "apac", "north_america", "south_america", "middle_east",
    "africa",
}

# EU member states present in the config's region vocabulary — a search for
# any of these should also watch EUR-Lex transposition (early signal: a
# directive's national implementing law shows up there first).
_EU_MEMBERS = {
    "austria", "belgium", "czech_republic", "denmark", "estonia", "finland",
    "france", "germany", "greece", "hungary", "ireland", "italy",
    "luxembourg", "netherlands", "poland", "portugal", "romania", "spain",
    "sweden",
}

# Sub-national places -> parent country, so e.g. Scotland inherits the UK
# Parliament bills source and Hesse inherits Germany's DIP.
_SUBNATIONAL_PARENT = {
    "scotland": "uk", "wales": "uk", "northern_ireland": "uk",
    "zurich": "switzerland",
    "hessen": "germany", "bayern": "germany",
    "nordrhein_westfalen": "germany", "baden_wuerttemberg": "germany",
    "berlin": "germany", "hamburg": "germany",
    "niedersachsen": "germany", "sachsen": "germany",
    "new_south_wales": "australia", "south_australia": "australia",
    "karnataka": "india", "tamil_nadu": "india",
    "telangana": "india", "maharashtra": "india",
    "ontario": "canada", "british_columbia": "canada",
    "quebec": "canada", "alberta": "canada",
    "abu_dhabi": "uae", "dubai": "uae",
}

_US_ALIASES = {
    "us", "usa", "u.s.", "u.s.a.", "united states",
    "united states of america", "america", "federal",
}
_UK_ALIASES = {"uk", "u.k.", "united kingdom", "britain", "great britain", "england"}
_EU_ALIASES = {"eu", "e.u.", "european union", "eu member states"}
_COUNTRY_ALIASES = {
    "czechia": "czech_republic",
    "holland": "netherlands",
    "korea": "south_korea",
    "united arab emirates": "uae",
    "emirates": "uae",
}

# US state display-name -> (region_key, postal code)
_STATE_BY_NAME = {
    key.replace("-", " "): (key.replace("-", "_"), code.upper())
    for key, code in US_STATE_ABBREVS.items()
}

# How each structured source is explained to the admin. Descriptions say
# what the source finds and why it matters for finding policies early.
_SOURCE_INFO = {
    "legiscan": (
        "law_api", "LEGISCAN_API_KEY",
        "Bills in all 50 US state legislatures — the earliest signal for US "
        "state policy (catches bills at introduction)",
    ),
    "camara": (
        "law_api", None,
        "Bills in Brazil's Chamber of Deputies - a fast-growing data "
        "centre market with a tax-incentive push and no other coverage "
        "here",
    ),
    "diavgeia": (
        "law_api", None,
        "Greece's transparency register, where by law every government "
        "act must be posted before it takes effect - so nothing enacted "
        "is missing",
    ),
    "egov_japan": (
        "law_api", None,
        "Enacted Japanese law with full-text search - the counterpart "
        "to the Diet proceedings source, showing what actually became "
        "law",
    ),
    "eu_have_your_say": (
        "law_api", None,
        "European Commission initiatives while they are still open for "
        "feedback - the earliest signal of any source here, months "
        "before a law exists",
    ),
    "govuk": (
        "law_api", None,
        "UK consultations and calls for evidence - catches the shape of "
        "a policy while it is still being decided, before any bill is "
        "drafted",
    ),
    "kokkai": (
        "law_api", None,
        "What is being said in the Japanese Diet, rather than what it "
        "has passed - a leading indicator, so most of what it finds is "
        "correctly set aside",
    ),
    "nz_pco": (
        "law_api", None,
        "New Zealand legislation with genuine full-text search, from "
        "the official drafting office",
    ),
    "oireachtas": (
        "law_api", None,
        "Bills before the Irish parliament, with full text - Ireland is "
        "a top EU data centre market and heat reuse is already a "
        "planning condition there",
    ),
    "pmg": (
        "law_api", None,
        "South African parliamentary committee activity and open "
        "comment windows",
    ),
    "riigikogu": (
        "law_api", None,
        "Documents before the Estonian parliament",
    ),
    "ris_austria": (
        "law_api", None,
        "Austria's official legal information system - enacted federal "
        "law and the consolidated statute book",
    ),
    "sejm": (
        "law_api", None,
        "Prints and bills before the Polish Sejm",
    ),
    "stortinget": (
        "law_api", None,
        "Cases before the Norwegian Storting - Norway is amending its "
        "Energy Act to cover surplus heat from data centres, so this is "
        "a live one",
    ),
    "tweede_kamer": (
        "law_api", None,
        "Documents before the Dutch parliament - the Netherlands "
        "already requires some data centres to explore heat reuse",
    ),
    "va_lis": (
        "law_api", None,
        "Every bill in a Virginia legislative session, from the state's own "
        "published session files - no key needed, and the most active US "
        "state for data centre legislation",
    ),
    "govinfo": (
        "law_api", "GOVINFO_API_KEY",
        "US federal legislation from Congress",
    ),
    "regulations_gov": (
        "law_api", "REGULATIONSGOV_API_KEY",
        "US federal rulemaking dockets — catches proposed rules while the "
        "public comment window is still open",
    ),
    "riksdagen": (
        "law_api", None,
        "Swedish Parliament (Riksdag) documents, including bills in progress",
    ),
    "folketing": (
        "law_api", None,
        "Danish Parliament (Folketing) documents, including bills in progress",
    ),
    "uk_bills": (
        "law_api", None,
        "UK Parliament bills currently moving through Westminster",
    ),
    "legisinfo": (
        "law_api", None,
        "Canadian Parliament bills tracked by LEGISinfo",
    ),
    "dip": (
        "law_api", "DIP_API_KEY",
        "German Bundestag and Bundesrat parliamentary documents",
    ),
    "eurlex_nim": (
        "transposition", None,
        "EU directive transposition tracker — flags a country's implementing "
        "law as soon as it is notified to the Commission",
    ),
}

_KIND_ORDER = {"law_api": 0, "transposition": 1, "website": 2}

# Rough LLM cost ceilings (USD). Deliberately generous so the real bill
# comes in under the preview, never over.
_PER_STRUCTURED_DOC = {"low": 0.004, "standard": 0.012, "high": 0.06}
_PER_CRAWL_DOMAIN = {"low": 0.05, "standard": 0.15, "high": 0.60}


def _normalize(text: str) -> str:
    return " ".join((text or "").replace("_", " ").replace("-", " ").split()).lower()


# Natural phrasings people actually type or that older UI suggestions used.
_PLACE_ALIASES = {
    "us states": "us_states",
    "us state governments": "us_states",
    "all us states": "us_states",
    "asia pacific": "apac",
    "nordics": "nordic",
    "the nordics": "nordic",
    "scandinavia": "nordic",
}


def resolve_place(query: str) -> dict:
    """Resolve free text ("California", "EU", "Nordic") into a place dict."""
    low = _normalize(query)
    # "United States (federal and state)" -> "united states"
    low = re.sub(r"\s*\([^)]*\)\s*$", "", low).strip()
    # "Nordic countries" -> "nordic"
    low = re.sub(r"\s+countries$", "", low).strip()
    low = _PLACE_ALIASES.get(low, low)
    if not low:
        return {"query": query, "kind": "unknown", "region_key": "", "display": ""}

    if low in _US_ALIASES:
        return {
            "query": query, "kind": "us_federal",
            "region_key": "us", "display": "United States",
        }
    if low in _STATE_BY_NAME:
        region_key, code = _STATE_BY_NAME[low]
        return {
            "query": query, "kind": "us_state", "region_key": region_key,
            "display": low.title(), "state_code": code,
        }
    if low in _UK_ALIASES:
        return {
            "query": query, "kind": "country",
            "region_key": "uk", "display": "United Kingdom",
        }
    if low in _EU_ALIASES:
        return {
            "query": query, "kind": "eu",
            "region_key": "eu", "display": "European Union",
        }

    key = _COUNTRY_ALIASES.get(low, low.replace(" ", "_"))
    from .config import VALID_REGIONS  # local import avoids cycle at module load
    if key in _GROUP_KEYS:
        return {
            "query": query, "kind": "region_group",
            "region_key": key, "display": VALID_REGIONS.get(key, low.title()),
        }
    if key in VALID_REGIONS:
        return {
            "query": query, "kind": "country",
            "region_key": key, "display": VALID_REGIONS[key],
        }
    return {"query": query, "kind": "unknown", "region_key": "", "display": query}


# Group-level suggestions, curated so each reads naturally AND resolves.
_GROUP_SUGGESTIONS = [
    "European Union", "United States", "United Kingdom",
    "Nordic", "Europe", "North America", "South America",
    "Middle East", "Africa", "APAC",
]


@lru_cache(maxsize=1)
def _suggested_places_cached() -> tuple[str, ...]:
    return tuple(_build_suggested_places())


def suggested_places() -> list[str]:
    """Alphabetized place names for the search box - every entry resolves.

    Cached: the inputs (VALID_REGIONS, state list) are fixed at runtime,
    so the ~360 resolver calls run once per process, not per request.
    """
    return list(_suggested_places_cached())


def _build_suggested_places() -> list[str]:
    """Generate the suggestion list - every entry resolves.

    Regression guard: the UI once suggested region DESCRIPTIONS
    ("US state governments") that resolve_place rejected. Suggestions are
    generated from names, then filtered through the resolver itself so a
    suggestion that stops resolving fails tests instead of failing users.
    """
    from .config import VALID_REGIONS

    names: set[str] = set(_GROUP_SUGGESTIONS)
    names.update(name.title() for name in _STATE_BY_NAME)
    for key, display in VALID_REGIONS.items():
        if key in _GROUP_KEYS or key in ("us", "uk", "eu", "us_states"):
            continue
        for candidate in (display, key.replace("_", " ").title()):
            if resolve_place(candidate)["kind"] != "unknown":
                names.add(candidate)
                break

    return sorted(
        (n for n in names if resolve_place(n)["kind"] != "unknown"),
        key=str.lower,
    )


def _coverage_keys(place: dict) -> set[str]:
    """Region tags a structured source may carry to cover this place."""
    kind = place["kind"]
    key = place["region_key"]
    if kind == "us_state":
        return {key, "us_states", "us"}
    if kind == "us_federal":
        return {"us", "us_states"}
    keys = {key}
    parent = _SUBNATIONAL_PARENT.get(key)
    if parent:
        keys.add(parent)
    return keys


def _wants_transposition(place: dict) -> bool:
    key = place["region_key"]
    parent = _SUBNATIONAL_PARENT.get(key)
    return (
        place["kind"] == "eu"
        or key in ("eu", "europe")
        or key in _EU_MEMBERS
        or parent in _EU_MEMBERS
    )


def _describe(domain: dict) -> dict:
    source_type = domain.get("source_type", "crawl")
    if source_type == "crawl":
        kind, env, description = (
            "website", None, f"Government website: {domain.get('name', domain['id'])}",
        )
    else:
        kind, env, description = _SOURCE_INFO.get(
            source_type, ("law_api", None, domain.get("name", domain["id"])),
        )
    return {
        "id": domain["id"],
        "name": domain.get("name", domain["id"]),
        "kind": kind,
        "description": description,
        "requires_key": env is not None,
        "key_present": bool(os.environ.get(env)) if env else True,
        "key_env": env,
    }


def build_search_plan(
    place_query: str,
    terms: Optional[list[str]],
    config,
    cost_level: str = "standard",
) -> dict:
    """Build a reviewable plan for a place-first policy search."""
    place = resolve_place(place_query)
    warnings: list[str] = []

    if place["kind"] == "unknown":
        return {
            "place": place,
            "terms": terms or [],
            "targets": "",
            "channels": [],
            "source_params": {},
            "sources": [],
            "estimate": {"legiscan": None, "llm_ceiling_usd": 0.0, "cost_level": cost_level},
            "warnings": [
                f"Could not recognize '{place_query}'. Try a country (\"Sweden\"), "
                "a US state (\"California\"), or a region (\"Nordic\", \"EU\")."
            ],
        }

    all_domains = [
        d for d in config.domains_config.get("domains", [])
        if d.get("enabled", True)
    ]
    region_key = place["region_key"]
    coverage = _coverage_keys(place)

    crawl_domains = [
        d for d in all_domains
        if d.get("source_type", "crawl") == "crawl"
        and region_key in d.get("region", [])
    ]
    structured = [
        d for d in all_domains
        if d.get("source_type", "crawl") not in ("crawl", "eurlex_nim")
        and coverage & set(d.get("region", []))
    ]
    if _wants_transposition(place):
        structured += [
            d for d in all_domains if d.get("source_type") == "eurlex_nim"
        ]

    sources = sorted(
        (_describe(d) for d in {d["id"]: d for d in (crawl_domains + structured)}.values()),
        key=lambda s: (_KIND_ORDER[s["kind"]], s["name"]),
    )

    # Targets: the region key resolves every domain tagged with it (crawl and
    # structured alike); structured ids are added explicitly since sources
    # like LegiScan are not tagged per-state.
    target_parts: list[str] = []
    if crawl_domains:
        target_parts.append(region_key)
    target_parts += sorted(
        {d["id"] for d in structured}
        | {d["id"] for d in all_domains
           if d.get("source_type", "crawl") != "crawl"
           and region_key in d.get("region", [])}
    )
    targets = ",".join(dict.fromkeys(target_parts))

    channels = []
    kinds = {s["kind"] for s in sources}
    if "website" in kinds:
        channels.append("crawl")
    if "law_api" in kinds:
        channels.append("law_apis")
    if "transposition" in kinds:
        channels.append("transposition")

    source_params: dict = {}
    if place["kind"] == "us_state":
        source_params["state"] = place["state_code"]
    if terms:
        source_params["terms"] = terms

    for s in sources:
        if s["requires_key"] and not s["key_present"]:
            warnings.append(
                f"{s['key_env']} not set — {s['name']} will be skipped this run."
            )
    if not sources:
        warnings.append(
            f"No sources cover {place['display']} yet. An admin can add "
            "government websites for it via the assistant."
        )
    elif not crawl_domains:
        warnings.append(
            f"No government websites are configured for {place['display']} yet "
            "— searching law databases only."
        )

    # Cost estimate: a deliberate ceiling, not a promise.
    legiscan_estimate = None
    if any(s["id"].startswith("legiscan") for s in sources):
        from ..sources.legiscan import (
            DEFAULT_MAX_API_CALLS, DEFAULT_MAX_DOCUMENTS, DEFAULT_TERMS,
            monthly_usage,
        )
        n_terms = len(terms) if terms else len(DEFAULT_TERMS)
        legiscan_estimate = {
            "max_queries": min(DEFAULT_MAX_API_CALLS, n_terms + DEFAULT_MAX_DOCUMENTS),
            **monthly_usage(),
        }

    level = cost_level if cost_level in _PER_STRUCTURED_DOC else "standard"
    n_structured = sum(1 for s in sources if s["kind"] != "website")
    llm_ceiling = round(
        n_structured * 25 * _PER_STRUCTURED_DOC[level]
        + len(crawl_domains) * _PER_CRAWL_DOMAIN[level],
        2,
    )

    return {
        "place": place,
        "terms": terms or [],
        "targets": targets,
        "channels": channels,
        "source_params": source_params,
        "sources": sources,
        "estimate": {
            "legiscan": legiscan_estimate,
            "llm_ceiling_usd": llm_ceiling,
            "cost_level": level,
        },
        "warnings": warnings,
    }
