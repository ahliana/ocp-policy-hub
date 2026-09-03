# ADR-0006: One monthly trigger, the in-app schedule

- Status: Proposed
- Date: 2026-09-02
- Owner: the workstream lead
- Supersedes: none

## Context

Three separate things try to start the monthly scan:

1. The in-app schedule "Monthly full scan" (`schedules` table; 1st of the
   month, 06:00 UTC; all channels; ceiling $50). On 1 September 2026 it
   started scan `86463134` at 06:00:00, which completed, stored 71 rows and
   recorded its estimate and actual.
2. A server cron line that runs the CLI agent with "Scan the all group".
   On 1 September it started a second, crawl-only scan `81bcf50d` at
   06:00:22, which produced no rows, spent nothing, and left a row marked
   `running` that never completes.
3. A GitHub Actions workflow (`.github/workflows/monthly-scan.yml`) that has
   failed 62 seconds in every month since 1 August for want of an
   `ANTHROPIC_API_KEY` repository secret.

Two of the three have never done useful work and both are ways to spend
twice.

## Decision (proposed)

The in-app schedule is the only trigger. The server cron monthly line is
removed by read-modify-write (never `| crontab -`, which once replaced every
job on the host). The GitHub Actions monthly workflow is disabled. Scan row
`81bcf50d` is marked failed with a note. The weekly signals cron line is
unaffected by this record.

## Consequences

- One place to look for what ran, what it cost, and what it was estimated
  to cost.
- The $50 ceiling and the busy check in `schedule_runner.py` apply to every
  monthly run, including manual "Run now".
- If the container is down at 06:00 on the 1st, the scan does not run that
  month; the schedule's `next_run_at` shows it, and Run now covers it.

## Evidence

- `schedules` table and `scans` table on the server, read 2 September 2026.
- `crontab -l` on the server: two `POLICYPULSE` lines, weekly and monthly.
- Actions run history for `monthly-scan.yml`.

## Guarded by

`none`. This is server configuration; `docs/OPERATIONS.md` records the
single trigger once the change is made.
