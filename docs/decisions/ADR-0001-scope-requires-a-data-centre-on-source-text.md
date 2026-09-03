# ADR-0001: Scope requires a data-centre reference, evaluated on source text

- Status: Accepted
- Date: 2026-08-28
- Owner: the workstream lead, on the reviewer's rule
- Supersedes: none

## Context

The reviewer and the pipeline disagreed about the subject. The screening
prompt kept thermal energy network bills, district heating incentives and
generic efficiency acts because they could affect data-centre heat reuse.
The reviewer's rule, stated on 2026-08-28: a policy without a data centre is
not what this tool is looking for. Her review of the first 143 rows removed
14 for exactly this reason.

A first draft of the rule was evaluated against the sheet's short
descriptions. Two rejected bills survived it, because the analysis model had
written "data centers" into their summaries. The bills never say it.

## Decision

`src/core/scope.py` defines one setting, `analysis.data_center_required`,
with three values: `required` (default), `adjacent` (keep and mark),
`off` (no change). Under `required`, a document whose extracted text never
mentions a data centre, in any of the configured languages and spellings, is
dropped before any model call.

The gate runs on `extracted.text`, never on a stored summary or description.
The same setting generates the scope sentence in the screening prompt, so the
gate and the model are told the same thing. An unknown setting value narrows
to `required` rather than widening.

The recommendation at the time was `adjacent`; the owner chose `required`.
The cost is made visible rather than argued: a protected-recall list is
checked by name after every rule change.

## Consequences

- On 1 September 2026 the gate dropped 7,909 pages before any model call.
- Three of the reviewer's own earlier curated keeps (the NY Utility Thermal
  Energy Network Act, NYSERDA's Heat Recovery Program, the EMB3RS platform)
  fall outside the rule when it is applied to their short descriptions. They
  sit on the protected list so the check names them rather than the rule
  dropping them quietly; she has since removed the first of the three
  herself.
- Any future rule that reads summaries instead of source text will silently
  stop working. Lesson PL-001.

## Evidence

- Reviewer's rule, 2026-08-28, recorded in the work log.
- NJ A4490 stored summary versus bill text, 2026-08-28.
- Scan `86463134`, 1 September 2026: `filtered_out_of_scope` 7,909.
- Reviewer's column, read 2 September 2026: 14 removals for "no data centre".

## Guarded by

`tests/unit/test_scope.py`: the rule, the three settings, the default, the
prompt line, the placement after the lanes rejoin and before the screener,
and `TestTheGateReadsSourceText` for the summary trap.
