"""Tests for the Virginia LIS bulk-file source.

The fixtures are real rows, copied from the live 2026 session files on
2026-08-28. They are kept verbatim because every awkward thing in them is
the point: the two files spell the same bill differently, the summary is an
HTML fragment with inconsistent quoting, and the document id is space
padded.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import PageStatus
from src.sources import SOURCE_REGISTRY
from src.sources.base import SourceError
from src.sources.va_lis import (
    LIS_FILES,
    VirginiaLISSource,
    _lifecycle_stage,
    normalize_bill_no,
    session_code,
    strip_summary_html,
)

# --- real rows, 2026 regular session (20261) -------------------------------

# Note the unpadded Bill_id here against the padded SUM_BILNO below. Same
# bill, two files, two spellings.
BILLS_CSV = (
    '"Bill_id","Bill_description","Passed","Failed","Approved","Vetoed",'
    '"Carried_over","Chapter_id"\r\n'
    '"HB323","Data centers; Department of Energy shall lead efforts to '
    'accelerate use of waste heat, report.","Y","N","Y","N","N","CHAP0591"\r\n'
    '"HB906","Data centers; load flexibility.","N","Y","N","N","N",""\r\n'
)

SUMMARIES_CSV = (
    '"SUM_BILNO","SUMMARY_DOCID","SUMMARY_TYPE","SUMMARY_TEXT"\r\n'
    '"HB0323","HB323S    ","SUMMARY AS INTRODUCED",'
    '"<p class=sumtext><b>Department of Energy; use of waste heat from data '
    'centers.</b> Directs the Department of  Energy to identify opportunities '
    'for using waste heat from data centers in the Commonwealth.</p>"\r\n'
    '"HB0323","HB323SE   ","SUMMARY AS PASSED HOUSE",'
    '"<p class=\'sumtext\'><b>Department of Energy; use of waste heat from '
    'data centers.</b> Requires the Department to submit a report&nbsp;no '
    'later than September 1, 2026. </p>"\r\n'
    '"HB0906","HB906S    ","SUMMARY AS INTRODUCED",'
    '"<p class=sumtext>Data center load flexibility.</p>"\r\n'
)


def _mock_response(payload: bytes):
    resp = MagicMock()
    resp.status_code = 200
    resp.content = payload
    resp.headers = {"content-type": "text/csv"}
    resp.raise_for_status = MagicMock()
    return resp


def _mock_client():
    """A client returning the bills file then the summaries file."""
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    client.get = AsyncMock(side_effect=[
        _mock_response(BILLS_CSV.encode("utf-8")),
        _mock_response(SUMMARIES_CSV.encode("utf-8")),
    ])
    return client


class TestBillNumberNormalisation:
    @pytest.mark.small
    def test_padded_and_unpadded_numbers_are_one_key(self):
        """BILLS.CSV says HB323 and Summaries.csv says HB0323 for the same
        bill. Without this the join matches nothing at all."""
        assert normalize_bill_no("HB0323") == normalize_bill_no("HB323") == "HB323"

    @pytest.mark.small
    def test_space_padded_document_id_normalises(self):
        """Document ids carry trailing spaces in the real file."""
        assert normalize_bill_no("HB323S    ") == "HB323"

    @pytest.mark.small
    def test_junk_returns_empty_rather_than_a_wrong_key(self):
        assert normalize_bill_no("") == ""
        assert normalize_bill_no("   ") == ""
        assert normalize_bill_no("not a bill") == ""


class TestSummaryText:
    @pytest.mark.small
    def test_markup_and_entities_are_removed(self):
        """Left in place, the keyword matcher scores the markup and the
        analysis model reads tags as prose. Both quoting styles appear in
        the real file, and so does a non-breaking space."""
        raw = ("<p class=sumtext><b>Heat.</b> Directs the Department"
               "&nbsp;to report. </p>")
        assert strip_summary_html(raw) == "Heat. Directs the Department to report."

    @pytest.mark.small
    def test_single_quoted_attributes_are_removed_too(self):
        raw = "<p class='sumtext'>Requires a report&nbsp;by September.</p>"
        assert "<" not in strip_summary_html(raw)
        assert "sumtext" not in strip_summary_html(raw)

    @pytest.mark.small
    def test_empty_summary_is_empty_not_none(self):
        assert strip_summary_html("") == ""


class TestSessionCode:
    @pytest.mark.small
    def test_regular_session_code(self):
        assert session_code(2026) == "20261"
        assert session_code(2025) == "20251"

    @pytest.mark.small
    def test_special_session_is_refused_rather_than_guessed(self):
        """The special-session digits follow convention but were never
        confirmed against a live URL, and a wrong code returns an empty
        file rather than an error: it would read as Virginia passing
        nothing at all."""
        with pytest.raises(SourceError, match="not verified"):
            session_code(2026, kind="special1")

    @pytest.mark.small
    def test_pre_rebuild_year_points_at_the_legacy_host(self):
        with pytest.raises(SourceError, match="legacylis"):
            session_code(2024)


class TestLifecycleStage:
    @pytest.mark.small
    def test_enacted_beats_passed_on_the_real_hb323_flags(self):
        """HB 323 carries Passed=Y and Approved=Y at once. It is enacted,
        not merely passed, and the order of the checks is what decides."""
        row = {"Passed": "Y", "Failed": "N", "Approved": "Y",
               "Vetoed": "N", "Chapter_id": "CHAP0591"}
        assert _lifecycle_stage(row) == "enacted"

    @pytest.mark.small
    def test_failed_is_terminal(self):
        row = {"Passed": "N", "Failed": "Y", "Approved": "N", "Vetoed": "N"}
        assert _lifecycle_stage(row) == "failed"

    @pytest.mark.small
    def test_no_flags_at_all_is_proposed(self):
        assert _lifecycle_stage({}) == "proposed"


class TestFetch:
    @pytest.mark.medium
    @pytest.mark.asyncio
    async def test_hb323_arrives_with_its_waste_heat_text(self):
        """FAILS ON OLD BEHAVIOR. Before this source existed, HB 323 was
        reachable only by rendering a React page with a headless browser,
        and it was absent from the production database. Nothing in the
        codebase could produce this document."""
        with patch("src.sources.va_lis.build_client", return_value=_mock_client()):
            results = await VirginiaLISSource().fetch(
                {"id": "va_lis", "source_params": {"session_year": 2026}}
            )

        hb323 = [r for r in results if "HB323" in r.url]
        assert len(hb323) == 1
        doc = hb323[0]
        assert "waste heat from data centers" in doc.content
        assert doc.status == PageStatus.SUCCESS
        assert doc.lifecycle_stage == "enacted"
        assert doc.url == "https://lis.virginia.gov/bill-details/20261/HB323"

    @pytest.mark.medium
    @pytest.mark.asyncio
    async def test_a_bill_with_two_summaries_yields_one_document(self):
        """A bill picks up a new summary at each stage. The introduced text
        is what it asked for; the passed text is what it does."""
        with patch("src.sources.va_lis.build_client", return_value=_mock_client()):
            results = await VirginiaLISSource().fetch({"id": "va_lis"})

        hb323 = [r for r in results if "HB323" in r.url]
        assert len(hb323) == 1
        assert "September 1, 2026" in hb323[0].content, "the later summary should win"

    @pytest.mark.medium
    @pytest.mark.asyncio
    async def test_the_document_cap_is_honoured(self):
        with patch("src.sources.va_lis.build_client", return_value=_mock_client()):
            results = await VirginiaLISSource().fetch(
                {"id": "va_lis", "source_params": {"max_documents": 1}}
            )
        assert len(results) == 1

    @pytest.mark.medium
    @pytest.mark.asyncio
    async def test_a_missing_file_names_the_address_it_tried(self):
        """The blob store is case sensitive and a wrong name returns a 404
        that reads like an outage unless the message says what was tried."""
        import httpx

        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.get = AsyncMock(side_effect=httpx.HTTPError("404 Not Found"))

        with patch("src.sources.va_lis.build_client", return_value=client):
            with pytest.raises(SourceError) as excinfo:
                await VirginiaLISSource().fetch({"id": "va_lis"})

        message = str(excinfo.value)
        assert "lisfiles/20261/BILLS.CSV" in message
        assert "case sensitive" in message


class TestRegistration:
    @pytest.mark.small
    def test_source_is_registered_under_va_lis(self):
        assert SOURCE_REGISTRY["va_lis"] is VirginiaLISSource

    @pytest.mark.small
    def test_source_needs_no_api_key(self):
        """The whole argument for the bulk files is that they need nothing:
        no key, no registration, no JavaScript."""
        assert VirginiaLISSource.api_key_env is None


class TestAgainstTheLiveStore:
    @pytest.mark.large
    @pytest.mark.asyncio
    async def test_every_lis_file_name_resolves(self):
        """The one test that touches the network. It exists because the
        file names do not share a convention: BILLS.CSV is upper case and
        Summaries.csv is mixed case, on a case-sensitive store. If the
        Commonwealth renames or re-cases a file, this is what says so."""
        import httpx

        from src.sources.va_lis import BLOB_BASE

        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            for key, name in LIS_FILES.items():
                url = f"{BLOB_BASE}/20261/{name}"
                resp = await client.head(url)
                assert resp.status_code == 200, (
                    f"LIS file {key!r} did not resolve at {url}. "
                    "Check the exact casing against the blob store."
                )
