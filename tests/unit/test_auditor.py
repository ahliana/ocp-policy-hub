"""Tests for src.orchestration.auditor (WP-22 - usage capture).

The auditor's cost was previously invisible in a scan's recorded actuals:
Auditor never read response.usage, so its one Sonnet call per scan was
missing from job.cost entirely. generate_advisory() must capture the
usage so ScanManager can price and add it.
"""

from unittest.mock import AsyncMock, MagicMock

import anthropic
import pytest

from src.core.models import DEFAULT_ANALYSIS_MODEL
from src.orchestration.auditor import Auditor


def _make_advisory_response(text: str = "## Advisory\nAll good.", input_tokens: int = 4500, output_tokens: int = 300):
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    content_block = MagicMock()
    content_block.text = text
    response = MagicMock()
    response.content = [content_block]
    response.usage = usage
    return response


@pytest.mark.medium
class TestAuditorUsageCapture:
    def _auditor(self):
        auditor = Auditor.__new__(Auditor)
        auditor.client = AsyncMock()
        auditor.model = DEFAULT_ANALYSIS_MODEL
        auditor.last_input_tokens = None
        auditor.last_output_tokens = None
        return auditor

    @pytest.mark.asyncio
    async def test_generate_advisory_captures_usage(self):
        auditor = self._auditor()
        auditor.client.messages.create = AsyncMock(
            return_value=_make_advisory_response(input_tokens=4500, output_tokens=300)
        )

        advisory = await auditor.generate_advisory(
            scan_summary={"scan_id": "s1"}, domain_results=[], flagged_issues=[],
        )

        assert advisory == "## Advisory\nAll good."
        assert auditor.last_input_tokens == 4500
        assert auditor.last_output_tokens == 300

    @pytest.mark.asyncio
    async def test_new_auditor_has_no_usage_yet(self):
        """Before generate_advisory ever runs (or when it isn't called at
        all), ScanManager must be able to tell there is nothing to add."""
        auditor = self._auditor()
        assert auditor.last_input_tokens is None
        assert auditor.last_output_tokens is None

    @pytest.mark.asyncio
    async def test_auth_error_leaves_usage_unset(self):
        auditor = self._auditor()
        auth_error = anthropic.AuthenticationError.__new__(anthropic.AuthenticationError)
        auditor.client.messages.create = AsyncMock(side_effect=auth_error)

        advisory = await auditor.generate_advisory(
            scan_summary={}, domain_results=[], flagged_issues=[],
        )

        assert advisory is None
        assert auditor.last_input_tokens is None
        assert auditor.last_output_tokens is None

    @pytest.mark.asyncio
    async def test_generic_error_leaves_usage_unset(self):
        auditor = self._auditor()
        auditor.client.messages.create = AsyncMock(side_effect=RuntimeError("boom"))

        advisory = await auditor.generate_advisory(
            scan_summary={}, domain_results=[], flagged_issues=[],
        )

        assert advisory is None
        assert auditor.last_input_tokens is None
        assert auditor.last_output_tokens is None

    def test_init_sets_usage_fields_to_none(self):
        auditor = Auditor(api_key="sk-ant-test")
        assert auditor.last_input_tokens is None
        assert auditor.last_output_tokens is None
