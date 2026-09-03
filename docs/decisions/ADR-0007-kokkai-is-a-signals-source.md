# ADR-0007: Kokkai is a signals source, not a policy source

- Status: Proposed
- Date: 2026-09-02
- Owner: the workstream lead
- Supersedes: none

## Context

`src/sources/kokkai.py` reads Japan's Diet proceedings. Its own docstring
says why it exists: Kokkai carries what the Diet is saying, e-Gov carries
what Japan has enacted, and the gap is the signal. On 2026-06-12 an
Environment Ministry official told a committee that Japanese environmental
law has no framework regulating waste heat at all. The docstring also says
that most of its output will correctly fail screening, because speeches are
not policies.

The reviewer removed 11 of 11 Kokkai rows as "not a policy". On 1 September
2026 the source produced 15 items, all 15 went to the strong model, and 12
were stored as policies.

The app has a second lane built for exactly this shape: tips. The weekly
news sweep creates leads (`src/storage/leads.py`, `origin="news"`) that
appear in the Admin tips inbox. A person dismisses a tip or chases it;
chasing fetches the URL and runs analysis, and only then can a row exist.
No model spend happens until someone chases.

## Decision (proposed)

Kokkai becomes a signals source. Each matching speech becomes a lead with
`origin="kokkai"`, the speaker and date in the title, the matching sentence
as the snippet and the speech URL as the source. It never creates a policy
row and never reaches the Staging tab. The alternative, disabling the source
in `config/domains/api_sources.yaml`, loses the early warning.

## Consequences

- Eleven rows of review time per round saved; the reviewer never sees a
  speech unless a person chased it into a policy.
- Fifteen strong-model calls per scan saved.
- The 2026-06-12 statement and its successors still surface, in the inbox
  built for things that are not yet policy.
- A source parameter `lane: signals` would let any future transcript-style
  source take the same path.

## Evidence

- Reviewer's column rows 109 to 119, read 2 September 2026.
- Scan `86463134`: `kokkai_api` 15 items, 15 analyses, 12 stored.
- `src/sources/kokkai.py` docstring.

## Guarded by

`none yet`. WP-3 adds a test that a Kokkai record in the signals lane
produces a lead and zero policies.
