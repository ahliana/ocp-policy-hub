# ADR-0005: The reviewer's sheet column is the review record; the app reads it and never rewrites it

- Status: Proposed
- Date: 2026-09-02
- Owner: the workstream lead, with the reviewer
- Supersedes: none

## Context

The review surface is the Google Sheet, not the app. The reviewer added a
column (AC on the Staging tab of "Heat Reuse Policies Database") and wrote a
verdict and a reason beside 134 of 143 rows: 32 keep, 88 remove, 14 to be
decided, 9 blank. Nothing in the app reads that column. The app's own
`Review Status` column on the sheet holds only `new` and `promoted`, set by
the app.

Two consequences follow. The public site, whose default posture shows every
find except `rejected`, shows all 88 rows she removed. And the measurement
package shipped on 2026-08-28 has been waiting for labels that have existed
in her column since July.

## Decision (proposed)

The reviewer's column is the review record. A one-way import
(`src/output/import_reviews.py`, work package WP-2) reads it and updates the
app: `keep` becomes `reviewed`, `remove` becomes `rejected` with her reason
stored as the review note, `tbd` and blank are left as `new`. The import is
idempotent and runs before each monthly scan, so deduplication and the
same-instrument check see her decisions. The app never writes into her
column. `promoted` keeps its existing meaning, moved to the master tab by a
person, and outranks `reviewed`.

A "Reason" column with a fixed dropdown of the eight reason categories is
appended after her column so the next review round can be counted without
reading every cell. Her free-text column stays.

The public visibility posture moves to "reviewed only by default, visitors
can switch".

Pending the owner's word: the `keep` to `reviewed` mapping, and pointing
production at the sheet of record (it currently writes to a copy, so scans
since July have not reached her tab).

## Consequences

- Her workflow does not change.
- The 88 removed rows leave the public site on the first import.
- The golden set (WP-1) and every later precision measurement derive from
  the same column, with provenance.
- Three of the 13 rows on the earlier protected-recall list are ones she has
  since removed; the list must be corrected before it is used as a floor.

## Evidence

- Reviewer's column, read 2 September 2026, 143 rows.
- `src/storage/public_visibility.py`: default posture `default_all`.
- Production `SPREADSHEET_ID` points at "Copy of Heat Reuse Policies
  Database", 214 rows; the sheet of record has 143, none newer than
  24 July 2026.

## Guarded by

`none yet`. WP-2 adds an import test in which the CNDCP row becomes
`rejected` with the note "private sector initiative", red until the importer
exists.
