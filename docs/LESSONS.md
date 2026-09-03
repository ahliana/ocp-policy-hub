# Lessons register

One entry per defect that cost real time, in the order it was learned. Each
entry names the test that now fails if the defect comes back, or says
plainly that nothing mechanical holds it yet and what will.

The format follows the user-level lessons register: an id, a title, when it
was first and last seen, how many times, a status, and the enforcement. A
lesson is `mechanized` when a test or gate catches it, `open` when only this
page and a reader's memory do.

`tests/unit/test_lessons_traceability.py` reads this file. Every `guard:`
line must name a test that exists (`tests/unit/test_x.py::TestClass::test_y`)
or read `guard: none` followed by a reason in parentheses. A lesson whose
guard vanishes fails the fast suite, and the fast suite runs on every commit.

How to add one: copy the block, give it the next id, write the defect in one
paragraph a stranger would understand, write the occurrence with the date and
what it cost, name the guard. Link the decision record if there is one. Do
not soften it.

---

## PL-001

- title: A rule evaluated on a model-written summary silently stops working
- first_seen: 2026-08-28
- last_seen: 2026-08-28
- recurrences: 1
- status: mechanized
- guard: tests/unit/test_scope.py::TestTheGateReadsSourceText::test_the_verdict_is_taken_on_extracted_text_not_a_summary
- decision: [ADR-0001](decisions/ADR-0001-scope-requires-a-data-centre-on-source-text.md)
- class: verdict depends on derived data that the verdict itself shaped

**The defect.** The analysis model writes English summaries, and for bills
adjacent to the subject it writes the subject in: NJ A4490's stored summary
says the network "could incorporate waste heat sources including data
centers"; the bill never mentions a data centre. Any scope or keyword rule
run against stored summaries therefore passes exactly the documents it
exists to drop, and nobody notices because the rule reports that it ran.

**Occurrence.** 2026-08-28, while designing the scope gate. The first draft
evaluated the reviewer's rule against the sheet's short descriptions to
estimate its effect; two of the bills she had rejected survived it. Reading
the source text showed why. The gate was moved to run on `extracted.text`
before any model call, and the docstring of `src/core/scope.py` carries the
warning. Cost: about two hours, and it would have shipped a gate that did
nothing.

**How it is held.** The guard test asserts that the pipeline passes extracted
text, not a summary, to `scope_verdict`, and that the NJ A4490 summary is in
scope while its bill text is out, which is the trap in one assertion.

---

## PL-002

- title: Two files from one publisher spell the same identifier differently
- first_seen: 2026-08-28
- last_seen: 2026-08-28
- recurrences: 1
- status: mechanized
- guard: tests/unit/test_sources_va_lis.py::TestBillNumberNormalisation::test_padded_and_unpadded_numbers_are_one_key
- decision: [ADR-0004](decisions/ADR-0004-virginia-from-the-lis-session-files.md)
- class: agreement assumed between dependent sources

**The defect.** Virginia's LIS bulk store publishes `BILLS.CSV` and
`Summaries.csv` for the same session. The first spells the flagship bill
`HB323`; the second spells it `HB0323`, sometimes with trailing spaces. The
file names also differ in casing on a case-sensitive store. A join on the
raw identifier matches nothing, and a fetch with the wrong casing returns a
404 that looks like "no such session".

**Occurrence.** 2026-08-28, building the Virginia session-file source. The
first join produced zero summaries for 3,646 bills. Cost: under an hour, but
only because the count was checked; a join that silently produced
summary-less rows would have shipped.

**How it is held.** `normalize_bill_no` strips padding and whitespace and the
guard test pins that `HB0323`, `HB323` and `HB323S    ` are one key. The
file names are constants with a comment on the casing.

---

## PL-003

- title: Ask whether it ran before asking why it failed
- first_seen: 2026-08-28
- last_seen: 2026-08-28
- recurrences: 1
- status: open
- guard: none (a diagnostic habit, not a code path; WP-6 persists the per-domain funnel counters so the question "did this source run, and what did it drop" is answerable from the scan record)
- class: absence indistinguishable from failure

**The defect.** A missing result was diagnosed as a filtering problem. The
question "which stage dropped HB 323" had a two-day plan behind it before
anyone checked whether Virginia had been scanned at all. Production held 143
policies and zero rows in `scans` and `scan_domains`; the one Virginia record
had been imported from the reviewer's curated tab. Nothing had dropped the
bill, because nothing had looked for it.

**Occurrence.** 2026-08-28. The plan was reordered once the scan table was
read. Cost: the first hour of the session, and a plan that would have tuned a
gate the bill never reached.

**How it is held.** Not mechanically. The habit is written into the session
brief: before diagnosing a filter, read `scans` and `scan_domains` for the
source in question. Work package WP-6 keeps the counters that make the
answer visible.

---

## PL-004

- title: A cost estimate built from unmeasured multipliers was 20 times too high
- first_seen: 2026-09-01
- last_seen: 2026-09-02
- recurrences: 1
- status: open
- guard: none (WP-6 adds tests/unit/test_scan_manager.py::TestEstimateDefaults::test_estimate_for_all_is_in_the_decade_of_the_last_actual, red against today's config)
- class: a number nobody had measured, presented with two decimals

**The defect.** `ScanManager.estimate_cost` multiplies pages per domain, a
keyword pass rate, a screening pass rate, and tokens per call. Two of those
were assumptions that had never been compared with a run: 20,000 input
tokens per analysis (measured: 3,129) and about 1,010 analyses for a full
scan (measured: 445). The product was $188.46 against an actual of $9.05, and
the range shown, $75 to $471, did not contain the truth. People read the
number as the tool's cost and planned around it.

**Occurrence.** The first completed monthly scan, 1 September 2026, scan
`86463134`, was the first time an actual existed to compare. The estimator's
blend rule already prefers measured rates after two completed scans; there
had been none.

**How it is held.** Not yet. WP-6 sets the static defaults in
`config/pricing.yaml` from the measured run with a provenance comment, shows
the last actual beside every estimate, and adds the guard test named above.
Until it lands, the estimate shown before a scan can be an order of
magnitude high.

---

## PL-005

- title: Unbounded waiter loops ran for hours after their condition became unreachable
- first_seen: 2026-08-28
- last_seen: 2026-08-31
- recurrences: 3
- status: open
- guard: none (an operating rule for agents and sessions, recorded in docs/SESSION_BRIEF.md; nothing in this suite can observe a shell loop)
- class: a check that cannot fail

**The defect.** `until <condition>; do sleep N; done` waits forever when the
thing it waits for is killed, blocked by a gate, or never started. Three such
loops ran for hours during the 2026-08-28 to 08-31 work, one of them for
three days, each burning a background task slot and reporting nothing.

**Occurrence.** Three times in one stretch of work, waiting on a push, a
gate, and a test run respectively. Cost: attention and a direct callout.

**How it is held.** A bounded loop that gives up:
`for i in $(seq 1 60); do <check> && break; sleep 10; done`, checking for the
failure signal as well as the success one. Written into the session brief as
a rule. There is no mechanical enforcement; the harness cannot see shell
loops.

---

## PL-006

- title: A gate file synced mid-edit was committed half-finished in another repo
- first_seen: 2026-08-28
- last_seen: 2026-08-28
- recurrences: 1
- status: mechanized
- guard: none (enforced outside this suite by the Proofmark `ring-stale` gate, which refused the commit; the fix is `python gates/ring.py sync` from the canonical repo, then commit `gates/` here)
- class: two version truths drift

**The defect.** Proofmark distributes gate files from one canonical repo to
every install. A sync run while a canonical file was still being edited
copied the half-finished file into this repo, and this repo then committed
it. The manifest hash in the canonical repo moved on; the install's copy did
not match it.

**Occurrence.** 2026-08-28, during the registrar-decorator change to
`gates/route_handlers.py`. The `ring-stale` gate refused the next commit here
with the fix in its message. Cost: minutes, because the gate caught it. The
lesson is recorded because the same sequence, sync then edit then commit,
is easy to repeat.

**How it is held.** By the gate: `ring-stale` compares the install's gate
files with the canonical manifest on every commit. Finish the edit, then
sync, then commit.
