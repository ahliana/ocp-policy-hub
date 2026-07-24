"""Staging-sheet schema: maps a Policy onto the OCP Heat Reuse Policies
Database column layout, plus PolicyPulse-specific columns.

The first 13 columns mirror the "Heat Reuse Policies Database" tab exactly
(same header text, same order) so a curator can copy a reviewed Staging row
straight into the master database. The remaining columns carry the extra
metadata our pipeline produces (relevance, provenance, verification, etc.).

This module is pure data mapping with no imports from the output layer, so
core.models can delegate to it without a layering inversion.
"""

import re
from datetime import datetime

from src.agent.domain_generator import US_STATE_ABBREVS

# --- Master "Heat Reuse Policies Database" columns (exact header text) ---
MASTER_HEADERS = [
    "Geographical Area",
    "Country",
    "Region",
    "Name",
    "Incentive, Standard, or Enabler?",
    "Type (policy, legislation, regulation, grant, tax credit, "
    "voluntary initiative, etc.)",
    "Description",
    "Exclusive to Data Centers?",
    "Status",
    "Date Issued (newest version)",
    "Link",
    "Notes",
    "Person Who Added it to the Database",
]

# --- PolicyPulse extras appended after the master columns ---
EXTRA_HEADERS = [
    "Relevance Score",
    "Lifecycle Stage",
    "Policy Type (raw)",
    "Key Requirements",
    "Bill Number",
    "Source Language",
    "Discovered At",
    "Crawl Status",
    "Review Status",
    "Verification Flags",
    "Referenced Policies",
    "Referenced URLs",
    "Scan ID",
    "Domain ID",
    "Error Details",
]

STAGING_HEADERS = MASTER_HEADERS + EXTRA_HEADERS

# Header of the column holding a policy's canonical URL. scan_manager dedupes
# staged rows against this column, so keep it in sync with the "Link" mapping.
LINK_HEADER = "Link"

# Header of the review-status column — the sheet reconciliation pass
# (SheetsClient.update_review_statuses) writes a rejected policy's status
# here by URL match.
REVIEW_STATUS_HEADER = "Review Status"

# Label rows this tool auto-generates, so a curator knows the provenance.
ADDED_BY = "PolicyPulse (automated)"


# --- Policy type -> master "Type" label ---
# Grounded in the vocabulary the master database already uses
# (Policy, Legislation, Regulation, Directive, Grant Program,
#  Voluntary Initiative, Executive Order, Tax Credit, Standard, Guidance).
_TYPE_LABELS = {
    "law": "Legislation",
    "regulation": "Regulation",
    "directive": "Directive",
    "incentive": "Incentive",
    "tax_incentive": "Tax Credit",
    "grant": "Grant Program",
    "plan": "Policy",
    "requirement": "Regulation",
    "standard": "Standard",
    "guidance": "Guidance",
    "matching_platform": "Voluntary Initiative",
    "unknown": "",
}

# --- Lifecycle stage -> master "Status" label ---
_STATUS_LABELS = {
    "proposed": "Proposed",
    "consultation": "Consultation",
    "in_committee": "In Committee",
    "passed": "Passed",
    "enacted": "Enacted",
    "transposition_notified": "Transposed",
    "amended": "Amended",
    "unknown": "",
}

# --- Geography lookups ------------------------------------------------------

# US state display names built from the canonical abbreviation table.
_US_STATE_NAMES = {
    key.replace("-", " "): key.replace("-", " ").title().replace(" Of ", " of ")
    for key in US_STATE_ABBREVS
}

_US_FEDERAL = {
    "us", "usa", "u.s.", "u.s.a.", "united states",
    "united states of america", "america", "federal",
}
_EU_WIDE = {
    "eu", "e.u.", "european union", "eu member states",
    "eu-wide", "europe wide", "europe-wide",
}
_UK_NATIONAL = {
    "uk", "u.k.", "united kingdom", "great britain", "britain", "england",
}
_UK_DEVOLVED = {"scotland", "wales", "northern ireland"}

# Country (normalized lower name) -> (Geographical Area, display Country name)
_COUNTRY_TO_AREA = {
    "germany": ("Europe", "Germany"),
    "france": ("Europe", "France"),
    "netherlands": ("Europe", "Netherlands"),
    "denmark": ("Europe", "Denmark"),
    "sweden": ("Europe", "Sweden"),
    "norway": ("Europe", "Norway"),
    "ireland": ("Europe", "Ireland"),
    "switzerland": ("Europe", "Switzerland"),
    "austria": ("Europe", "Austria"),
    "belgium": ("Europe", "Belgium"),
    "spain": ("Europe", "Spain"),
    "italy": ("Europe", "Italy"),
    "poland": ("Europe", "Poland"),
    "portugal": ("Europe", "Portugal"),
    "czech republic": ("Europe", "Czech Republic"),
    "czechia": ("Europe", "Czech Republic"),
    "greece": ("Europe", "Greece"),
    "hungary": ("Europe", "Hungary"),
    "romania": ("Europe", "Romania"),
    "finland": ("Europe", "Finland"),
    "iceland": ("Europe", "Iceland"),
    "estonia": ("Europe", "Estonia"),
    "luxembourg": ("Europe", "Luxembourg"),
    "canada": ("North America", "Canada"),
    "mexico": ("North America", "Mexico"),
    "singapore": ("Asia-Pacific", "Singapore"),
    "japan": ("Asia-Pacific", "Japan"),
    "south korea": ("Asia-Pacific", "South Korea"),
    "korea": ("Asia-Pacific", "South Korea"),
    "australia": ("Asia-Pacific", "Australia"),
    "india": ("Asia-Pacific", "India"),
    "china": ("Asia-Pacific", "China"),
    "united arab emirates": ("Middle East", "United Arab Emirates"),
    "uae": ("Middle East", "United Arab Emirates"),
    "saudi arabia": ("Middle East", "Saudi Arabia"),
    "south africa": ("Africa", "South Africa"),
    "brazil": ("South America", "Brazil"),
}


def _normalize(text: str) -> str:
    return " ".join(text.replace("_", " ").replace("-", " ").split()).lower()


def _word_in(needle: str, haystack: str) -> bool:
    """True if needle appears as a whole word/phrase in haystack."""
    return re.search(rf"\b{re.escape(needle)}\b", haystack) is not None


def _match_place(cand: str) -> tuple[str, str, str] | None:
    """Exact-match a normalized candidate to (area, country, region)."""
    if cand in _US_FEDERAL:
        return ("North America", "USA", "National")
    if cand in _US_STATE_NAMES:
        return ("North America", "USA", _US_STATE_NAMES[cand])
    if cand in _EU_WIDE:
        return ("Europe", "EU Member States", "Regional")
    if cand in _UK_NATIONAL:
        return ("Europe", "United Kingdom", "National")
    if cand in _UK_DEVOLVED:
        return ("Europe", "United Kingdom", cand.title())
    area_country = _COUNTRY_TO_AREA.get(cand)
    if area_country:
        area, country = area_country
        return (area, country, "National")
    return None


def _contains_place(text: str) -> tuple[str, str, str] | None:
    """Whole-word containment fallback for messy values ("Stockholm, Sweden").

    Checks specific places (states, UK nations, countries) only — not the broad
    "eu"/"us" tokens — so a country name always wins over a bloc qualifier.
    """
    for name, display in _US_STATE_NAMES.items():
        if _word_in(name, text):
            return ("North America", "USA", display)
    for term in _UK_DEVOLVED:
        if _word_in(term, text):
            return ("Europe", "United Kingdom", term.title())
    for name, (area, country) in _COUNTRY_TO_AREA.items():
        if _word_in(name, text):
            return (area, country, "National")
    return None


def _candidates(low: str) -> list[str]:
    """Normalized value plus qualifier-stripped variants, most specific first."""
    cands = [low]
    stripped = re.sub(r"\s*\([^)]*\)\s*$", "", low).strip()
    if stripped and stripped != low:
        cands.append(stripped)
    for c in list(cands):
        c2 = re.sub(r"\bstate\b$", "", c).strip()
        if c2 and c2 != c:
            cands.append(c2)
    return cands


def split_jurisdiction(jurisdiction: str) -> tuple[str, str, str]:
    """Resolve a free-text jurisdiction into (geographical_area, country, region).

    Best effort: recognized places fill all three columns the way the master
    database does (e.g. "New Jersey" -> North America / USA / New Jersey).
    Handles the qualifier-tagged values the extraction LLM emits, such as
    "Sweden (EU)" or "Finland (EU-wide regulation)". Unrecognized values go to
    Country untouched so nothing is lost.
    """
    raw = (jurisdiction or "").strip()
    if not raw:
        return ("", "", "")

    low = _normalize(raw)

    for cand in _candidates(low):
        hit = _match_place(cand)
        if hit:
            return hit

    contained = _contains_place(low)
    if contained:
        return contained

    # Unknown jurisdiction: keep the original text in Country.
    return ("", raw, "")


def type_label(policy_type_value: str) -> str:
    """Map a PolicyType value to the master database's Type vocabulary."""
    return _TYPE_LABELS.get(policy_type_value, "")


def status_label(lifecycle_stage: str) -> str:
    """Map a lifecycle stage to the master database's Status vocabulary."""
    return _STATUS_LABELS.get(lifecycle_stage or "", "")


def _join_jurisdiction(geo_area: str, country: str, region: str) -> str:
    """Best-effort inverse of split_jurisdiction().

    split_jurisdiction() discards precision (e.g. "US" and "United States"
    both normalize to the same place), so this cannot recover the original
    free-text jurisdiction exactly. It recovers enough to re-scope a policy
    on import: the region name when it identifies a specific place (a US
    state, a UK nation), otherwise the country.
    """
    region = (region or "").strip()
    country = (country or "").strip()
    if region and region not in {"National", "Regional"}:
        return region
    return country


def _split_list(value: str, sep: str) -> list[str]:
    """Inverse of "sep.join(...)": split and drop empty/whitespace entries."""
    return [v.strip() for v in (value or "").split(sep) if v.strip()]


def _parse_sheet_datetime(value: str) -> str | datetime:
    """Normalize a datetime string as Google Sheets renders it.

    Values are written as ISO (USER_ENTERED), but Sheets re-renders them on
    read as e.g. "2026-07-07 6:28:07" - a single-digit hour that strict ISO
    parsing rejects. strptime tolerates the missing zero-padding; anything
    else passes through unchanged for pydantic to parse.
    """
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value


def from_staging_row(row: dict) -> dict:
    """Map a Staging sheet row (header-keyed dict) into Policy constructor kwargs.

    Best-effort inverse of to_staging_row(). The human-curation columns
    ("Incentive, Standard, or Enabler?", "Exclusive to Data Centers?", "Notes",
    "Person Who Added it to the Database") carry no Policy field and are
    ignored. Unknown/extra columns in `row` are likewise ignored — only the
    headers this function looks up are read.

    This is pure reshaping, not validation: it never raises. Missing/blank
    values fall back to the same defaults Policy() itself would use, except
    url/policy_name which are passed through as empty strings for the caller
    to reject. Callers should pass the result to Policy(**kwargs) and handle
    any pydantic ValidationError.
    """

    def g(header: str) -> str:
        value = row.get(header, "")
        return "" if value is None else str(value).strip()

    jurisdiction = _join_jurisdiction(
        g("Geographical Area"), g("Country"), g("Region"),
    )

    kwargs: dict = {
        "url": g("Link"),
        "policy_name": g("Name"),
        "jurisdiction": jurisdiction,
        "policy_type": g("Policy Type (raw)") or "unknown",
        "summary": g("Description"),
        "relevance_score": g("Relevance Score") or 0,
        "source_language": g("Source Language") or "English",
        "bill_number": g("Bill Number") or None,
        "key_requirements": g("Key Requirements") or None,
        "crawl_status": g("Crawl Status") or "success",
        "error_details": g("Error Details") or None,
        "review_status": g("Review Status") or "new",
        "scan_id": g("Scan ID") or None,
        "domain_id": g("Domain ID") or None,
        "verification_flags": _split_list(g("Verification Flags"), ","),
        "referenced_policies": _split_list(g("Referenced Policies"), ";"),
        "referenced_urls": _split_list(g("Referenced URLs"), ";"),
        "lifecycle_stage": g("Lifecycle Stage") or "unknown",
    }

    # Optional date/datetime fields: omit entirely when blank so Policy's own
    # defaults (None / discovered_at=now) apply, rather than passing "".
    effective_date = g("Date Issued (newest version)")
    if effective_date:
        kwargs["effective_date"] = effective_date
    discovered_at = g("Discovered At")
    if discovered_at:
        kwargs["discovered_at"] = _parse_sheet_datetime(discovered_at)

    return kwargs


def to_staging_row(policy) -> list:
    """Serialize a Policy into a Staging row aligned with STAGING_HEADERS.

    Duck-typed on the Policy attributes to avoid importing core.models here.
    """
    geo_area, country, region = split_jurisdiction(policy.jurisdiction)
    flags = ", ".join(f.value for f in policy.verification_flags)
    return [
        # --- master columns ---
        geo_area,
        country,
        region,
        policy.policy_name,
        "",  # Incentive, Standard, or Enabler? — human curation
        type_label(policy.policy_type.value),
        policy.summary,
        "",  # Exclusive to Data Centers? — human curation
        status_label(policy.lifecycle_stage),
        policy.effective_date.isoformat() if policy.effective_date else "",
        policy.url,
        "",  # Notes — human curation
        ADDED_BY,
        # --- PolicyPulse extras ---
        policy.relevance_score,
        policy.lifecycle_stage,
        policy.policy_type.value,
        policy.key_requirements or "",
        policy.bill_number or "",
        policy.source_language,
        policy.discovered_at.isoformat(),
        policy.crawl_status,
        policy.review_status,
        flags,
        "; ".join(policy.referenced_policies) if policy.referenced_policies else "",
        "; ".join(policy.referenced_urls) if policy.referenced_urls else "",
        policy.scan_id or "",
        policy.domain_id or "",
        policy.error_details or "",
    ]
