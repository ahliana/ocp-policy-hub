"""Tool definitions and dispatch for the standalone agent loop.

Defines 15 tools in Anthropic API format:
- 13 policy hub tools (same as MCP server)
- 1 built-in web_search (server-side, no dispatch needed)
- 1 add_domain tool (creates new domain YAML configs)
"""

import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..core.config import ConfigLoader, ConfigurationError
from ..core.crawler import AsyncCrawler
from ..core.extractor import HtmlExtractor
from ..core.keywords import build_keyword_matcher
from ..core.llm import ClaudeClient, LLMError
from ..core.log_setup import log_audit_event
from ..core.verifier import Verifier
from ..orchestration.scan_manager import ScanManager
from ..storage.store import PolicyStore
import yaml

from .domain_generator import (
    US_STATE_ABBREVS as _US_STATES,
    generate_domain_id,
    detect_region,
    suggest_output_file,
    build_domain_entry,
    format_domain_yaml,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Jurisdiction matching for search_policies
# ---------------------------------------------------------------------------
# Countries are stored inconsistently ("US", "Sweden (EU)", "Finland
# (EU-wide regulation)") while reader questions phrase them in full
# ("United States", "USA"). Match on shared aliases plus word-boundary
# containment, so "United States" finds "US" but "US" never matches
# "Belarus".
_JURISDICTION_ALIAS_GROUPS: list[set[str]] = [
    {"us", "usa", "united states", "united states of america", "america"},
    {"uk", "united kingdom", "britain", "great britain"},
    {"eu", "european union"},
    {"uae", "united arab emirates"},
    {"south korea", "korea", "republic of korea"},
    {"czech republic", "czechia"},
]


def _word_in(needle: str, haystack: str) -> bool:
    """True if needle appears as a whole word/phrase in haystack."""
    return re.search(rf"\b{re.escape(needle)}\b", haystack) is not None


def _jurisdiction_aliases(text: str) -> set[str]:
    """Expand a jurisdiction string to itself plus any alias-group members."""
    aliases = {text}
    for group in _JURISDICTION_ALIAS_GROUPS:
        if any(_word_in(member, text) for member in group):
            aliases |= group
    return aliases


def jurisdiction_matches(query: str, stored: str) -> bool:
    """Whether a jurisdiction query should match a stored jurisdiction value."""
    q = (query or "").strip().lower()
    if not q:
        return True
    s = (stored or "").strip().lower()
    if not s:
        return False

    query_aliases = _jurisdiction_aliases(q)
    stored_aliases = _jurisdiction_aliases(s)
    if query_aliases & stored_aliases:
        return True

    # Word-boundary containment either direction: "Sweden" -> "Sweden (EU)".
    return any(
        _word_in(qa, sa) or _word_in(sa, qa)
        for qa in query_aliases
        for sa in stored_aliases
    )


# ---------------------------------------------------------------------------
# Region -> groups mapping for auto-assignment when adding domains
# ---------------------------------------------------------------------------
REGION_TO_GROUPS: dict[str, list[str]] = {
    # Broad groups
    "us_states": ["us_states", "us"],
    "eu": ["eu"],
    "nordic": ["nordic"],
    "eu_central": ["eu_central", "eu"],
    "eu_west": ["eu_west", "eu"],
    "eu_south": ["eu_south", "eu"],
    "eu_east": ["eu_east", "eu"],
    "apac": ["apac"],
    "north_america": ["north_america"],
    "south_america": ["south_america"],
    "middle_east": ["middle_east"],
    "africa": ["africa"],
    # EU countries
    "spain": ["eu_south", "eu"],
    "italy": ["eu_south", "eu"],
    "portugal": ["eu_south", "eu"],
    "greece": ["eu_south", "eu"],
    "poland": ["eu_east", "eu"],
    "czech_republic": ["eu_east", "eu"],
    "hungary": ["eu_east", "eu"],
    "romania": ["eu_east", "eu"],
    "germany": ["eu_central", "eu"],
    "france": ["eu_central", "eu"],
    "netherlands": ["eu_west", "eu"],
    "ireland": ["eu_west", "eu"],
    "belgium": ["eu_west", "eu"],
    "austria": ["eu_central", "eu"],
    "switzerland": ["eu_central"],
    "finland": ["nordic", "eu"],
    "iceland": ["nordic"],
    "estonia": ["eu_east", "eu"],
    "luxembourg": ["eu_west", "eu"],
    # UK
    "uk": ["uk"],
    "scotland": ["uk"],
    "wales": ["uk"],
    "northern_ireland": ["uk"],
    # Nordic
    "denmark": ["nordic", "eu"],
    "sweden": ["nordic", "eu"],
    "norway": ["nordic", "eu"],
    # Swiss cantons
    "zurich": ["eu_central", "switzerland"],
    # German Länder
    "hessen": ["eu_central", "germany", "eu"],
    "bayern": ["eu_central", "germany", "eu"],
    "nordrhein_westfalen": ["eu_central", "germany", "eu"],
    "baden_wuerttemberg": ["eu_central", "germany", "eu"],
    "berlin": ["eu_central", "germany", "eu"],
    "hamburg": ["eu_central", "germany", "eu"],
    "niedersachsen": ["eu_central", "germany", "eu"],
    "sachsen": ["eu_central", "germany", "eu"],
    # APAC
    "singapore": ["apac"],
    "japan": ["apac"],
    "south_korea": ["apac"],
    "australia": ["apac"],
    "new_zealand": ["apac"],
    "india": ["apac"],
    "new_south_wales": ["australia", "apac"],
    "south_australia": ["australia", "apac"],
    "karnataka": ["india", "apac"],
    "tamil_nadu": ["india", "apac"],
    "telangana": ["india", "apac"],
    "maharashtra": ["india", "apac"],
    # Americas
    "canada": ["north_america"],
    "ontario": ["canada", "north_america"],
    "british_columbia": ["canada", "north_america"],
    "quebec": ["canada", "north_america"],
    "alberta": ["canada", "north_america"],
    "brazil": ["south_america"],
    "mexico": ["north_america"],
    # Middle East
    "uae": ["middle_east"],
    "abu_dhabi": ["middle_east", "uae"],
    "dubai": ["middle_east", "uae"],
    "saudi_arabia": ["middle_east"],
    # Africa
    "south_africa": ["africa"],
}

# Auto-add US state names so add_domain auto-assigns to us_states/us groups
REGION_TO_GROUPS.update({
    state.replace("-", "_"): ["us_states", "us"]
    for state in _US_STATES
})

# ---------------------------------------------------------------------------
# Tool definitions — Anthropic API format
# ---------------------------------------------------------------------------

# The 11 existing tools (same schemas as MCP server, just different key name)
POLICY_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_domains",
        "description": (
            "List government websites in the database. Filter by group "
            "(e.g. 'eu', 'nordic', 'us'), category, tags, or region. "
            "Use group='all' to see everything."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "group": {"type": "string", "description": "Domain group: 'quick', 'eu', 'nordic', 'dach', 'north_america', 'asia_pacific', 'all'"},
                "category": {"type": "string", "description": "Filter by category (e.g. 'energy_ministry')"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Filter by tags"},
                "region": {"type": "string", "description": "Filter by region (e.g. 'eu', 'us', 'apac')"},
            },
        },
    },
    {
        "name": "list_groups",
        "description": (
            "List all available scan targets: named groups (eu, nordic, us_states), "
            "regions, and US states. Shows domain counts for each. Use this to help "
            "users discover what they can scan."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Filter: 'groups' (named groups only), 'states' (US states only), or 'all' (everything)",
                    "default": "all",
                },
            },
        },
    },
    {
        "name": "get_domain_config",
        "description": "Get full configuration for a specific government website by its ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "domain_id": {"type": "string", "description": "Domain ID (e.g. 'bmwk_de', 'uk_legislation')"},
            },
            "required": ["domain_id"],
        },
    },
    {
        "name": "start_scan",
        "description": (
            "Start scanning government websites to discover policies. "
            "Runs in the background — use get_scan_status to check progress. "
            "Returns a scan_id immediately."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "domains": {"type": "string", "default": "quick", "description": "Which domains to scan: 'quick', 'eu', 'nordic', 'dach', 'north_america', 'asia_pacific', 'all'"},
                "max_concurrent": {"type": "integer", "default": 5, "description": "Max parallel workers (1-20)"},
                "skip_llm": {"type": "boolean", "default": False, "description": "Skip AI analysis (keyword-only mode)"},
            },
        },
    },
    {
        "name": "get_scan_status",
        "description": "Check the progress of a running or completed scan. Shows per-domain status, policies found, and costs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "scan_id": {"type": "string", "description": "Scan ID from start_scan"},
            },
            "required": ["scan_id"],
        },
    },
    {
        "name": "stop_scan",
        "description": "Cancel a running scan.",
        "input_schema": {
            "type": "object",
            "properties": {
                "scan_id": {"type": "string", "description": "Scan ID to cancel"},
            },
            "required": ["scan_id"],
        },
    },
    {
        "name": "analyze_url",
        "description": (
            "Analyze a single webpage for policy content. Fetches the page, "
            "extracts text, checks for policy keywords, and uses AI to identify "
            "and classify any policies found."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to analyze (e.g. 'https://example.gov/policy')"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "match_keywords",
        "description": "Test keyword scoring on arbitrary text. Shows which policy-related keywords are found and the relevance score.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to check for policy keywords"},
                "language": {"type": "string", "default": "en", "description": "Language code (en, de, fr, etc.)"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "search_policies",
        "description": "Search through discovered policies. Filter by country, policy type, relevance score, or keywords.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Full-text search keywords, matched against policy name, "
                        "summary, key requirements, and jurisdiction. All words "
                        "must match (AND); the last word also matches as a prefix."
                    ),
                },
                "jurisdiction": {"type": "string", "description": "Country or jurisdiction (e.g. 'Germany', 'Denmark')"},
                "policy_type": {"type": "string", "description": "Type: 'law', 'regulation', 'directive', 'incentive', 'grant', 'plan', 'standard'"},
                "min_score": {"type": "integer", "description": "Minimum relevance score (1-10)"},
            },
        },
    },
    {
        "name": "get_policy_stats",
        "description": "Get an overview of all discovered policies — totals by country, type, and flagged items.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_audit_advisory",
        "description": "Get AI-generated insights and recommendations from a completed scan.",
        "input_schema": {
            "type": "object",
            "properties": {
                "scan_id": {"type": "string", "description": "Scan ID"},
            },
            "required": ["scan_id"],
        },
    },
    {
        "name": "estimate_cost",
        "description": "Estimate the API cost before running a scan. Shows expected number of pages, AI calls, and dollar cost.",
        "input_schema": {
            "type": "object",
            "properties": {
                "domains": {"type": "string", "description": "Domain group to estimate (e.g. 'eu', 'quick', 'all')"},
            },
            "required": ["domains"],
        },
    },
    {
        "name": "list_scans",
        "description": (
            "List all scans in this session — running, completed, and failed. "
            "Shows scan IDs, status, domain groups, and policy counts. Use this "
            "when you need to find a scan ID or check what scans have been done."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]

# add_domain tool
ADD_DOMAIN_TOOL: dict[str, Any] = {
    "name": "add_domain",
    "description": (
        "Add a new government website to the database. Fetches the URL to "
        "auto-detect the site name, language, and region. Creates a YAML "
        "config file so the site can be scanned in future runs."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL of the government website to add"},
            "name": {"type": "string", "description": "Optional: override the auto-detected site name"},
        },
        "required": ["url"],
    },
}

# Anthropic's built-in web search tool (server-side, no dispatch needed)
WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "web_search_20250305",
    "name": "web_search",
}


def get_all_tools() -> list[dict[str, Any]]:
    """Return all 15 tools for messages.create(tools=...)."""
    return POLICY_TOOLS + [ADD_DOMAIN_TOOL, WEB_SEARCH_TOOL]


# ---------------------------------------------------------------------------
# Tool dispatch — executes tools locally
# ---------------------------------------------------------------------------

async def execute_tool(
    name: str,
    arguments: dict[str, Any],
    config: ConfigLoader,
    scan_manager: ScanManager,
) -> dict[str, Any]:
    """Execute a tool and return the result as a dict.

    This mirrors the dispatch logic in src/mcp/server.py but returns
    plain dicts instead of TextContent.

    The web_search tool is server-side and never reaches this function.
    """
    try:
        if name == "list_domains":
            group = arguments.get("group", "all")
            domains = config.get_enabled_domains(group)
            # Same overlay the API routes and ScanManager apply - without it,
            # a domain disabled in the admin Sources panel still shows as
            # available through agent chat.
            from ..core.overrides import apply_domain_overrides
            from ..storage.domain_overrides import DomainOverridesStore
            domains = apply_domain_overrides(
                domains,
                DomainOverridesStore(data_dir=scan_manager.data_dir).get_all(),
            )
            category = arguments.get("category")
            tags = arguments.get("tags")
            region = arguments.get("region")

            if category:
                domains = [d for d in domains if d.get("category") == category]
            if tags:
                domains = [d for d in domains if any(t in d.get("tags", []) for t in tags)]
            if region:
                domains = [d for d in domains if region in d.get("region", [])]

            return {
                "domains": [
                    {"id": d["id"], "name": d["name"], "base_url": d["base_url"],
                     "region": d.get("region", []), "category": d.get("category")}
                    for d in domains
                ],
                "count": len(domains),
            }

        elif name == "list_groups":
            return _execute_list_groups(arguments, config)

        elif name == "get_domain_config":
            domain_id = arguments["domain_id"]
            all_domains = {d["id"]: d for d in config.domains_config.get("domains", [])}
            if domain_id not in all_domains:
                return {"error": f"Domain '{domain_id}' not found"}
            return all_domains[domain_id]

        elif name == "start_scan":
            # Check for already-running scans — warn about rate limit pressure
            running_scans = [
                j for j in scan_manager.jobs.values()
                if j.status.value == "running"
            ]

            job = await scan_manager.start_scan(
                domains_group=arguments.get("domains", "quick"),
                max_concurrent=arguments.get("max_concurrent", 5),
                skip_llm=arguments.get("skip_llm", False),
            )

            result = {
                "scan_id": job.scan_id,
                "status": job.status.value,
                "domain_count": job.domain_count,
            }

            if running_scans:
                other_ids = ", ".join(j.scan_id for j in running_scans)
                result["warning"] = (
                    f"Another scan is already running ({other_ids}). "
                    f"Both scans share the same API key, so rate limits "
                    f"may slow things down. Both scans will still complete — "
                    f"they'll just retry automatically if throttled."
                )

            return result

        elif name == "get_scan_status":
            scan_id = arguments["scan_id"]
            job = scan_manager.jobs.get(scan_id)
            if not job:
                return {"error": f"Scan '{scan_id}' not found"}
            policies = scan_manager.get_policies(scan_id)

            completed = job.progress.completed_domains
            total = job.progress.total_domains

            result = {
                "scan_id": job.scan_id,
                "status": job.status.value,
                "domain_count": job.domain_count,
                # policy_count is a RUNNING TOTAL — it increases each time a
                # domain finishes and finds policies, not per-poll.
                "policy_count": job.policy_count,
                "policy_count_note": "running total across all completed domains",
                "progress": {
                    "total": total,
                    "completed": completed,
                    "domains": [dp.model_dump() for dp in job.progress.domains],
                },
                "cost": job.cost.model_dump() if job.cost else None,
                "sheets_export": job.sheets_export.model_dump(),
                "policies": [p.model_dump(mode="json") for p in policies[:20]],
            }

            # Add smart polling guidance so the agent doesn't burn API calls.
            # Scans take minutes per domain — frequent polling wastes tokens
            # and can trigger rate limits.
            if job.status.value == "running":
                progress_pct = (completed / total * 100) if total else 100
                if progress_pct < 25:
                    wait = 30
                elif progress_pct < 75:
                    wait = 45
                else:
                    wait = 20  # Close to done, check sooner
                result["recommended_wait_seconds"] = wait
                result["hint"] = (
                    f"Scan is {progress_pct:.0f}% complete. "
                    f"Wait at least {wait}s before checking again."
                )

            return result

        elif name == "stop_scan":
            success = await scan_manager.stop_scan(arguments["scan_id"])
            return {"cancelled": success}

        elif name == "analyze_url":
            url = arguments["url"]
            settings = config.settings

            crawler = AsyncCrawler(
                max_depth=0, max_pages=1,
                delay_seconds=0.5,
                timeout_seconds=settings.crawl.timeout_seconds,
                user_agent=settings.crawl.user_agent,
            )
            try:
                results = await crawler.crawl_domain(url, [""], "agent_analysis")
            finally:
                await crawler.close()

            if not results or not results[0].is_success:
                return {
                    "url": url,
                    "status": results[0].status.value if results else "failed",
                    "error": results[0].error_message if results else "No response",
                }

            extractor = HtmlExtractor(settings.config_dir)
            extracted = extractor.extract(results[0].content, url)

            kw_matcher = build_keyword_matcher(config, scan_manager.data_dir)
            kw_result = kw_matcher.match(extracted.text)

            response: dict[str, Any] = {
                "url": url,
                "title": extracted.title,
                "language": extracted.language,
                "word_count": extracted.word_count,
                "keyword_score": kw_result.score + kw_result.url_bonus,
                "categories": kw_result.categories_matched,
                "matches": len(kw_result.matches),
            }

            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if api_key and kw_matcher.is_relevant(kw_result, url=url):
                llm = ClaudeClient(api_key=api_key)
                try:
                    screening = await llm.screen_relevance(extracted.text, url)
                    response["screening"] = {"relevant": screening.relevant, "confidence": screening.confidence}

                    if screening.relevant:
                        analysis = await llm.analyze_policy(extracted.text, url, extracted.language)
                        domain_id = generate_domain_id(urlparse(url).hostname or url)
                        found = llm.to_policies(
                            analysis,
                            url,
                            extracted.language or "en",
                            domain_id=domain_id,
                            scan_id="manual-analysis",
                        )
                        if found:
                            verifier = Verifier()
                            for policy in found:
                                policy.verification_flags = verifier.verify(policy)

                            store = PolicyStore(data_dir=scan_manager.data_dir)
                            added = store.add_policies(found)

                            primary = found[0]
                            response["policy"] = primary.model_dump(mode="json")
                            if len(found) > 1:
                                response["additional_policies"] = [
                                    p.model_dump(mode="json") for p in found[1:]
                                ]
                            response["flags"] = [f.value for f in primary.verification_flags]
                            response["saved"] = added > 0
                            response["saved_to"] = str(store.policies_file)

                            if added:
                                for policy in found:
                                    log_audit_event(
                                        data_dir=scan_manager.data_dir,
                                        event="policy_found",
                                        scan_id=policy.scan_id,
                                        domain_id=policy.domain_id,
                                        policy_name=policy.policy_name,
                                        url=policy.url,
                                        relevance=policy.relevance_score,
                                        source="analyze_url",
                                    )
                except LLMError as e:
                    response["llm_error"] = str(e)
                finally:
                    await llm.close()

            return response

        elif name == "match_keywords":
            kw_matcher = build_keyword_matcher(config, scan_manager.data_dir)
            result = kw_matcher.match(arguments["text"])
            return {
                "score": result.score,
                "matches": [m.model_dump() for m in result.matches],
                "categories": result.categories_matched,
                "is_excluded": result.is_excluded,
            }

        elif name == "search_policies":
            store = PolicyStore(data_dir=scan_manager.data_dir)
            jurisdiction = arguments.get("jurisdiction")
            policy_type = arguments.get("policy_type")
            min_score = arguments.get("min_score")
            query = arguments.get("query", "")

            if query:
                # Full-text search (name, summary, key requirements,
                # jurisdiction) over persisted policies only — in-memory
                # scan-in-progress results aren't in the FTS index.
                filtered = store.search_text(
                    query,
                    jurisdiction=jurisdiction,
                    policy_type=policy_type,
                    min_score=min_score,
                )
            else:
                policies = store.get_all()
                seen_urls = {p.get("url") for p in policies}
                for policy in scan_manager.get_all_policies():
                    p_dict = policy.model_dump(mode="json")
                    if p_dict.get("url") not in seen_urls:
                        policies.append(p_dict)
                        seen_urls.add(p_dict.get("url"))

                filtered = []
                for p in policies:
                    if jurisdiction and not jurisdiction_matches(
                        jurisdiction, p.get("jurisdiction", "")
                    ):
                        continue
                    if policy_type and p.get("policy_type") != policy_type:
                        continue
                    if min_score and (p.get("relevance_score", 0) or 0) < min_score:
                        continue
                    filtered.append(p)

            return {
                "policies": filtered,
                "count": len(filtered),
            }

        elif name == "get_policy_stats":
            store = PolicyStore(data_dir=scan_manager.data_dir)
            policies = store.get_all()
            seen_urls = {p.get("url") for p in policies}
            for policy in scan_manager.get_all_policies():
                p_dict = policy.model_dump(mode="json")
                if p_dict.get("url") not in seen_urls:
                    policies.append(p_dict)
                    seen_urls.add(p_dict.get("url"))

            by_jurisdiction: dict[str, int] = {}
            by_type: dict[str, int] = {}
            for p in policies:
                j = p.get("jurisdiction") or "Unknown"
                by_jurisdiction[j] = by_jurisdiction.get(j, 0) + 1
                pt = p.get("policy_type") or "unknown"
                by_type[pt] = by_type.get(pt, 0) + 1

            return {
                "total": len(policies),
                "by_jurisdiction": by_jurisdiction,
                "by_type": by_type,
                "flagged": sum(1 for p in policies if p.get("verification_flags")),
            }

        elif name == "get_audit_advisory":
            scan_id = arguments["scan_id"]
            job = scan_manager.jobs.get(scan_id)
            if not job:
                return {"error": f"Scan '{scan_id}' not found"}
            return {
                "scan_id": scan_id,
                "advisory": job.audit_advisory or "No advisory available (scan may still be running)",
            }

        elif name == "list_scans":
            scans = []
            for job in scan_manager.jobs.values():
                scans.append({
                    "scan_id": job.scan_id,
                    "status": job.status.value,
                    "domain_group": job.domain_group,
                    "domain_count": job.domain_count,
                    "policy_count": job.policy_count,
                    "started_at": str(job.started_at) if job.started_at else None,
                    "completed_at": str(job.completed_at) if job.completed_at else None,
                    "progress": {
                        "completed": job.progress.completed_domains,
                        "total": job.progress.total_domains,
                    },
                })
            return {
                "scans": scans,
                "count": len(scans),
                "note": "Use get_scan_status with a scan_id to see full details.",
            }

        elif name == "estimate_cost":
            return scan_manager.estimate_cost(arguments["domains"])

        elif name == "add_domain":
            return await _execute_add_domain(arguments, config)

        else:
            return {"error": f"Unknown tool: {name}"}

    except ConfigurationError as e:
        return {"error": f"Configuration error: {str(e)}"}
    except Exception as e:
        return {"error": str(e)}


def _execute_list_groups(
    arguments: dict[str, Any],
    config: ConfigLoader,
) -> dict:
    """List all scannable targets: named groups, regions, countries, states."""
    category = arguments.get("category", "all")
    all_domains = [d for d in config.domains_config.get("domains", []) if d.get("enabled", True)]
    groups = config.domains_config.get("groups", {})

    # Named groups from groups.yaml
    named_groups = {}
    for name, gcfg in sorted(groups.items()):
        if name == "all":
            continue
        domain_ids = gcfg.get("domains", [])
        named_groups[name] = {
            "description": gcfg.get("description", ""),
            "domains": len(domain_ids) if domain_ids else 0,
        }

    # Regions and states from domain region fields
    region_counts: dict[str, int] = {}
    for d in all_domains:
        for r in d.get("region", []):
            region_counts[r] = region_counts.get(r, 0) + 1

    # Broad region groups (not scannable as country names)
    broad_regions = {"us", "us_states", "eu", "nordic", "apac", "uk",
                     "eu_central", "eu_west", "eu_south", "eu_east", "europe",
                     "north_america", "south_america", "middle_east", "africa"}

    us_state_keys = _all_us_state_keys()
    de_laender_keys = _all_de_laender_keys()
    uk_nation_keys = _all_uk_nation_keys()
    ca_province_keys = _all_ca_province_keys()
    in_state_keys = _all_in_state_keys()
    au_state_keys = _all_au_state_keys()
    ae_emirate_keys = _all_ae_emirate_keys()
    ch_canton_keys = _all_ch_canton_keys()

    sub_national = (us_state_keys | de_laender_keys | uk_nation_keys
                    | ca_province_keys | in_state_keys | au_state_keys
                    | ae_emirate_keys | ch_canton_keys)

    # Countries: everything in region_counts that's not broad or sub-national
    countries = {
        k: {"domains": v}
        for k, v in sorted(region_counts.items())
        if k not in broad_regions and k not in sub_national
    }

    us_states = {
        k: {"domains": v}
        for k, v in sorted(region_counts.items())
        if k in us_state_keys
    }
    de_laender = {
        k: {"domains": v}
        for k, v in sorted(region_counts.items())
        if k in de_laender_keys
    }
    uk_nations = {
        k: {"domains": v}
        for k, v in sorted(region_counts.items())
        if k in uk_nation_keys
    }
    ca_provinces = {
        k: {"domains": v}
        for k, v in sorted(region_counts.items())
        if k in ca_province_keys
    }

    if category == "groups":
        return {"groups": named_groups, "total_domains": len(all_domains)}
    elif category == "states":
        return {"us_states": us_states, "total_domains": len(all_domains)}
    elif category == "countries":
        return {"countries": countries, "total_domains": len(all_domains)}
    else:
        result = {
            "groups": named_groups,
            "countries": countries,
            "uk_nations": uk_nations,
            "de_laender": de_laender,
            "ca_provinces": ca_provinces,
            "us_states": us_states,
            "total_domains": len(all_domains),
            "tip": (
                "Scan by group ('eu', 'canada'), country ('germany', 'india'), "
                "state ('virginia', 'hessen'), or UK nation ('scotland'). "
                "Not seeing your country? Use 'discover [country]' to search "
                "for and add government websites."
            ),
        }
        return result


def _all_us_state_keys() -> set[str]:
    """Return all US state names as underscore-separated keys."""
    from .domain_generator import US_STATE_ABBREVS
    return {s.replace("-", "_") for s in US_STATE_ABBREVS}


def _all_de_laender_keys() -> set[str]:
    """Return all German Länder names used as region keys."""
    return {
        "hessen", "bayern", "nordrhein_westfalen", "baden_wuerttemberg",
        "berlin", "hamburg", "niedersachsen", "sachsen",
    }


def _all_uk_nation_keys() -> set[str]:
    """Return UK devolved nation names used as region keys."""
    return {"scotland", "wales", "northern_ireland"}


def _all_ca_province_keys() -> set[str]:
    """Return Canadian province names used as region keys."""
    return {"ontario", "british_columbia", "quebec", "alberta"}


def _all_in_state_keys() -> set[str]:
    """Return Indian state names used as region keys."""
    return {"karnataka", "tamil_nadu", "telangana", "maharashtra"}


def _all_au_state_keys() -> set[str]:
    """Return Australian state names used as region keys."""
    return {"new_south_wales", "south_australia"}


def _all_ae_emirate_keys() -> set[str]:
    """Return UAE emirate names used as region keys."""
    return {"abu_dhabi", "dubai"}


def _all_ch_canton_keys() -> set[str]:
    """Return Swiss canton names used as region keys."""
    return {"zurich"}


async def _execute_add_domain(
    arguments: dict[str, Any],
    config: ConfigLoader,
) -> dict[str, Any]:
    """Add a new domain by fetching URL metadata and creating YAML config."""
    url = arguments["url"]
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if not hostname:
        return {"error": f"Invalid URL: {url}"}

    # Check if domain already exists
    domain_id = generate_domain_id(hostname)
    existing = {d["id"] for d in config.domains_config.get("domains", [])}
    if domain_id in existing:
        return {
            "already_exists": True,
            "domain_id": domain_id,
            "message": f"Domain '{domain_id}' already exists in the database.",
        }

    # Fetch URL to get metadata
    settings = config.settings
    crawler = AsyncCrawler(
        max_depth=0, max_pages=1,
        delay_seconds=0.5,
        timeout_seconds=settings.crawl.timeout_seconds,
        user_agent=settings.crawl.user_agent,
    )
    try:
        results = await crawler.crawl_domain(url, [""], "add_domain")
    finally:
        await crawler.close()

    # Extract metadata
    title = hostname
    language = "en"
    requires_playwright = False

    if results and results[0].is_success:
        extractor = HtmlExtractor(settings.config_dir)
        extracted = extractor.extract(results[0].content, url)
        if extracted.title:
            title = extracted.title
        if extracted.language:
            language = extracted.language
    elif results and results[0].status.value == "js_required":
        requires_playwright = True

    # Override name if provided
    name = arguments.get("name", title)

    # Build paths
    path = parsed.path or "/"
    query = parsed.query
    start_path = path
    if query:
        start_path = f"{path}?{query}"
    start_paths = [start_path] if start_path != "/" else ["/"]

    # Build entry
    base_url = f"{parsed.scheme}://{hostname}"
    region = detect_region(hostname)
    entry = build_domain_entry(
        name=name,
        domain_id=domain_id,
        base_url=base_url,
        start_paths=start_paths,
        language=language,
        requires_playwright=requires_playwright,
        region=region,
    )

    # Save to config directory
    output_file = suggest_output_file(hostname)
    config_dir = Path(config.config_dir) / "domains"
    output_path = config_dir / output_file

    # Create parent directories if needed
    output_path.parent.mkdir(parents=True, exist_ok=True)

    yaml_content = format_domain_yaml(entry, standalone=True)

    try:
        if output_path.exists():
            # Append to existing file
            append_content = format_domain_yaml(entry, standalone=False)
            with open(output_path, "a", encoding="utf-8") as f:
                f.write("\n" + append_content)
        else:
            output_path.write_text(yaml_content, encoding="utf-8")
    except OSError as e:
        return {"error": f"Failed to write domain YAML to {output_path}: {e}"}

    # Auto-assign to matching groups in groups.yaml
    try:
        groups_updated = _auto_assign_groups(domain_id, region, config.config_dir)
    except Exception as e:
        logger.error("Failed to update groups.yaml: %s", e, exc_info=True)
        groups_updated = []

    # Reload config to pick up the new domain
    config.load()

    return {
        "success": True,
        "domain_id": domain_id,
        "name": name,
        "base_url": base_url,
        "region": region,
        "language": language,
        "config_file": str(output_path),
        "groups_updated": groups_updated,
        "message": f"Added '{name}' ({domain_id}) to the database. It can now be scanned.",
    }


def _auto_assign_groups(
    domain_id: str,
    regions: list[str],
    config_dir: Path,
) -> list[str]:
    """Add domain_id to matching groups in groups.yaml based on its regions.

    Returns list of group names that were updated.
    """
    groups_file = config_dir / "groups.yaml"
    if not groups_file.exists():
        return []

    with open(groups_file, encoding="utf-8") as f:
        groups_data = yaml.safe_load(f) or {}

    groups = groups_data.get("groups", {})
    if not groups:
        return []

    # Collect all target groups from region mapping
    target_groups: set[str] = set()
    for r in regions:
        target_groups.update(REGION_TO_GROUPS.get(r, []))

    # Add domain_id to matching groups (skip 'all' — it's auto-populated)
    updated: list[str] = []
    for group_name in target_groups:
        if group_name not in groups:
            continue
        if group_name == "all":
            continue
        group = groups[group_name]
        if not isinstance(group, dict):
            continue
        domain_list = group.get("domains", [])
        if domain_list is None:
            domain_list = []
            group["domains"] = domain_list
        if domain_id not in domain_list:
            domain_list.append(domain_id)
            updated.append(group_name)

    if updated:
        try:
            tmp = groups_file.with_suffix(".yaml.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                yaml.dump(groups_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            tmp.replace(groups_file)
        except OSError as e:
            logger.error("Failed to write groups.yaml: %s", e)
            return []

    return updated
