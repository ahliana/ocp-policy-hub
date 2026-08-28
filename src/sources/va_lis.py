"""Virginia LIS structured policy source, read from the session bulk files.

Virginia is the most active US state for data centre legislation and it was
the worst covered. The old approach pointed a headless browser at one bill
page at a time: ``lis.virginia.gov/bill-details/20261/HB323`` is a React
shell that returns 2,465 bytes before JavaScript runs, so every Virginia
bill cost a Chromium render and only the bills someone had already heard
about were ever looked at. HB 323, the first state law on data centre heat
reuse, was configured as its own domain and never reached the database.

The Division of Legislative Automated Systems publishes the whole session
as CSV on Azure blob storage, hourly during session, with no API key, no
registration and no JavaScript. One fetch covers every bill, which is why
this source replaces the per-bill domains rather than supplementing them.

Three things measured against the live files, each of which breaks a
reasonable assumption:

- **File names do not share a convention.** ``BILLS.CSV`` is upper case and
  ``Summaries.csv`` is mixed case, on a store that is case sensitive.
  ``SUMMARIES.CSV`` is a 404. The names are therefore listed literally in
  :data:`LIS_FILES` and never constructed.
- **The two files disagree about bill numbers.** ``BILLS.CSV`` says
  ``HB323`` and ``Summaries.csv`` says ``HB0323`` for the same bill, so a
  naive join between them matches nothing at all rather than matching
  badly. Document ids are space padded too (``"HB323S    "``). Everything
  goes through :func:`normalize_bill_no` before it is used as a key.
- **Summary text is an HTML fragment**, with both quoted and unquoted class
  attributes and named entities. Keyword matching on the raw column would
  match markup, so it is stripped before the text is handed on.

Sessions are ``YYYY`` plus a type digit, ``1`` for regular. The special
session digits follow the same long-standing convention but were not
verified against a live URL, so :func:`session_code` refuses them rather
than fetching an address that may not exist.

Coverage note: the bulk files cover 2025 forward. Earlier years live on the
legacy LIS host and are still served by the existing ``va_legislature``
crawl domain, which is left alone on purpose.

License: public records published by the Commonwealth of Virginia.
"""

import csv
import io
import logging
import re

import httpx

from ..core.models import CrawlResult, PageStatus
from . import register_source
from ._common import build_client
from .base import PolicySource, SourceError

logger = logging.getLogger(__name__)

BLOB_BASE = "https://lis.blob.core.windows.net/lisfiles"

#: Exact file names as published. Casing differs per file and the blob
#: store is case sensitive, so these are literals, never built from a
#: pattern. ``test_every_lis_file_name_resolves`` proves them against the
#: real store.
LIS_FILES = {
    "bills": "BILLS.CSV",
    "summaries": "Summaries.csv",
}

#: Bill-details page for a bill; the citation of record, even though we do
#: not fetch it. The contract says the URL must be the official document.
BILL_URL = "https://lis.virginia.gov/bill-details/{session}/{bill_no}"

DEFAULT_SESSION_YEAR = 2026
DEFAULT_MAX_DOCUMENTS = 200

#: Summary versions, later stages last. When a bill carries several, the
#: latest is the one that describes the bill as it now stands.
_SUMMARY_ORDER = [
    "SUMMARY AS INTRODUCED",
    "SUMMARY AS PASSED HOUSE",
    "SUMMARY AS PASSED SENATE",
    "SUMMARY AS PASSED",
    "SUMMARY AS ENACTED",
    "SUMMARY AS CHAPTERED",
]

#: Bill number: letters then digits, with any zero padding on the digits.
_BILL_NO_RE = re.compile(r"^([A-Za-z]+)0*(\d+)")

_TAG_RE = re.compile(r"<[^>]+>")
_ENTITY_RE = re.compile(r"&(nbsp|amp|lt|gt|quot|#39|apos);")
_ENTITIES = {
    "nbsp": " ", "amp": "&", "lt": "<", "gt": ">",
    "quot": '"', "#39": "'", "apos": "'",
}

#: The bills file records status as Y/N flag columns, measured live on the
#: 2026 session: Passed, Failed, Approved, Vetoed, Carried_over. A chaptered
#: bill also carries a Chapter_id such as "CHAP0591". A source-declared
#: stage overrides whatever the analysis model would infer.
_YES = {"y", "yes", "true", "1"}


def session_code(year: int = DEFAULT_SESSION_YEAR, kind: str = "regular") -> str:
    """LIS session code: the year plus a digit for the session type.

    Confirmed live: ``20261`` is the 2026 regular session and ``20251`` the
    2025 regular session. The special-session digits (2 and 3) follow the
    same convention but were never confirmed against a live URL, so asking
    for one raises instead of fetching a guess. A wrong code returns an
    empty file, not an error, which would read as "Virginia passed nothing"
    rather than as a bug.
    """
    if kind != "regular":
        raise SourceError(
            f"Session type {kind!r} is not verified. Only 'regular' session "
            "codes have been confirmed against live LIS URLs; confirm the "
            "special-session code before relying on it."
        )
    if year < 2025:
        raise SourceError(
            f"Session {year} predates the 2025 LIS rebuild. Bulk files cover "
            "2025 forward; earlier sessions are on legacylis.virginia.gov and "
            "are served by the va_legislature crawl domain."
        )
    return f"{year}1"


def normalize_bill_no(raw: str) -> str:
    """Canonical bill number, so padded and unpadded forms are one key.

    ``HB0323``, ``HB323`` and ``"HB323S    "`` all reduce to ``HB323``. The
    summaries file pads to four digits, the API parameter and our domain
    config do not, and document ids carry trailing spaces. Without this the
    two files silently fail to join and the bill disappears.
    """
    match = _BILL_NO_RE.match((raw or "").strip())
    if not match:
        return ""
    return f"{match.group(1).upper()}{match.group(2)}"


def strip_summary_html(text: str) -> str:
    """Plain readable text from a summary cell.

    The column holds HTML fragments whose attributes are sometimes quoted
    and sometimes not, plus named entities. Left in place, the keyword
    matcher scores the markup and the analysis model reads tags as prose.
    """
    if not text:
        return ""
    plain = _TAG_RE.sub(" ", text)
    plain = _ENTITY_RE.sub(lambda m: _ENTITIES[m.group(1)], plain)
    return " ".join(plain.split())


def _flag(row: dict, name: str) -> bool:
    """One Y/N status column as a boolean."""
    return (row.get(name) or "").strip().lower() in _YES


def _lifecycle_stage(row: dict) -> str:
    """Stage from the bills row's status flags.

    Order is not cosmetic. HB 323 carries Passed=Y and Approved=Y at once,
    and it is enacted, not merely passed. A vetoed or failed bill is
    checked first because those are terminal whatever else is set.
    """
    if _flag(row, "Vetoed") or _flag(row, "Failed"):
        return "failed"
    if _flag(row, "Approved") or (row.get("Chapter_id") or "").strip():
        return "enacted"
    if _flag(row, "Passed"):
        return "passed"
    if _flag(row, "Carried_over"):
        return "in_committee"
    return "proposed"


def _best_summary(summaries: list[dict]) -> str:
    """The latest summary version for a bill.

    A bill picks up a new summary at each stage. The introduced text is
    what the bill asked for; the passed text is what it does. Where both
    exist the later one is the accurate description, so it wins.
    """
    if not summaries:
        return ""

    def rank(row: dict) -> int:
        label = (row.get("SUMMARY_TYPE") or "").strip().upper()
        return _SUMMARY_ORDER.index(label) if label in _SUMMARY_ORDER else -1

    best = max(summaries, key=rank)
    return strip_summary_html(best.get("SUMMARY_TEXT") or "")


def parse_csv(payload: bytes) -> list[dict]:
    """Rows from a LIS CSV, tolerating the byte-order mark on some files."""
    text = payload.decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def _first(row: dict, *names: str) -> str:
    """First non-empty value among candidate column names.

    The bills file's header casing has changed across sessions, so columns
    are looked up by several spellings rather than one.
    """
    for name in names:
        value = row.get(name)
        if value and str(value).strip():
            return str(value).strip()
    return ""


async def fetch_lis_csv(
    client: httpx.AsyncClient, session: str, key: str,
) -> bytes:
    """Fetch one session file by its registry key.

    A wrong name returns the store's not-found XML with a 404, which reads
    like an outage unless the message says which address was tried. It says.
    """
    if key not in LIS_FILES:
        raise SourceError(f"Unknown LIS file {key!r}; known: {sorted(LIS_FILES)}")
    url = f"{BLOB_BASE}/{session}/{LIS_FILES[key]}"
    try:
        resp = await client.get(url)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise SourceError(
            f"Virginia LIS file {key!r} could not be fetched at {url}: {e}. "
            "The blob store is case sensitive; check the exact file name."
        ) from e
    return resp.content


@register_source
class VirginiaLISSource(PolicySource):
    """Every bill in a Virginia session, from the published bulk files."""

    id = "va_lis"
    api_key_env = None

    async def fetch(self, domain: dict) -> list[CrawlResult]:
        params = domain.get("source_params") or {}
        year = int(params.get("session_year", DEFAULT_SESSION_YEAR))
        max_documents = int(
            params.get("max_documents", domain.get("max_pages", DEFAULT_MAX_DOCUMENTS))
        )
        session = session_code(year)

        async with build_client() as client:
            bills_raw = await fetch_lis_csv(client, session, "bills")
            summaries_raw = await fetch_lis_csv(client, session, "summaries")

        bills = parse_csv(bills_raw)
        summaries = parse_csv(summaries_raw)
        logger.info(
            "Virginia LIS session %s: %d bills, %d summary rows",
            session, len(bills), len(summaries),
        )

        by_bill: dict[str, list[dict]] = {}
        for row in summaries:
            key = normalize_bill_no(row.get("SUM_BILNO") or "")
            if key:
                by_bill.setdefault(key, []).append(row)

        results: list[CrawlResult] = []
        for row in bills:
            bill_no = normalize_bill_no(_first(row, "Bill_id", "BILL_ID", "bill_id"))
            if not bill_no:
                continue

            description = _first(row, "Bill_description", "BILL_DESCRIPTION")
            summary = _best_summary(by_bill.get(bill_no, []))
            body = "\n\n".join(part for part in (description, summary) if part)
            if not body:
                continue

            results.append(CrawlResult(
                url=BILL_URL.format(session=session, bill_no=bill_no),
                status=PageStatus.SUCCESS,
                content=f"{bill_no}\n\n{body}",
                content_type="text/plain",
                title=description or bill_no,
                language="en",
                content_length=len(body),
                domain_id=domain.get("id"),
                lifecycle_stage=_lifecycle_stage(row),
            ))
            if len(results) >= max_documents:
                logger.info(
                    "Virginia LIS: stopping at the %d document cap; %d bills "
                    "in the session were not read this run",
                    max_documents, len(bills) - len(results),
                )
                break

        return results
