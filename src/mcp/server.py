"""MCP server — 11 tools for Claude to orchestrate policy scanning."""

import asyncio
import json
import os
from typing import Any

from pathlib import Path
from dotenv import load_dotenv

# Resolve .env from project root (2 levels up from src/mcp/server.py)
# so credentials load regardless of the process working directory.
_project_root = Path(__file__).resolve().parents[2]
load_dotenv(_project_root / ".env", override=True)

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from ..core.config import ConfigLoader, ConfigurationError
from ..core.crawler import AsyncCrawler
from ..core.extractor import HtmlExtractor
from ..core.keywords import build_keyword_matcher
from ..core.llm import ClaudeClient, LLMError
from ..core.verifier import Verifier
from ..orchestration.events import EventBroadcaster
from ..orchestration.scan_manager import ScanManager

server = Server("OCP-CE-HR-Policy-Searcher")

# Singletons
_config: ConfigLoader | None = None
_scan_manager: ScanManager | None = None
_broadcaster: EventBroadcaster | None = None


def _get_config() -> ConfigLoader:
    global _config
    if _config is None:
        config_dir = os.environ.get("OCP_CONFIG_DIR", "config")
        _config = ConfigLoader(config_dir=config_dir)
        _config.load()
    return _config


def _get_scan_manager() -> ScanManager:
    global _scan_manager, _broadcaster
    if _scan_manager is None:
        _broadcaster = EventBroadcaster()
        _scan_manager = ScanManager(
            config=_get_config(),
            broadcaster=_broadcaster,
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
            data_dir=os.environ.get("OCP_DATA_DIR", "data"),
        )
    return _scan_manager


def _json_result(data: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(data, indent=2, default=str))]


# === TOOL DEFINITIONS ===

TOOLS = [
    Tool(
        name="list_domains",
        description="List and filter available domains. Can filter by group, category, tags, or region.",
        inputSchema={
            "type": "object",
            "properties": {
                "group": {"type": "string", "description": "Group name, region, or 'all'"},
                "category": {"type": "string", "description": "Filter by category"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Filter by tags"},
                "region": {"type": "string", "description": "Filter by region"},
            },
        },
    ),
    Tool(
        name="list_groups",
        description="List all scannable targets: named groups, regions, and US states with domain counts.",
        inputSchema={
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Filter: 'groups', 'states', or 'all'", "default": "all"},
            },
        },
    ),
    Tool(
        name="get_domain_config",
        description="Get full configuration for a specific domain by ID.",
        inputSchema={
            "type": "object",
            "properties": {
                "domain_id": {"type": "string", "description": "Domain ID"},
            },
            "required": ["domain_id"],
        },
    ),
    Tool(
        name="start_scan",
        description="Start a parallel scan of domains. Returns scan_id immediately.",
        inputSchema={
            "type": "object",
            "properties": {
                "domains": {"type": "string", "default": "quick", "description": "Domain group to scan"},
                "max_concurrent": {"type": "integer", "default": 5, "description": "Max parallel workers"},
                "skip_llm": {"type": "boolean", "default": False, "description": "Skip LLM analysis"},
            },
        },
    ),
    Tool(
        name="get_scan_status",
        description="Get detailed status of a scan including per-domain progress and costs.",
        inputSchema={
            "type": "object",
            "properties": {
                "scan_id": {"type": "string", "description": "Scan ID"},
            },
            "required": ["scan_id"],
        },
    ),
    Tool(
        name="stop_scan",
        description="Cancel a running scan.",
        inputSchema={
            "type": "object",
            "properties": {
                "scan_id": {"type": "string", "description": "Scan ID to cancel"},
            },
            "required": ["scan_id"],
        },
    ),
    Tool(
        name="analyze_url",
        description="Run the full analysis pipeline on a single URL (fetch → extract → keywords → LLM → verify).",
        inputSchema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to analyze"},
            },
            "required": ["url"],
        },
    ),
    Tool(
        name="match_keywords",
        description="Test keyword scoring on arbitrary text.",
        inputSchema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to match keywords against"},
                "language": {"type": "string", "default": "en", "description": "Language code"},
            },
            "required": ["text"],
        },
    ),
    Tool(
        name="search_policies",
        description="Search discovered policies with optional filters.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "jurisdiction": {"type": "string", "description": "Filter by jurisdiction"},
                "policy_type": {"type": "string", "description": "Filter by policy type"},
                "min_score": {"type": "integer", "description": "Minimum relevance score"},
            },
        },
    ),
    Tool(
        name="get_policy_stats",
        description="Get aggregate statistics about discovered policies.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="get_audit_advisory",
        description="Get the post-scan auditor's recommendations for a scan.",
        inputSchema={
            "type": "object",
            "properties": {
                "scan_id": {"type": "string", "description": "Scan ID"},
            },
            "required": ["scan_id"],
        },
    ),
    Tool(
        name="estimate_cost",
        description="Estimate API costs before running a scan.",
        inputSchema={
            "type": "object",
            "properties": {
                "domains": {"type": "string", "description": "Domain group to estimate"},
            },
            "required": ["domains"],
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        config = _get_config()
        manager = _get_scan_manager()

        if name == "list_domains":
            group = arguments.get("group", "all")
            domains = config.get_enabled_domains(group)
            # Same overlay the API routes and ScanManager apply - without it,
            # a domain disabled in the admin Sources panel still shows as
            # available to MCP clients.
            from ..core.overrides import apply_domain_overrides
            from ..storage.domain_overrides import DomainOverridesStore
            domains = apply_domain_overrides(
                domains,
                DomainOverridesStore(data_dir=manager.data_dir).get_all(),
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

            return _json_result({
                "domains": [
                    {"id": d["id"], "name": d["name"], "base_url": d["base_url"],
                     "region": d.get("region", []), "category": d.get("category")}
                    for d in domains
                ],
                "count": len(domains),
            })

        elif name == "list_groups":
            from ..agent.tools import _execute_list_groups
            return _json_result(_execute_list_groups(arguments, config))

        elif name == "get_domain_config":
            domain_id = arguments["domain_id"]
            all_domains = {d["id"]: d for d in config.domains_config.get("domains", [])}
            if domain_id not in all_domains:
                return _json_result({"error": f"Domain '{domain_id}' not found"})
            return _json_result(all_domains[domain_id])

        elif name == "start_scan":
            job = await manager.start_scan(
                domains_group=arguments.get("domains", "quick"),
                max_concurrent=arguments.get("max_concurrent", 5),
                skip_llm=arguments.get("skip_llm", False),
            )
            return _json_result({
                "scan_id": job.scan_id,
                "status": job.status.value,
                "domain_count": job.domain_count,
            })

        elif name == "get_scan_status":
            scan_id = arguments["scan_id"]
            job = manager.jobs.get(scan_id)
            if not job:
                return _json_result({"error": f"Scan '{scan_id}' not found"})
            policies = manager.get_policies(scan_id)
            return _json_result({
                "scan_id": job.scan_id,
                "status": job.status.value,
                "domain_count": job.domain_count,
                "policy_count": job.policy_count,
                "progress": {
                    "total": job.progress.total_domains,
                    "completed": job.progress.completed_domains,
                    "domains": [dp.model_dump() for dp in job.progress.domains],
                },
                "cost": job.cost.model_dump() if job.cost else None,
                "policies": [p.model_dump(mode="json") for p in policies[:20]],
            })

        elif name == "stop_scan":
            success = await manager.stop_scan(arguments["scan_id"])
            return _json_result({"cancelled": success})

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
                results = await crawler.crawl_domain(url, [""], "mcp_analysis")
            finally:
                await crawler.close()

            if not results or not results[0].is_success:
                return _json_result({
                    "url": url,
                    "status": results[0].status.value if results else "failed",
                    "error": results[0].error_message if results else "No response",
                })

            extractor = HtmlExtractor(settings.config_dir)
            extracted = extractor.extract(results[0].content, url)

            kw_matcher = build_keyword_matcher(config, manager.data_dir)
            kw_result = kw_matcher.match(extracted.text)

            response = {
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
                        found = llm.to_policies(analysis, url, extracted.language or "en")
                        if found:
                            verifier = Verifier()
                            primary = found[0]
                            flags = verifier.verify(primary)
                            response["policy"] = primary.model_dump(mode="json")
                            if len(found) > 1:
                                response["additional_policies"] = [
                                    p.model_dump(mode="json") for p in found[1:]
                                ]
                            response["flags"] = [f.value for f in flags]
                except LLMError as e:
                    response["llm_error"] = str(e)
                finally:
                    await llm.close()

            return _json_result(response)

        elif name == "match_keywords":
            kw_matcher = build_keyword_matcher(config, manager.data_dir)
            result = kw_matcher.match(arguments["text"])
            return _json_result({
                "score": result.score,
                "matches": [m.model_dump() for m in result.matches],
                "categories": result.categories_matched,
                "is_excluded": result.is_excluded,
            })

        elif name == "search_policies":
            policies = manager.get_all_policies()
            jurisdiction = arguments.get("jurisdiction")
            policy_type = arguments.get("policy_type")
            min_score = arguments.get("min_score")
            query = arguments.get("query", "").lower()

            filtered = []
            for p in policies:
                if jurisdiction and jurisdiction.lower() not in (p.jurisdiction or "").lower():
                    continue
                if policy_type and p.policy_type.value != policy_type:
                    continue
                if min_score and p.relevance_score < min_score:
                    continue
                if query and query not in (p.policy_name + " " + p.summary).lower():
                    continue
                filtered.append(p)

            return _json_result({
                "policies": [p.model_dump(mode="json") for p in filtered],
                "count": len(filtered),
            })

        elif name == "get_policy_stats":
            policies = manager.get_all_policies()
            by_jurisdiction: dict[str, int] = {}
            by_type: dict[str, int] = {}
            for p in policies:
                j = p.jurisdiction or "Unknown"
                by_jurisdiction[j] = by_jurisdiction.get(j, 0) + 1
                by_type[p.policy_type.value] = by_type.get(p.policy_type.value, 0) + 1

            return _json_result({
                "total": len(policies),
                "by_jurisdiction": by_jurisdiction,
                "by_type": by_type,
                "flagged": sum(1 for p in policies if p.verification_flags),
            })

        elif name == "get_audit_advisory":
            scan_id = arguments["scan_id"]
            job = manager.jobs.get(scan_id)
            if not job:
                return _json_result({"error": f"Scan '{scan_id}' not found"})
            return _json_result({
                "scan_id": scan_id,
                "advisory": job.audit_advisory or "No advisory available (scan may still be running)",
            })

        elif name == "estimate_cost":
            return _json_result(manager.estimate_cost(arguments["domains"]))

        else:
            return _json_result({"error": f"Unknown tool: {name}"})

    except ConfigurationError as e:
        return _json_result({"error": f"Configuration error: {str(e)}"})
    except Exception as e:
        return _json_result({"error": str(e)})


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
