# ADR-0003: Recall-first screening in front of the analysis model

- Status: Accepted, under review by work package WP-5
- Date: original design, recorded 2026-09-02
- Owner: the workstream lead
- Supersedes: none

## Context

The strong analysis model is expensive per call. A cheap screener in front
of it was designed to save money without losing anything, so its prompt was
written recall-first: keep any page that could plausibly affect data-centre
heat reuse, even indirectly; when in doubt, keep it. A rejection only sticks
above a confidence threshold; below it the page escalates to the strong
model anyway.

Measured on the first full monthly scan (1 September 2026): 636 pages
screened, 445 passed (70 percent), and the strong model then found no policy
in 285 of those 445. One Pennsylvania domain passed 199 pages and produced
zero rows. The strong model took 86 percent of the scan's $9.05.

The reviewer's removals show what the screener lets through: reports, blog
posts, project news, opinion pieces, and bills that mention data centres
without any heat-reuse substance. Twelve of her 88 removals are "not a
policy" of the report-or-article kind and six are "data centre present, no
heat reuse".

## Decision

Kept as designed until a replacement is measured against a golden set.
The replacement under review (WP-5) asks the same cheap model three narrow
questions instead of one wide one: the kind of document from a fixed list;
the sentence that names a data centre, quoted; the sentence about reusing
or recovering heat, quoted. A report or article stops at the screener; under
`required`, no data-centre quote stops at the screener. The quotes are kept
on the record.

The bar for adopting the replacement: zero lost keeps on the reviewer's 32
keeps, and a measured fall in strong-model calls on zero-yield domains.

## Consequences

- Until WP-5 lands, most strong-model spend buys nothing, and the estimate
  and the actual both reflect that.
- Changing the screening prompt changes the rules fingerprint, so the first
  scan after the change re-screens every cached page. That is intended and
  costs about $1.30 at the 1 September volume.

## Evidence

- Scan `86463134`, 1 September 2026, per-call token log: screening 636
  calls, analysis 445, zero-yield analyses 285 across 52 domains.
- Reviewer's column, read 2 September 2026.

## Guarded by

`none yet`. WP-5 adds recorded screening-window fixtures for the reviewer's
keeps and a golden test that fails on any lost keep.
