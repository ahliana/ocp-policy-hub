<!-- proofmark-ship head=d0c0cabefc07ab02a129dd44bf633bd646a23e39 -->
# Report - OCP-CE-HR-Policy-Searcher, ocp/main..HEAD

## What shipped
- d0c0cab fix(ui): contain admin panels in frame; honest estimate empty states
- 877ec61 chore(gates): sync gate.py from canonical (never-fix-twice counts JS tests)

13 files changed, 865 insertions(+), 204 deletions(-)

## Test floor
1869 -> 1869  (unchanged)

## Coverage (reported fact, never a gate)
TOTAL 8848 1235 86%  measured 2026-08-26T06:36:10+00:00

## What the gates did
passes 18, bound 5, blocks 4, overrides 0, exceptions 0
    block     never-fix-twice: fix commit with no test change: fix(ui): contain admin panels in frame; honest estimate empty states
    block     unmarked-ratchet: 1740 unmarked tests > baseline 1694: a new test must carry a size marker - small (hermetic, enforced), medium 
    block     ruff-swallow: exception swallowing (L002), not in baseline: ['src/orchestration/scan_manager.py::BLE001::ScanManager._run_sc
    block     pytest: tests failed: ERROR    src.orchestration.scan_manager:scan_manager.py:794 Scan f2efa623 failed: db locked; FAI

## Rollback
    git revert --no-edit 547bfd60962a..HEAD

## To be written by a person - the tool cannot know these
- What you found and did NOT fix: the new Playwright containment spec (frontend/e2e/admin-containment.spec.js) has not yet run against a live stack - run npm run e2e at next dev-stack session; KeywordsPanel needed no table wrap (details/ul only), verified not skipped.
- The diagnosis you got wrong first, and what corrected it: none.
- Numbered open questions: none.
- Verified live by fetching real content (not a status code)? what, and what did it say: frontend suite 391 passing including new DOM-structure assertions that each admin table sits inside .admin-table-wrap and the visibility label/hint are distinct block elements; node --check confirms the e2e spec parses.

## Escapes to analyse - to be written by a person

One block per escape. A run of fix commits on one day for one thing is
one escape, not several. `none` is a real answer to the first question -
some defects no mechanical gate could have caught, and saying so is the
analysis. The second is yes or no.

### escape 2026-08-26 costs (1 fix commit)
- Which gate should have caught it (name one, or `none`): none
- Did that gate already exist and pass anyway (yes/no): no

### escape 2026-08-26 ui (1 fix commit)
- Which gate should have caught it (name one, or `none`): none
- Did that gate already exist and pass anyway (yes/no): no
