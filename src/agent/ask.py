"""Reader Q&A: answer natural language questions about stored policies.

This is the public-facing counterpart to the admin PolicyAgent. It runs
a deliberately tiny agent loop with exactly two read-only tools, so a
reader question can search the policy library but can never start a
scan, hit the web, or spend more than a few LLM calls. Cost per
question on Haiku is a fraction of a cent; the /api/ask route adds
rate and daily limits on top.
"""

import json
import logging
from typing import Any

from .. import aispend
from .tools import POLICY_TOOLS, execute_tool

logger = logging.getLogger(__name__)

READER_TOOL_NAMES = ("search_policies", "get_policy_stats")
ASK_MAX_ITERATIONS = 5
ASK_MAX_TOKENS = 1024

READER_SYSTEM_PROMPT = """You answer questions about government data center \
heat-reuse policies that this tool has already discovered and stored.

Rules:
- Use search_policies and get_policy_stats to look up stored policies. If a \
search returns nothing, retry with synonyms, broader terms, or the country's \
native-language keywords (e.g. "Abwärme" for Germany) before giving up.
- For a region group (Nordics, EU, DACH, Benelux...), search each member \
country separately with the jurisdiction filter and NO query keywords first, \
so you see everything stored for that country. search_policies does a \
full-text search over each policy's name, summary, key requirements, and \
jurisdiction — all words must match, so keep queries short and specific.
- Before concluding a country or region has NO policies, call \
get_policy_stats to see which jurisdictions actually have stored policies, \
then search those. Do not claim emptiness without checking stats first.
- Answer ONLY from stored data. Never invent policies. If nothing matches, \
say so plainly and mention that an administrator can scan that region.
- Reply in the same language the question was asked in.
- Use plain language for non-technical readers; explain what each policy \
means in practical terms.
- Cite each policy's official URL.
- Be concise: the most relevant policies first, no filler."""


def reader_tools() -> list[dict[str, Any]]:
    """The read-only tool subset exposed to reader questions."""
    return [t for t in POLICY_TOOLS if t["name"] in READER_TOOL_NAMES]


async def answer_question(
    question: str,
    *,
    client,
    model: str,
    config,
    scan_manager,
) -> dict[str, Any]:
    """Answer a reader question using only stored policy data.

    Args:
        question: The reader's natural language question.
        client: An anthropic.AsyncAnthropic (or compatible) client.
        model: Model ID to use (admin cost level decides this).
        config: ConfigLoader for tool execution.
        scan_manager: ScanManager for tool execution (read paths only).

    Returns:
        {"answer": str, "tool_calls": int}
    """
    tools = reader_tools()
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    answer_parts: list[str] = []
    tool_calls = 0

    for _ in range(ASK_MAX_ITERATIONS):
        response = await aispend.acreate(
            client, label="agent:ask",
            model=model,
            max_tokens=ASK_MAX_TOKENS,
            system=READER_SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )

        tool_uses = []
        # Keep only this turn's text: readers should see the final answer,
        # not the "let me search..." narration from intermediate turns.
        answer_parts = []
        for block in response.content:
            if block.type == "text":
                answer_parts.append(block.text)
            elif block.type == "tool_use":
                tool_uses.append(block)

        if response.stop_reason == "end_turn" or not tool_uses:
            break

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for tool_use in tool_uses:
            tool_calls += 1
            if tool_use.name not in READER_TOOL_NAMES:
                # Defense in depth: the model only sees reader tools, but
                # never execute anything else even if one slips through.
                logger.warning("Reader agent requested forbidden tool: %s", tool_use.name)
                result: dict[str, Any] = {
                    "error": "This tool is not available for reader questions."
                }
            else:
                try:
                    result = await execute_tool(
                        name=tool_use.name,
                        arguments=tool_use.input,
                        config=config,
                        scan_manager=scan_manager,
                    )
                except Exception as e:
                    logger.error("Reader tool %s failed: %s", tool_use.name, e)
                    result = {"error": str(e)}
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": json.dumps(result, default=str),
            })

        messages.append({"role": "user", "content": tool_results})

    return {"answer": "\n".join(answer_parts).strip(), "tool_calls": tool_calls}
