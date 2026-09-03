# ADR-0004: Virginia bills come from the LIS session files, not per-bill crawling

- Status: Accepted
- Date: 2026-08-28
- Owner: the workstream lead
- Supersedes: the three per-bill Virginia crawl domains, now disabled in `config/domains/us/virginia.yaml`

## Context

Virginia HB 323 (2026), the first US state law on data-centre heat reuse,
enacted as Chapter 591, was the flagship recall case. Three crawl domains
existed for it with Playwright enabled and a lowered threshold. None had
ever fetched it, because the scan table was empty (lesson PL-003), and
per-bill crawling of a JavaScript site is fragile and slow in any case.

The Virginia Legislative Information System publishes bulk files per
session with no key, no JavaScript and no registration:
`https://lis.blob.core.windows.net/lisfiles/{session}/{FILE}`, session code
`20261` for the 2026 regular session. `BILLS.CSV` holds every bill with
status flags and chapter id; `Summaries.csv` holds the summaries.

## Decision

`src/sources/va_lis.py` is a structured source (`va_lis`, no API key) that
fetches both files, joins them on a normalised bill number, filters to bills
whose text mentions a data centre, and stages each bill's lifecycle from the
flags: vetoed or failed is `failed`; approved or a chapter id is `enacted`;
passed is `passed`; otherwise `proposed`. The bill URL of record is the LIS
bill-details page.

## Consequences

- One keyless fetch returns 3,646 bills and 5,780 summary rows; 31 bills
  mention a data centre; HB 323 is found and staged `enacted` from
  `Chapter_id=CHAP0591`.
- The same pattern applies to any legislature that publishes session bulk
  files, and is preferred over per-bill crawling wherever it exists.
- Special sessions and years before the 2025 LIS rebuild are refused rather
  than guessed.

## Evidence

- Live fetch, 2026-08-28: counts above.
- Scan `86463134`, 1 September 2026: `va_lis_2026` returned 3,646 items,
  22 dropped at screening, 4 rows stored.

## Guarded by

`tests/unit/test_sources_va_lis.py`: bill-number normalisation (lesson
PL-002), session codes, lifecycle staging on the real HB 323 flags,
registration without an API key, and a fetch test against recorded files.
