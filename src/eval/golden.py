"""The labelled set the pipeline is measured against, and what must survive.

Nothing in this system measured whether a change helped. The 21 per cent
precision figure came from a person counting by hand, and there was nowhere
to put the answer back, so every subsequent tuning argument would have been
opinion against opinion.

Two pieces live here. The golden set is versioned labels, one row per
document, keep or reject with the reason category. Protected recall is the
much smaller list of documents that any change must still retrieve, no
matter what it does to precision.

**Where the protected list comes from matters.** These are not documents
chosen because they are convenient to retrieve. Twelve of the thirteen are
the curated master tab of the Heat Reuse Policies Database, which is what a
human reviewer decided to keep, seven of them entered by hand. The
thirteenth is Virginia HB 323, the first state law on data centre heat
reuse, which was absent from the production database on 2026-08-28 and is
the reason any of this work happened.

Measured on 2026-08-28, applying the required scope rule to the name and
description held in the sheet, three of the thirteen would be dropped: the
NYSERDA Heat Recovery Program, the EMB3RS heat and cold matching platform,
and the New York Utility Thermal Energy Network and Jobs Act. That is an
upper bound rather than a prediction, because the live gate reads the full
source document and those three may well name a data centre somewhere in
it. Which is exactly why they are listed: the test says the day the rule
drops one, instead of the rule dropping it quietly.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

GOLDEN_DIR = Path("data") / "golden"

#: The reasons a reviewer rejects an item, drawn from the categories the
#: research review actually produced. "other" always carries a note.
REJECTION_REASONS = (
    "wrong_document_type",
    "thermal_network_without_data_centre",
    "wrong_jurisdiction",
    "proceedings_or_transcript",
    "report_about_policy",
    "duplicate",
    "other",
)

#: Documents any rule change must still retrieve. URL first, then why it is
#: here, so a failure message can say what was lost rather than only that
#: something was.
PROTECTED_RECALL = (
    ("https://lis.virginia.gov/bill-details/20261/HB323",
     "Virginia HB 323, the first state law on data centre heat reuse. "
     "Absent from production on 2026-08-28; the reason for this work."),
    ("https://eur-lex.europa.eu/eli/dir/2023/1791/oj/eng",
     "EU Energy Efficiency Directive 2023/1791, Article 26(6). Curated keep, "
     "marked not exclusive to data centres."),
    ("https://www.gesetze-im-internet.de/enefg/",
     "German Energy Efficiency Act. Curated keep, marked not exclusive."),
    ("https://legislation.nysenate.gov/pdf/bills/2021/S9422",
     "New York Utility Thermal Energy Network and Jobs Act. Curated keep, and "
     "the case the data-centre scope rule is most likely to cost us."),
    ("https://www.nyserda.ny.gov/All-Programs/Heat-Recovery-Program",
     "NYSERDA Heat Recovery Program. Curated keep, at risk from the scope rule."),
    ("https://www.emb3rs.eu/",
     "EMB3RS heat and cold matching platform. Curated keep, at risk from the "
     "scope rule."),
    ("https://app.leg.wa.gov/rcw/default.aspx?cite=43.31.635",
     "Washington Industrial Symbiosis Program. Curated keep."),
    ("https://www.regjeringen.no/en/dokumenter/norwegian-data-centres-sustainable-digital-powerhouses/id2867155/?ch=4",
     "Norway's data centre heat reuse assessment mandate. Curated keep."),
    ("https://lokaleregelgeving.overheid.nl/CVDR646404",
     "Noord-Holland data centre heat reuse requirement. Curated keep."),
    ("https://www.climateneutraldatacentre.net/",
     "Climate Neutral Data Centre Pact. Curated keep."),
    ("https://lis.virginia.gov/bill-details/20251/HB2578",
     "Virginia HB 2578, failed 2025. Curated keep, and the recall case for "
     "bills that did not pass."),
    ("https://www.energy.gov/articles/doe-announces-40-million-more-efficient-cooling-data-centers",
     "DOE COOLERCHIPS funding. Curated keep."),
    ("https://bidenwhitehouse.archives.gov/briefing-room/presidential-actions/2025/01/14/executive-order-on-advancing-united-states-leadership-in-artificial-intelligence-infrastructure/",
     "Executive order on AI infrastructure, waste heat planning. Curated keep."),
)


@dataclass(frozen=True)
class GoldenItem:
    """One labelled document."""

    url: str
    keep: bool
    reason: str = ""
    labelled_by: str = ""
    labelled_on: str = ""


class GoldenSetError(Exception):
    """The labelled set could not be loaded or is malformed."""


def _parse_row(row: dict, line_no: int) -> GoldenItem:
    url = (row.get("url") or "").strip()
    if not url:
        raise GoldenSetError(f"line {line_no}: no url")
    if "keep" not in row:
        raise GoldenSetError(f"line {line_no}: no keep/reject decision for {url}")
    keep = bool(row["keep"])
    reason = (row.get("reason") or "").strip()
    if not keep and not reason:
        # A reject with no reason cannot be counted, and an uncountable
        # label is the thing that made the first review unusable as
        # evidence. Refuse it at load rather than at analysis time.
        raise GoldenSetError(
            f"line {line_no}: {url} is a reject with no reason. "
            f"One of: {', '.join(REJECTION_REASONS)}"
        )
    if reason and reason not in REJECTION_REASONS:
        raise GoldenSetError(
            f"line {line_no}: unknown reason {reason!r} for {url}. "
            f"One of: {', '.join(REJECTION_REASONS)}"
        )
    return GoldenItem(
        url=url,
        keep=keep,
        reason=reason,
        labelled_by=(row.get("labelled_by") or "").strip(),
        labelled_on=(row.get("labelled_on") or "").strip(),
    )


def load_golden(version: str, golden_dir: Path | None = None) -> list[GoldenItem]:
    """Load one version of the labelled set.

    Versions are separate files so a later review round never overwrites an
    earlier one: the point of a golden set is that it is a fixed target.
    """
    root = golden_dir or GOLDEN_DIR
    path = root / f"{version}.jsonl"
    if not path.exists():
        available = sorted(p.stem for p in root.glob("*.jsonl")) if root.exists() else []
        raise GoldenSetError(
            f"No golden set {version!r} at {path}. "
            + (f"Available: {', '.join(available)}" if available
               else "None exist yet; the next review round produces v1.")
        )

    items = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as e:
            raise GoldenSetError(f"line {line_no}: not valid JSON: {e}") from e
        items.append(_parse_row(row, line_no))

    if not items:
        raise GoldenSetError(f"Golden set {version!r} at {path} has no rows")
    return items


def protected_urls() -> set[str]:
    """Just the addresses, exactly as the curated tab records them."""
    return {url for url, _ in PROTECTED_RECALL}


def missing_protected(retrieved: set[str]) -> list[tuple[str, str]]:
    """Protected documents absent from a retrieved set, with their reasons.

    Addresses are compared after normalisation, because a stored URL and a
    curated one differ by a trailing slash or a tracking parameter often
    enough that raw string comparison reports losses that did not happen.
    The first run of this check reported Norway's assessment mandate as
    missing when the only difference was a chapter anchor. A guard that
    cries wolf teaches people to ignore it, which costs more than having no
    guard at all.

    Returned rather than asserted so the caller can report all of them at
    once. A check that stops at the first loss hides the rest, and the rest
    are how you tell a bad rule from a bad document.
    """
    from ..core.urls import normalize_url

    seen = {normalize_url(url) for url in retrieved}
    return [
        (url, why) for url, why in PROTECTED_RECALL
        if normalize_url(url) not in seen
    ]


def _report_missing() -> int:
    """`python -m src.eval.golden` - which protected documents are absent.

    Not a test. A test that asserted these were present would be red until
    a scan has actually run, and a permanently red test teaches people to
    ignore red. This is the operational check: run it after a scan and it
    names what was lost, with the reason each one matters.
    """
    from ..storage.store import PolicyStore

    stored = {(p.get("url") or "").strip() for p in PolicyStore().get_all()}
    missing = missing_protected(stored)

    total = len(protected_urls())
    print(f"protected documents: {total}")
    print(f"present in the store: {total - len(missing)}")
    print()
    if not missing:
        print("Every protected document is present.")
        return 0
    print(f"MISSING {len(missing)}:")
    for url, why in missing:
        print(f"  {url}")
        print(f"    {why}")
    print()
    print("A missing protected document is a recall failure. Nobody sees a "
          "recall failure unless something like this says so.")
    return 1


if __name__ == "__main__":  # pragma: no cover - thin CLI wrapper
    raise SystemExit(_report_missing())
