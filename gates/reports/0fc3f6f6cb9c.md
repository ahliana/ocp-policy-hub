<!-- proofmark-ship head=0fc3f6f6cb9c6e9741a5a8f0f951fafa48aebb5f -->
# Report - OCP-CE-HR-Policy-Searcher, ocp/main..HEAD

## What shipped
- 0fc3f6f Merge branch 'feature/scan-cost-ux-phase-b'
- d96e304 docs(report): ship report for the e2e race fix
- c171ed9 test(e2e): atomic geometry read in the containment spec
- 7bed450 docs(report): ship report for Phase B
- 9b3da58 docs(report): ship report for Phase A
- 81ab0bf feat(costs): calibrated range estimates, funnel ledger, estimate-vs-actual
- 8c816ac fix(costs): true estimates and exact actuals; mid-scan budget stop
- d0c0cab fix(ui): contain admin panels in frame; honest estimate empty states
- 877ec61 chore(gates): sync gate.py from canonical (never-fix-twice counts JS tests)

44 files changed, 4532 insertions(+), 326 deletions(-)

## Test floor
1869 -> 1966  (ratcheted)

## Coverage (reported fact, never a gate)
TOTAL 9121 1217 87%  measured 2026-08-26T12:34:15+00:00

## What the gates did
passes 32, bound 9, blocks 4, overrides 0, exceptions 0
    block     never-fix-twice: fix commit with no test change: fix(ui): contain admin panels in frame; honest estimate empty states
    block     unmarked-ratchet: 1740 unmarked tests > baseline 1694: a new test must carry a size marker - small (hermetic, enforced), medium 
    block     ruff-swallow: exception swallowing (L002), not in baseline: ['src/orchestration/scan_manager.py::BLE001::ScanManager._run_sc
    block     pytest: tests failed: ERROR    src.orchestration.scan_manager:scan_manager.py:794 Scan f2efa623 failed: db locked; FAI

## Rollback
    git revert --no-edit 547bfd60962a..HEAD
    # then restore gates/min_test_count.txt to 1869; the floor does not fall on its own

## To be written by a person - the tool cannot know these
- What you found and did NOT fix: this is the merge landing of PRs #31 and #32 on Ahliana's word - both branch pushes carry their own filled reports (d0c0cabefc07, 81ab0bf1ac7b, c171ed9ce2b7); their open items stand unchanged. Production still runs 5a7ad2f - deploy is the recommended next step before the Sept 1 scheduled scan so the budget stop and estimate ledger are live for it.
- The diagnosis you got wrong first, and what corrected it: none in this landing.
- Numbered open questions: carried from the branch reports.
- Verified live by fetching real content (not a status code)? what, and what did it say: PR #31 and #32 CI both fully green on GitHub (backend + frontend + DCO); the containment spec ran 4/4 against the live dev stack earlier this session.
