<!-- proofmark-ship head=cc3aa018fa9cd18852d10c8412ee5d23c4841c0e -->
# Report - OCP-CE-HR-Policy-Searcher, d77eef9..HEAD

## What shipped
- cc3aa01 docs: say that the e2e suite needs the admin token
- b4dec38 chore(gates): ring sync, amended fix commits are judged on the commit
- ca1ae71 fix(sources): every source now says what it finds, in a sentence
- c05efdc chore(gates): move the 24 source classes out of the debt baseline
- a37905f feat(eval): measure the pipeline, and guard the documents already kept
- b4e736b feat(scope): require a data centre reference, and re-decide on rule change
- a3eaeed feat(sources): read Virginia bills from the LIS session files

32 files changed, 2341 insertions(+), 63 deletions(-)

## Test floor
2296 -> 2380  (ratcheted)

## Coverage (reported fact, never a gate)
TOTAL 10027 1243 88%  measured 2026-08-28T17:33:46+00:00

## What the gates did
passes 17, bound 9, blocks 9, overrides 0, exceptions 4
    block     vulture-orphan: unwired code (L003), not in baseline: ['src\\sources\\va_lis.py::class::VirginiaLISSource']
    block     vulture-ratchet: baseline grew vs HEAD (debt can only shrink): ['src\\sources\\va_lis.py::class::VirginiaLISSource']
    exception swallow-ratchet: PROOFMARK_ACCEPT_DEBT=1
    exception vulture-ratchet: PROOFMARK_ACCEPT_DEBT=1
    exception swallow-ratchet: PROOFMARK_ACCEPT_DEBT=1
    exception vulture-ratchet: PROOFMARK_ACCEPT_DEBT=1
    block     vulture-orphan: unwired code (L003), not in baseline: ['src\\core\\cache.py::attribute::rules_changed', 'src\\core\\cache.py::
    block     unmarked-ratchet: 1691 unmarked tests > baseline 1690: a new test must carry a size marker - small (hermetic, enforced), medium 
    block     unmarked-ratchet: 1691 unmarked tests > baseline 1690: a new test must carry a size marker - small (hermetic, enforced), medium 
    block     vulture-orphan: unwired code (L003), not in baseline: ['src\\eval\\golden.py::function::protected_urls', 'src\\eval\\golden.py
    block     never-fix-twice: fix commit with no test change: fix(sources): every source now says what it finds, in a sentence
    block     never-fix-twice: fix commit with no test change: fix(sources): every source now says what it finds, in a sentence
    block     never-fix-twice: fix commit with no test change: fix(sources): every source now says what it finds, in a sentence

## Rollback
    git revert --no-edit d77eef9077d1..HEAD
    # then restore gates/min_test_count.txt to 2296; the floor does not fall on its own

## To be written by a person - the tool cannot know these
- What you found and did NOT fix:
- The diagnosis you got wrong first, and what corrected it:
- Numbered open questions:
- Verified live by fetching real content (not a status code)? what, and what did it say:

## Escapes to analyse - to be written by a person

One block per escape. A run of fix commits on one day for one thing is
one escape, not several. `none` is a real answer to the first question -
some defects no mechanical gate could have caught, and saying so is the
analysis. The second is yes or no.

### escape 2026-08-29 sources (2 fix commits)
- Which gate should have caught it (name one, or `none`):
- Did that gate already exist and pass anyway (yes/no):
