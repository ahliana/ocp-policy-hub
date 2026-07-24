"""Google Sheets integration — exports discovered policies to a Staging sheet."""

import base64
import json
import logging
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials
from tenacity import retry, stop_after_attempt, wait_exponential

from ..core.models import Policy
from ..core.policy_schema import LINK_HEADER, REVIEW_STATUS_HEADER, STAGING_HEADERS
from ..storage.leads import Lead

logger = logging.getLogger(__name__)

# Tips worksheet columns (see SheetsClient.export_tips). Public vocabulary
# ("tip") over the internal Lead/LeadStore field names — mirrors the
# API-level rename in src/api/routes/leads.py.
TIP_HEADERS = ["Submitted At", "Origin", "Title/Note", "URL", "Status", "Chase Outcome"]


def _col_letter(n: int) -> str:
    """1-based column index -> spreadsheet letter (1 -> A, 27 -> AA)."""
    letters = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


class SheetsClient:
    """Write policies to a Google Spreadsheet."""

    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

    def __init__(self, credentials_b64: str, spreadsheet_id: str):
        self.spreadsheet_id = spreadsheet_id
        self._credentials_b64 = credentials_b64
        self._client: Optional[gspread.Client] = None
        self._spreadsheet = None

    def connect(self) -> None:
        if not self._credentials_b64 or len(self._credentials_b64) < 50:
            raise ValueError(
                f"GOOGLE_CREDENTIALS looks invalid (length={len(self._credentials_b64) if self._credentials_b64 else 0}). "
                "Check your .env file — the value should be base64-encoded service account JSON."
            )
        creds_json = base64.b64decode(self._credentials_b64).decode("utf-8")
        creds_dict = json.loads(creds_json)
        credentials = Credentials.from_service_account_info(creds_dict, scopes=self.SCOPES)
        self._client = gspread.authorize(credentials)
        self._spreadsheet = self._client.open_by_key(self.spreadsheet_id)

    def get_staging_sheet(self, name: str = "Staging") -> gspread.Worksheet:
        try:
            return self._spreadsheet.worksheet(name)
        except gspread.WorksheetNotFound:
            headers = Policy.sheet_headers()
            sheet = self._spreadsheet.add_worksheet(
                name, rows=1000, cols=len(headers),
            )
            end_col = _col_letter(len(headers))
            sheet.update([headers], f"A1:{end_col}1")
            return sheet

    def _link_column_index(self, sheet: gspread.Worksheet) -> int:
        """1-based index of the URL column, found by header name.

        The Staging layout mirrors the master database where the URL lives in
        the "Link" column, not column A, so dedupe must locate it by header.
        """
        header_row = sheet.row_values(1)
        for idx, header in enumerate(header_row, start=1):
            if header.strip().lower() == LINK_HEADER.lower():
                return idx
        # Empty/absent header row: fall back to the canonical layout position.
        return STAGING_HEADERS.index(LINK_HEADER) + 1

    def get_existing_urls(self, sheet_name: str = "Staging") -> set[str]:
        try:
            sheet = self._spreadsheet.worksheet(sheet_name)
            link_col = self._link_column_index(sheet)
            urls = sheet.col_values(link_col)
            return set(u for u in urls[1:] if u)
        except gspread.WorksheetNotFound:
            return set()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
    def read_staging_rows(self, sheet_name: str = "Staging") -> list[dict]:
        """Read every data row of the Staging sheet as header-keyed dicts.

        Returns [] if the sheet doesn't exist yet — nothing to seed a fresh
        deployment from until the sheet has been written to at least once.
        """
        try:
            sheet = self._spreadsheet.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            return []
        return sheet.get_all_records()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
    def append_policies(self, policies: list[Policy], sheet_name: str = "Staging") -> int:
        if not policies:
            return 0

        # Dedupe within the batch by URL (keep first occurrence) — multi-policy
        # extraction can pass several policies sharing one source URL.
        seen_urls: set[str] = set()
        deduped = []
        for p in policies:
            if p.url in seen_urls:
                continue
            seen_urls.add(p.url)
            deduped.append(p)

        # Skip policies already on the sheet — re-exports can pass URLs that
        # were already appended in a prior run.
        existing_urls = self.get_existing_urls(sheet_name)
        new_policies = [p for p in deduped if p.url not in existing_urls]
        if not new_policies:
            return 0

        sheet = self.get_staging_sheet(sheet_name)
        rows = [p.to_sheet_row() for p in new_policies]
        sheet.append_rows(rows, value_input_option="USER_ENTERED")
        return len(rows)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
    def update_review_statuses(
        self, url_statuses: dict[str, str], sheet_name: str = "Staging",
    ) -> int:
        """Batch-write the Review Status column for matching Staging rows.

        One-way (app -> sheet), URL-matched. Tolerant of missing rows: a URL
        not on the sheet yet (or a sheet that doesn't exist at all) is
        silently skipped rather than an error — the review workflow can
        outrun a not-yet-exported row. Returns the count of rows updated.
        """
        if not url_statuses:
            return 0
        try:
            sheet = self._spreadsheet.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            return 0

        header_row = sheet.row_values(1)
        if REVIEW_STATUS_HEADER not in header_row:
            return 0
        review_col = header_row.index(REVIEW_STATUS_HEADER) + 1
        link_col = self._link_column_index(sheet)

        urls = sheet.col_values(link_col)
        updates = [
            {
                "range": gspread.utils.rowcol_to_a1(row_idx, review_col),
                "values": [[url_statuses[url]]],
            }
            for row_idx, url in enumerate(urls[1:], start=2)
            if url in url_statuses
        ]
        if not updates:
            return 0
        sheet.batch_update(updates)
        return len(updates)

    def get_tips_sheet(self, name: str = "Tips") -> gspread.Worksheet:
        try:
            return self._spreadsheet.worksheet(name)
        except gspread.WorksheetNotFound:
            sheet = self._spreadsheet.add_worksheet(
                name, rows=1000, cols=len(TIP_HEADERS),
            )
            end_col = _col_letter(len(TIP_HEADERS))
            sheet.update([TIP_HEADERS], f"A1:{end_col}1")
            return sheet

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
    def export_tips(self, leads: list[Lead], sheet_name: str = "Tips") -> int:
        """One-way, full-snapshot export of the tip queue to the Tips worksheet.

        Rewrites the sheet on every call rather than appending: a tip's
        status and chase outcome change over its lifecycle (new -> chased,
        outcome recorded), so appending would accumulate stale duplicate
        rows instead of reflecting current state. Never called
        automatically on submission — batch/ops-triggered only (see
        src/output/export_tips.py).
        """
        sheet = self.get_tips_sheet(sheet_name)
        rows = [_tip_to_row(lead) for lead in leads]
        end_col = _col_letter(len(TIP_HEADERS))
        sheet.clear()
        sheet.update([TIP_HEADERS] + rows, f"A1:{end_col}{len(rows) + 1}")
        return len(rows)


def _tip_chase_outcome(lead: Lead) -> str:
    if lead.status == "chased":
        return f"Found: {lead.policy_url}" if lead.policy_url else "Checked - nothing found"
    if lead.chase_outcome == "fetch_failed":
        return f"Fetch failed: {lead.chase_error or 'unknown error'}"
    return ""


def _tip_to_row(lead: Lead) -> list:
    return [
        lead.found_at.isoformat(),
        lead.origin,
        lead.title or lead.snippet,
        lead.source_url,
        lead.status,
        _tip_chase_outcome(lead),
    ]
