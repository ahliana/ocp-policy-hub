"""Post-scan auditor — 1 LLM call per completed scan for strategic insights."""

import json
import logging
from typing import Optional

import anthropic

from ..core.models import DEFAULT_ANALYSIS_MODEL

logger = logging.getLogger(__name__)

AUDIT_PROMPT = """You are a policy research analyst reviewing scan results from an automated system that crawls government websites to find data center heat reuse policies.

SCAN SUMMARY:
{scan_summary}

DOMAIN RESULTS:
{domain_results}

FLAGGED ISSUES:
{flagged_issues}

TASK:
Analyze these results and provide a brief advisory (max 500 words) covering:
1. Key findings — notable policies discovered, jurisdiction patterns
2. Coverage gaps — regions or domain types that yielded no results but should have
3. Quality concerns — domains with many keyword matches but no policies (possible start_paths issue)
4. Recommendations — specific actions to improve next scan (e.g., "add start_path /energy/ to domain X")

Be specific and actionable. Reference domain IDs and URLs where relevant.
Format as markdown with headers.
"""


class Auditor:
    """Post-scan LLM advisory using a single bounded Sonnet call."""

    MAX_OUTPUT_TOKENS = 2000

    def __init__(self, api_key: str, model: str = DEFAULT_ANALYSIS_MODEL):
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model
        # Set by generate_advisory() on a successful call (WP-22) so
        # ScanManager can price and add this call's tokens into the scan's
        # recorded actuals. Stay None if the auditor never runs or its call
        # fails - callers must treat None as "nothing to add".
        self.last_input_tokens: Optional[int] = None
        self.last_output_tokens: Optional[int] = None

    async def generate_advisory(
        self,
        scan_summary: dict,
        domain_results: list[dict],
        flagged_issues: list[dict],
    ) -> Optional[str]:
        """Generate post-scan advisory. Returns markdown string or None on error."""
        try:
            prompt = AUDIT_PROMPT.format(
                scan_summary=json.dumps(scan_summary, indent=2, default=str),
                domain_results=json.dumps(domain_results[:50], indent=2, default=str),  # Cap size
                flagged_issues=json.dumps(flagged_issues[:30], indent=2, default=str),
            )

            response = await self.client.messages.create(
                model=self.model,
                max_tokens=self.MAX_OUTPUT_TOKENS,
                temperature=0.3,
                messages=[{"role": "user", "content": prompt}],
            )

            if hasattr(response, "usage"):
                self.last_input_tokens = response.usage.input_tokens
                self.last_output_tokens = response.usage.output_tokens

            return response.content[0].text

        except anthropic.AuthenticationError:
            logger.error("Auditor: authentication failed")
            return None
        except Exception as e:
            logger.warning(f"Auditor failed: {e}")
            return None

    async def close(self):
        await self.client.close()
