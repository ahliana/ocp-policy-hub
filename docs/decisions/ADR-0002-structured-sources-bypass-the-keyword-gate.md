# ADR-0002: Structured sources bypass the keyword gate; cross-source rules sit where the lanes rejoin

- Status: Accepted
- Date: original design, recorded 2026-09-02
- Owner: the workstream lead
- Supersedes: none

## Context

Two kinds of source feed the pipeline. Crawled pages are anything a site
links to, so a keyword gate is the only thing between a press release and a
model call. Structured sources (legislation APIs and bulk files) return
records that already matched a query; a bill about waste heat returned by
LegiScan does not need to be re-scored for the word "heat".

The consequence that was not obvious until measured: structured sources
produce most of what reaches the database. About 104 of the 143 rows the
reviewer saw came from them and about 9 from crawled pages, against 378
crawl domains. So the keyword gate, the stage everyone planned to tune,
governs about six percent of output.

## Decision

In `src/core/scanner.py`, a structured source's records are marked relevant
unconditionally and skip the keyword stage. Any rule that must see every
document, whatever its source, is placed after the cache check and before
the screener, where the two lanes have rejoined. The scope gate (ADR-0001)
sits there. A rule added to the keyword layer would never see a LegiScan
bill.

## Consequences

- Precision work on structured sources happens at the source (document-type
  allow-lists, correct document URLs; work package WP-3) or at the junction
  (scope gate, screener), never in `config/keywords.yaml`.
- The keyword gate remains the right place for crawl-only noise and is
  admin-editable in the Keywords panel.
- Any new cross-source rule must be placed at the junction, and a test reads
  the pipeline source to check the placement.

## Evidence

- Production store, 2026-08-28: 143 rows, `domain_id` distribution.
- Scan `86463134`, 1 September 2026: 15,578 dropped at the keyword gate,
  none of them from the 23 `law_apis` sources.

## Guarded by

`tests/unit/test_scope.py::TestTheGateSeesStructuredSources`, which reads
the pipeline source and fails if the gate moves inside the crawl-only
branch or after the screening call.
