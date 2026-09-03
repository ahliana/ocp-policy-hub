# What changed

For whoever reads the output: reviewers, visitors, the OCP workstream. Say
what changed and why it matters to them, not what the code does. Newest
first. The Proofmark gate refuses a `feat:` commit that changes source
without a line here (see `proofmark.toml`, `[proofmark.changelog]`).

<!-- proofmark:changelog -->
## Unreleased

- 2026-09-02 The reasoning behind every filter is now written down where a
  person can read it (`docs/HOW_IT_WORKS.md`), with decision records and a
  lessons register that a test keeps honest. Nothing about scan behaviour
  changed.

## 2026-08-31

- Virginia bills now come from the state's own session files: every 2026
  bill in one fetch, HB 323 included.
- A document that never mentions a data centre is dropped before any model
  spend. The setting is `analysis.data_center_required`, default `required`.
- Cached verdicts expire when a rule changes, so a rule change applies to
  every page, not only new ones.
- Every source explains itself in Admin under Sources.
<!-- /proofmark:changelog -->
