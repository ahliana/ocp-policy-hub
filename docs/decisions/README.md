# Decision records

One file per decision that would be expensive to relearn. Each record says
what the situation was, what was decided, what it costs, and what evidence
led there. A record is never edited after acceptance; a changed mind is a
new record whose status line names the one it supersedes, and the old
record's status changes to `Superseded by ADR-XXXX`. That way the history of
why stays readable.

Statuses: `Proposed` (written, awaiting the owner's word), `Accepted`,
`Superseded by ADR-XXXX`, `Rejected`.

`tests/unit/test_lessons_traceability.py` checks that every record listed
here exists, every record on disk is listed here, every record has a valid
status line, and every `ADR-XXXX` cited from a test resolves to a file.

To add one: copy `ADR-0000-template.md`, take the next number, keep the
filename as `ADR-NNNN-short-slug.md`, add a line below, and open it in the
same pull request as the change it explains.

| Record | Status | One line |
|---|---|---|
| [ADR-0001](ADR-0001-scope-requires-a-data-centre-on-source-text.md) | Accepted | A document with no data-centre reference in its source text is out of scope, by default |
| [ADR-0002](ADR-0002-structured-sources-bypass-the-keyword-gate.md) | Accepted | Legislation sources skip the keyword gate; cross-source rules sit where the lanes rejoin |
| [ADR-0003](ADR-0003-recall-first-screening.md) | Accepted, under review | The cheap screener keeps anything in doubt; the strong model decides |
| [ADR-0004](ADR-0004-virginia-from-the-lis-session-files.md) | Accepted | Virginia bills come from the LIS session CSVs, not per-bill crawling |
| [ADR-0005](ADR-0005-the-reviewers-column-is-the-review-record.md) | Proposed | The reviewer's sheet column is the review record; the app reads it, never rewrites it |
| [ADR-0006](ADR-0006-one-monthly-trigger.md) | Proposed | The in-app schedule is the only thing that starts the monthly scan |
| [ADR-0007](ADR-0007-kokkai-is-a-signals-source.md) | Proposed | Diet speeches become tips, never policy rows |
