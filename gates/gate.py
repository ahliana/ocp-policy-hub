"""Proofmark gate runner. One file, four entry points, fail-closed.

Subcommands:
  pre-commit    every check; blocks the commit on any failure (exit 1)
  post-commit   binds evidence to the commit; logs --no-verify overrides
  commit-msg F  never-fix-twice: a "fix" commit must add or change a test;
                also auto-appends a line to the escape log
  report        last 30 days of ledger activity, by gate
  baseline      regenerate the vulture baseline (logged as an exception)
  ship [REF]    the factual half of the end-of-work report: commits, test-floor
                delta, what the gates did, rollback command. Judgment sections
                are left blank on purpose. Defaults to @{u}..HEAD. Writes
                gates/reports/<sha>.md, which pre-push then requires.
  pre-push      refuse a push whose commits have no filled-in report
  scope         what the configured roots cover, and what they miss
  coverage      run the full suite under pytest-cov and print per-module
                statement coverage. Reported fact, never a gate, wired into
                no hook. Nonzero exit only when it could not measure.
                --seed-untested also writes gates/untested_baseline.txt from
                the measured 0% modules; COMMITTING that file arms the
                untested-module gate at pre-push.
  levels        the test mix by size (small/medium/large, enforced) and
                level (unit/integration/e2e, reported), plus the unmarked
                count. Report-only.

Every tool runs as sys.executable -m <tool>, so the interpreter is the
gate venv (.venv-proofmark) that the hook shim invoked - never PATH. Any
unexpected exception blocks the commit: fail-open is L002 living inside
the enforcement layer.

PORTABLE VERSION. The ScreenScribe original hardcoded src/config as the
source roots and tests/ as the test root, which is true of exactly one repo.
Layout now comes from proofmark.toml at the repo root - a separate file, not
a section of pyproject.toml, so installing gates never edits a config the
application already depends on.
"""

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import scope as scope_rules  # gates/ is sys.path[0]; the pure half, shared with install.py

ROOT = Path(__file__).resolve().parent.parent
GATES = ROOT / "gates"
# PATH first, the known Windows install location as fallback. Hardcoding the
# path alone meant a cloned repo on any other machine could not run its own
# hooks - Phase E's whole point is that a clone carries everything it needs.
GIT = shutil.which("git") or r"C:\Program Files\Git\cmd\git.exe"
# The ledger lives IN THE REPO (gates/ledger/, a runtime artifact like
# last_run.log). Until 2026-08-05 it defaulted to an absolute path on one
# machine, shared by six repos: any clone elsewhere silently created a fresh
# empty directory and all history detached with no error, concurrent sessions
# collided on one JSONL, and the canonical tree could never stay clean. The
# pre-split history is frozen in canonical Proofmark's ledger/ directory and
# ledger_entries() still reads it.
LEDGER_DIR = Path(os.environ.get("PROOFMARK_LEDGER_DIR") or (GATES / "ledger"))
LEDGER = LEDGER_DIR / "ledger.jsonl"
ESCAPES = LEDGER_DIR / "escapes.jsonl"
EVIDENCE = GATES / "evidence.json"
LOG = GATES / "last_run.log"
REPORTS = GATES / "reports"


def _config() -> dict:
    """Layout and pins for this repo.

    A missing or malformed proofmark.toml is fatal rather than defaulted. A
    gate that guesses its own source tree can silently scan nothing and report
    green, which is the single failure this system exists to prevent.
    """
    cfg = ROOT / "proofmark.toml"
    if not cfg.exists():
        raise SystemExit(
            f"PROOFMARK: no proofmark.toml at {cfg}. The gate refuses to guess "
            "which directories are source and which are tests."
        )
    data = tomllib.loads(cfg.read_text("utf-8"))["proofmark"]
    for key in ("src_dirs", "tests_dir"):
        if key not in data:
            raise SystemExit(f"PROOFMARK: proofmark.toml is missing '{key}'")
    return data


CONFIG = _config()

# Two interpreters, on purpose. ruff and vulture run from the pinned gate venv
# so their versions are provable and identical everywhere. pytest CANNOT: a real
# test suite imports the application's own dependencies, and the gate venv holds
# four tools. ScreenScribe hid this because its suite is essentially just the
# canary; the first install onto a repo with a real suite failed on `import
# pydub`, which is the correct failure and the reason this split exists.
def venv_python(venv: Path) -> Path:
    """The interpreter inside a venv, either layout. Windows is the proven
    platform; the POSIX branch exists so a stranger's clone is not wrong by
    construction (Episode 4 review - wire.py was Windows-only three ways)."""
    win = venv / "Scripts" / "python.exe"
    return win if win.exists() or os.name == "nt" else venv / "bin" / "python"


APP_VENV = CONFIG.get("app_venv")
if APP_VENV:
    _p = venv_python(ROOT / APP_VENV)
    if not _p.exists():
        raise SystemExit(f"PROOFMARK: app_venv python not found at {_p}")
    PYTEST_PY = str(_p)
else:
    PYTEST_PY = sys.executable

# Where pytest must be invoked from. Reachly's tests import `reachly`, which
# only resolves when pytest runs from backend/ - collecting from the repo root
# dies in conftest with ModuleNotFoundError. A gate that cannot collect is a
# gate that cannot run, so this is configuration rather than an assumption.
PYTEST_CWD_REL = CONFIG.get("pytest_cwd", ".")
PYTEST_CWD = (ROOT / PYTEST_CWD_REL).resolve()

# There is deliberately no time budget here. See test_no_time_budget.py.

SRC_RELS = list(CONFIG["src_dirs"])
# A root may be a single FILE as well as a directory. Real repos put a CLI
# entrypoint at the top level - FinDigger's run.py is 444 lines of dispatch -
# and there is nowhere to put it but the root. scope.py has always allowed it
# (`rel == r or rel.startswith(r + "/")`), so a file root passed the coverage
# check while this line silently dropped it from every scan: covered on paper,
# never actually read. `exists()`, not `is_dir()`, is the whole fix.
SRC_DIRS = [str(ROOT / d) for d in SRC_RELS if (ROOT / d).exists()]


def under_src(path: str) -> bool:
    """Is this repo-relative path inside a declared source root, or one itself?

    Mirrors scope.py's rule exactly. It exists because three separate places
    used a bare `startswith(d + "/")`, which is right for a directory and
    silently false for a file root - so run.py would be scoped in, and then
    skipped by new-source-needs-tests and by the reverse test.
    """
    return any(path == d or path.startswith(d + "/") for d in SRC_RELS)


TESTS_REL = CONFIG["tests_dir"]
TESTS = ROOT / TESTS_REL
# JS/TS test files by runner convention, counted by never-fix-twice wherever
# they live (frontend suites sit outside the Python tests dir).
JS_TEST_SUFFIXES = tuple(
    f".{kind}.{ext}" for kind in ("test", "spec")
    for ext in ("js", "jsx", "ts", "tsx"))
CONFIG_FILES = [
    "proofmark.toml",
    "gates/gate.py",
    "gates/doctor.py",
    "gates/scope.py",
    "gates/check_test_asserts.py",
    "gates/min_test_count.txt",
    "gates/vulture_baseline.txt",
    f"{TESTS_REL}/test_canary.py",
]


def _vulture_exclude() -> list[str]:
    """Exclude flags keeping the test tree out of vulture's call graph.

    Returned separately from the paths because vulture's parser rejects a
    positional path that appears after an option, so every path has to be
    passed before any flag.

    Excluding tests is the whole point of the L003 gate: vulture is scope-blind
    name matching, so a function called ONLY by its own test looks used. Scanning
    src alone is what makes that function report as dead, which is exactly the
    defect - shipped, tested, never wired up.

    ScreenScribe got this for free because tests/ sits beside src/. Tad's tests
    live inside backend/, so without this the test tree would be scanned as
    source and every orphan with a test would be invisible.
    """
    for d in SRC_RELS:
        if TESTS_REL.startswith(d + "/") or TESTS_REL == d:
            return ["--exclude", f"*/{Path(TESTS_REL).name}/*"]
    return []


VULTURE_KEY = re.compile(r"^(.*?):\d+: unused (\w+) '([^']+)'")
_verbose: list[str] = []


def note(text: str) -> None:
    _verbose.append(text)


# A runaway-HANG backstop for the whole-suite pytest runs, and nothing else.
# This is not a time budget (see test_no_time_budget.py - budgets that fire on
# ordinary healthy runs are furniture); it is the line between "slow" and
# "hung", so it must sit an order of magnitude above any honest run, never at
# the mean. The old blanket 600 in run() was placed for quick subprocesses and
# quietly became a suite budget the day a real suite outgrew it: FinDigger at
# 4,121 tests under coverage was refused six times on 2026-08-27, twice on a
# provably quiet machine. Quick calls (git, ruff, collection) keep the 600
# default - a hung `git status` should still die fast.
SUITE_HANG_BACKSTOP = 3600


def run(args: list[str], cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 600) -> subprocess.CompletedProcess:
    # env=None inherits, which is what every caller but the reverse test wants.
    # encoding pinned: text=True alone decodes with the ANSI code page on
    # Windows (cp1252), which corrupted non-ASCII test content on the reverse
    # test's round-trip and could hide a non-ASCII filename from every path
    # match (Episode 4 review, m1). wire.py already pinned utf-8; this file
    # never had.
    p = subprocess.run(args, cwd=cwd or ROOT, capture_output=True, text=True, timeout=timeout,
                       env=env, encoding="utf-8", errors="replace")
    note(f"$ {' '.join(str(a) for a in args)}\n[exit {p.returncode}]\n{p.stdout}\n{p.stderr}")
    return p


def git(*args: str) -> subprocess.CompletedProcess:
    return run([GIT, *args])


def ledger_line(kind: str, gate: str, detail: str) -> None:
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "repo": ROOT.name, "kind": kind, "gate": gate, "detail": detail[:400],
        }) + "\n")


def fail(gate: str, summary: str) -> None:
    LOG.write_text("\n".join(_verbose), encoding="utf-8")
    ledger_line("block", gate, summary)
    print(f"PROOFMARK BLOCK [{gate}] {summary}")
    print(f"  full output: {LOG}")
    print("  break-glass (you, not the agent): git commit --no-verify  (logged)")
    sys.exit(1)


def head_file(relpath: str) -> str | None:
    p = git("show", f"HEAD:{relpath}")
    return p.stdout if p.returncode == 0 else None


def vulture_keys(text: str) -> set[str]:
    out = set()
    root = (str(ROOT) + "\\").replace("/", "\\").lower()
    for line in text.splitlines():
        m = VULTURE_KEY.match(line.replace("/", "\\").strip())
        if m:
            path = m.group(1)
            if path.lower().startswith(root):
                path = path[len(root):]
            out.add(f"{path}::{m.group(2)}::{m.group(3)}")
    return out


# ---------------------------------------------------------------- checks --

def check_doctor() -> None:
    p = run([sys.executable, str(GATES / "doctor.py")])
    if p.returncode != 0:
        fail("doctor", "toolchain does not match pins; see gates/last_run.log")


def check_layout() -> None:
    """The configured source and test roots must actually exist.

    Without this, renaming a package directory turns every scan below into a
    scan of nothing and the whole suite reports green over an empty tree.
    """
    missing = [d for d in SRC_RELS if not (ROOT / d).exists()]
    if missing:
        fail("layout", f"proofmark.toml lists source dirs that do not exist: {missing}")
    if not TESTS.is_dir():
        fail("layout", f"proofmark.toml tests_dir does not exist: {TESTS_REL}")


def _tracked_files() -> list[str]:
    p = git("ls-files", "--cached")
    files = [ln for ln in p.stdout.splitlines() if ln.strip()]
    if not files:
        fail("scope", "git ls-files returned nothing; a coverage check that sees "
                      "no files cannot prove coverage of anything")
    return files


def _excludes() -> dict[str, str]:
    try:
        return scope_rules.validate_excludes(CONFIG.get("exclude"))
    except ValueError as exc:
        raise SystemExit(f"PROOFMARK: proofmark.toml: {exc}") from exc


def check_scope_complete() -> None:
    """The configured roots must cover every Python file in the repo.

    check_layout above proves those directories EXIST. This proves they are
    COMPLETE. Both failures report green, which is why the first one being
    closed did not close the second: see gates/scope.py for the measurement
    across four installs that showed the hole was live rather than theoretical.

    Blocks on Python only. Non-Python source is inventoried by `gate.py scope`
    and never blocks here - Phase 1 is a Python toolchain (ruff, vulture,
    pytest), so a repo with a frontend would be told on every single commit
    about a thing no gate is equipped to check. That is the prose-shaped
    mechanism this whole system replaces, and the kill rule would delete it
    inside a fortnight. Report it where someone is asking; do not narrate it.
    """
    uncovered, stale, empty_roots, _ = scope_rules.findings(
        _tracked_files(), SRC_RELS, TESTS_REL, _excludes()
    )
    if uncovered:
        fail("scope", scope_rules.uncovered_message(uncovered, SRC_RELS, TESTS_REL))
    if empty_roots:
        fail("scope-empty-root",
             f"declared root(s) holding no tracked file: {empty_roots}. check_layout "
             "only proves a directory exists on disk, which a gitignored runtime "
             "folder also does. Either the path is wrong, or it is not source and "
             "belongs out of src_dirs - a root that covers nothing reads as coverage.")
    if stale:
        fail("scope-stale-exclusion",
             f"exclusions in proofmark.toml that match no file: {stale}. "
             "Either the path moved and the carve-out is now hiding nothing, or it "
             "outlived its reason. Delete the line. A carve-out list that is not "
             "true reads as a live reason to skip something.")


# Canonical is the repo that carries ring.toml - the source of distribution.
# An install names its canonical in proofmark.toml (`canonical = "<path>"`,
# written by the ring install); a clone on a machine with no canonical simply
# has no such path that exists, and the ring checks pass quietly per the
# absent-manifest philosophy documented on check_ring_current. Deriving this
# from the ledger path died with the shared ledger.
IS_CANONICAL = (GATES / "ring.toml").exists()
_canon_cfg = CONFIG.get("canonical")
CANON_GATES = GATES if IS_CANONICAL else (
    Path(_canon_cfg) / "gates" if _canon_cfg else None)
RING_MANIFEST = CANON_GATES / "ring-manifest.json" if CANON_GATES else None


def check_ring_current() -> None:
    """This repo must be running the gate the ring publishes.

    Six repos each hold their own copy of these files, because a git hook cannot
    import from a directory that may not exist. Copies drift, and drift is
    invisible exactly when it matters: a repo silently running last week's gate
    reports green under last week's rules. Same shape as a scan that covers part
    of a tree, which is what check_scope_complete exists for - this is that
    failure applied to the gate itself.

    Absent manifest means "not part of a managed ring", which is a legitimate
    state - someone copies gates/ to a new machine, or Proofmark is not on this
    disk - so it passes quietly. That is the one place fail-open is right here,
    because the manifest is distribution infrastructure and not a defect gate. A
    manifest that EXISTS and disagrees is a different thing entirely, and blocks.
    """
    if IS_CANONICAL or RING_MANIFEST is None or not RING_MANIFEST.exists():
        return
    try:
        wanted = json.loads(RING_MANIFEST.read_text("utf-8"))["files"]
    except (OSError, ValueError, KeyError) as exc:
        fail("ring-stale", f"ring manifest at {RING_MANIFEST} is unreadable: {exc!r}. "
                           "A distribution check that cannot read its source must "
                           "not pass quietly.")
        return
    import hashlib
    behind = []
    for name, digest in sorted(wanted.items()):
        local = GATES / name
        if not local.exists():
            behind.append(f"{name} (missing)")
        elif hashlib.sha256(local.read_bytes()).hexdigest() != digest:
            behind.append(name)
    if behind:
        fail("ring-stale",
             f"this repo is running a gate the ring has moved past: {behind}. "
             f"It would report green under superseded rules. Fix from the "
             f"canonical repo:\n"
             f"    python gates/ring.py sync\n"
             f"  then commit gates/ here. Manifest: {RING_MANIFEST}")


def check_ring_manifest_current() -> None:
    """In the canonical repo, a distributed gate file cannot be committed
    without the manifest that describes it.

    check_ring_current is the receiving end: each install compares its own
    gates/ against the manifest and blocks if it is behind. That only works
    while the manifest actually describes canonical. Edit gate.py here, commit
    without running `ring.py sync`, and nothing anywhere notices: the five
    installs still hold the OLD gate.py, which still matches the OLD manifest,
    so every repo passes its ring check while the ring is split in two. The
    detector for drift is defeated by the drift being complete.

    It was not hypothetical - the manifest was left behind twice on 2026-08-04,
    both times caught by someone reading `git status`, and the report for
    1c29bf2 filed "nothing forces the manifest to be committed alongside the
    gate.py change it describes" as its first open question.

    Only fires in the canonical repo. An install has no ring.py and no business
    rewriting the manifest, which is why the import sits below the guard.
    """
    if not IS_CANONICAL:
        return
    staged = set(git("diff", "--cached", "--name-only").stdout.splitlines())

    import ring                                  # canonical repo only

    changed = sorted(f for f in staged
                     if f.startswith("gates/") and Path(f).name in ring.DISTRIBUTED)
    if not changed:
        return
    fix = ("    python gates/ring.py sync\n"
           "  then stage gates/ring-manifest.json with this commit.")
    if not RING_MANIFEST.exists():
        fail("ring-manifest", f"{changed} staged with no ring manifest at all.\n{fix}")
    import hashlib
    try:
        wanted = json.loads(RING_MANIFEST.read_text("utf-8"))["files"]
    except (OSError, ValueError, KeyError) as exc:
        fail("ring-manifest", f"the ring manifest is unreadable: {exc!r}.\n{fix}")
        return
    stale = [n for n in ring.DISTRIBUTED
             if (GATES / n).exists()
             and wanted.get(n) != hashlib.sha256((GATES / n).read_bytes()).hexdigest()]
    if stale:
        fail("ring-manifest",
             f"the manifest does not describe {stale}. Committing this would "
             f"leave five installs holding the previous gate AND passing their "
             f"own ring check, because the old copies still match the old "
             f"manifest.\n{fix}")
    if "gates/ring-manifest.json" not in staged:
        fail("ring-manifest",
             f"{changed} staged but gates/ring-manifest.json is not. The "
             f"manifest on disk is correct, so this is one `git add` away - "
             f"and without it the commit publishes a gate the manifest does "
             f"not name.\n{fix}")


def check_staged_matches_worktree() -> None:
    p = git("status", "--porcelain")
    both = [ln for ln in p.stdout.splitlines() if len(ln) >= 2 and ln[0] not in " ?" and ln[1] not in " ?"]
    if both:
        fail("toctou", f"staged content differs from worktree (gate would check bytes "
                       f"that are not being committed): {both[:5]}")


def swallow_keys(payload: str) -> dict[str, int] | None:
    """Ruff findings keyed by file + rule + ENCLOSING FUNCTION, with counts.
    None when the payload is not JSON - the caller must block, not shrug.

    Line numbers are useless as identity: moving a function rewrites every key
    below it and the baseline churns into noise. Vulture's baseline is stable
    because its keys are `path::type::name`, so this uses the same shape and
    resolves each finding's row to the function that contains it.

    Counts, not a bare set, because a key alone would let a second swallow be
    added to a function that already has one - growth hidden inside an entry
    that is already forgiven. That is the failure mode of every grandfathered
    list: it stops measuring the thing it was created to bound.
    """
    try:
        findings = json.loads(payload or "[]")
    except json.JSONDecodeError:
        # None, not {}: an unreadable scanner output must be distinguishable
        # from a clean scan. Returning {} here disabled the L002 gate ring-wide
        # on any garbled ruff stdout with a plausible exit code - fail-open
        # living inside the enforcement layer (Episode 4 review, M7;
        # re-decided 2026-08-06, reversing the behavior a test had pinned).
        return None
    out: dict[str, int] = {}
    cache: dict[str, list[tuple[int, int, str]]] = {}
    for f in findings:
        path = Path(f["filename"])
        try:
            rel = path.resolve().relative_to(ROOT).as_posix()
        except ValueError:
            rel = path.as_posix()
        if rel not in cache:
            cache[rel] = _function_spans(path)
        row = (f.get("location") or {}).get("row", 0)
        where = next((name for lo, hi, name in cache[rel] if lo <= row <= hi), "<module>")
        out[f"{rel}::{f['code']}::{where}"] = out.get(f"{rel}::{f['code']}::{where}", 0) + 1
    return out


def _function_spans(path: Path) -> list[tuple[int, int, str]]:
    """(start, end, dotted name) for every function, innermost first."""
    try:
        tree = ast.parse(path.read_text("utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return []
    spans: list[tuple[int, int, str]] = []

    def walk(node, prefix=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                if not isinstance(child, ast.ClassDef):
                    spans.append((child.lineno, child.end_lineno, name))
                walk(child, name + ".")
            else:
                walk(child, prefix)

    walk(tree)
    spans.sort(key=lambda s: s[1] - s[0])        # innermost (shortest span) wins
    return spans


def format_swallow_baseline(keys: dict[str, int]) -> str:
    return "".join(f"{k}\t{n}\n" for k, n in sorted(keys.items()))


def parse_swallow_baseline(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        key, _, count = line.rpartition("\t")
        out[key] = int(count) if count.strip().isdigit() else 1
    return out


def swallow_regressions(found: dict[str, int], baseline: dict[str, int]) -> list[str]:
    """New swallow sites, or more of them where some were already forgiven.

    A renamed function reads as a new site here, which is correct but blunt: the
    debt did not grow, it moved. `stale_swallow_entries` names the other side of
    that same move so the message can say so.
    """
    out = []
    for key, n in sorted(found.items()):
        was = baseline.get(key, 0)
        if n > was:
            out.append(key if was == 0 else f"{key} ({was} -> {n})")
    return out


def stale_swallow_entries(found: dict[str, int], baseline: dict[str, int]) -> list[str]:
    """Baseline entries whose finding no longer exists. Pure, so it is tested.

    Two ways this happens and they need opposite responses.

    Someone FIXED the swallow: the entry is now a standing pre-forgiveness for a
    future swallow in that function - the exact failure mode of every
    grandfathered list, quietly ceasing to bound the thing it was created to
    bound. It should be dropped so the debt actually shrinks.

    Or someone RENAMED the function: the old key goes stale and a new one
    appears as a regression, so a legitimate refactor blocks. Reporting both
    sides together is what makes that diagnosable instead of baffling.
    """
    return sorted(key for key in baseline if key not in found)


def rename_pairs(new_keys: list[str], stale: list[str]) -> list[str]:
    """Guess which new sites are renames of stale ones: same file, same rule.

    A guess, and labelled as one in the output. It cannot be certain - two
    functions in one file can hold the same rule - but "you renamed something"
    is the right first question when a refactor blocks, and the alternative is
    the person staring at a key that says nothing about why it is new.
    """
    def where(key):
        parts = key.split("::")
        return (parts[0], parts[1]) if len(parts) >= 2 else (key, "")

    stale_by_place: dict[tuple, list[str]] = {}
    for key in stale:
        stale_by_place.setdefault(where(key), []).append(key)
    out = []
    for key in new_keys:
        for old in stale_by_place.get(where(key.split(" (")[0]), []):
            out.append(f"{old} -> {key}")
    return out


def check_ruff() -> None:
    """L002, as a ratchet rather than an absolute.

    It was absolute, which is right for a repo gated from its first commit and
    wrong for one adopted later. FinDigger arrived with 125 findings - 24 real
    try/except/pass and 101 blind catches, most of which do record health and
    continue, which is the correct shape for a per-item batch fetcher. The gate
    blocked its own installation.

    Three ways that ends, and only one is honest. Fix all 125 now: days of work
    and a hundred judgement calls about someone else's error semantics. Blanket
    `# noqa`: the same debt, scattered where nobody can count it. Or grandfather
    it and block growth - which is exactly what the vulture baseline already
    does for L003, and this now mirrors it, including the second half that
    matters more than the first: the baseline itself may only shrink.
    """
    if not SRC_DIRS:
        return
    p = run([sys.executable, "-m", "ruff", "check", "--no-cache", "--isolated",
             "--ignore-noqa",
             "--select", "E722,S110,BLE001", "--output-format", "json", *SRC_DIRS])
    if p.returncode not in (0, 1):
        fail("ruff-swallow", f"ruff did not run (exit {p.returncode}); "
                             "a gate that cannot run fails red")
    found = swallow_keys(p.stdout)
    if found is None:
        fail("ruff-swallow", "ruff output was not parseable JSON; a gate that "
                             "cannot read its own scanner must fail red, not "
                             "report a clean scan")
    baseline_file = GATES / "swallow_baseline.txt"
    baseline = (parse_swallow_baseline(baseline_file.read_text("utf-8"))
                if baseline_file.exists() else {})
    new = swallow_regressions(found, baseline)
    stale = stale_swallow_entries(found, baseline)
    if new:
        renames = rename_pairs(new, stale)
        if renames:
            print("PROOFMARK: these look like renames rather than new debt "
                  "(same file, same rule):")
            for line in renames[:5]:
                print(f"    {line}")
            print("  If so, regenerate: .venv-proofmark\\Scripts\\python.exe "
                  "gates\\gate.py baseline")
        fail("ruff-swallow", f"exception swallowing (L002), not in baseline: {new[:5]}")
    if stale:
        # Not a block. A stale entry cannot let anything bad through today - the
        # finding it forgave is gone - but it WILL pre-forgive the next swallow
        # written into that function, which is how a grandfathered list stops
        # bounding anything. Saying so is enough; blocking would punish the
        # commit that did the fixing.
        print(f"PROOFMARK: {len(stale)} swallow baseline entr"
              f"{'y is' if len(stale) == 1 else 'ies are'} stale - the finding is "
              f"gone: {stale[:3]}")
        print("  Regenerate to bank it: .venv-proofmark\\Scripts\\python.exe "
              "gates\\gate.py baseline")
        ledger_line("admin", "swallow-stale", f"{len(stale)} stale entries: {stale[:5]}")
    # == "1", not truthiness: PROOFMARK_ACCEPT_DEBT=0 meant "on" here while the
    # untested-module gate read it as off - one variable, two grammars
    # (Episode 4 review, m2).
    head = head_file("gates/swallow_baseline.txt")
    if head is not None and os.environ.get("PROOFMARK_ACCEPT_DEBT") != "1":
        prev = parse_swallow_baseline(head)
        grown = swallow_regressions(baseline, prev)
        if grown:
            fail("swallow-ratchet",
                 f"baseline grew vs HEAD (debt can only shrink): {grown[:5]}")
    if os.environ.get("PROOFMARK_ACCEPT_DEBT") == "1":
        ledger_line("exception", "swallow-ratchet", "PROOFMARK_ACCEPT_DEBT=1")


def _whitelist_growth_is_generator_argued() -> bool:
    """True when gates/vulture_whitelist.py is byte-identical to what the
    generator produces for this repo right now.

    The whitelist ratchet exists because a whitelist entry is forgiven dead
    code (Episode 4, M8). But a name the GENERATOR emits is not an argument a
    human is smuggling past the ratchet - it is a route, an enum member, or a
    framework override the scan proved, and the commit that adds it carries
    the generator's own reasoning (WP-450: FinDigger's HTMLParser overrides
    could only land as ACCEPT_DEBT baseline growth, the wrong bucket). So
    growth passes exactly when --check reproduces the file: one hand-added
    line anywhere and the whole file stops matching, which closes the
    ride-along door. Absent generator (an install running a pre-2026-08-10
    ring copy) means no proof, and the ratchet blocks as before.
    """
    gen = GATES / "route_handlers.py"
    if not gen.exists():
        return False
    p = run([sys.executable, str(gen), str(ROOT), *SRC_RELS, "--check"])
    return p.returncode == 0


def check_vulture() -> None:
    if not SRC_DIRS:
        return
    wl = GATES / "vulture_whitelist.py"
    paths = SRC_DIRS + ([str(wl)] if wl.exists() else [])
    p = run([sys.executable, "-m", "vulture", *paths, *_vulture_exclude(),
             "--min-confidence", "60"])
    if p.returncode not in (0, 3):  # 0 = clean, 3 = findings; anything else = broken
        fail("vulture-orphan", f"vulture did not run (exit {p.returncode}); "
                               "a gate that cannot run fails red")
    found = vulture_keys(p.stdout)
    baseline_file = GATES / "vulture_baseline.txt"
    baseline = set(baseline_file.read_text("utf-8").splitlines()) if baseline_file.exists() else set()
    new = found - baseline
    if new:
        fail("vulture-orphan", f"unwired code (L003), not in baseline: {sorted(new)[:5]}")
    head = head_file("gates/vulture_baseline.txt")
    if head is not None and os.environ.get("PROOFMARK_ACCEPT_DEBT") != "1":
        grown = baseline - set(head.splitlines())
        if grown:
            fail("vulture-ratchet", f"baseline grew vs HEAD (debt can only shrink): {sorted(grown)[:5]}")
    # The whitelist is a baseline wearing a .py extension: any name in it reads
    # as "used" to vulture, so growth there is dead-code debt growing with no
    # log and no ratchet - the side door the Episode 4 review named (M8).
    wl_head = head_file("gates/vulture_whitelist.py")
    if wl.exists() and wl_head is not None \
            and os.environ.get("PROOFMARK_ACCEPT_DEBT") != "1":
        wl_lines = {ln.strip() for ln in wl.read_text("utf-8").splitlines()
                    if ln.strip() and not ln.strip().startswith("#")}
        head_lines = {ln.strip() for ln in wl_head.splitlines()
                      if ln.strip() and not ln.strip().startswith("#")}
        grown_wl = wl_lines - head_lines
        if grown_wl and _whitelist_growth_is_generator_argued():
            ledger_line("admin", "vulture-whitelist",
                        f"whitelist grew {len(grown_wl)} lines, generator-argued: "
                        f"route_handlers.py --check reproduces the file exactly")
        elif grown_wl:
            fail("vulture-ratchet",
                 f"gates/vulture_whitelist.py grew vs HEAD (a whitelist entry is "
                 f"forgiven dead code, and debt can only shrink): {sorted(grown_wl)[:5]}. "
                 f"Generator-produced growth passes on its own: regenerate with "
                 f"gates/route_handlers.py so --check reproduces the file. "
                 f"Logged escape for anything else: PROOFMARK_ACCEPT_DEBT=1")
    if os.environ.get("PROOFMARK_ACCEPT_DEBT") == "1":
        ledger_line("exception", "vulture-ratchet", "PROOFMARK_ACCEPT_DEBT=1")


def check_asserts() -> None:
    p = run([sys.executable, str(GATES / "check_test_asserts.py"), str(TESTS)])
    if p.returncode != 0:
        first = p.stdout.splitlines()[1] if len(p.stdout.splitlines()) > 1 else ""
        fail("assert-quality", f"assert-free test: {first}")


def check_canary() -> None:
    # `-o addopts=` and `-p no:warnings` are load-bearing, not tidiness.
    # This check demands the literal string "1 passed, 1 xfailed", and pytest
    # omits that summary line in two situations that have nothing to do with the
    # canary: when warnings are present, and when the repo's own addopts already
    # contain -q, so the gate's -q makes it -qq (extra quiet). VoxCast is
    # configured that way, which meant the canary could never pass there and the
    # gate would have been disabled as broken. Neutralising the repo's addopts
    # for this one two-test run makes the output shape independent of the
    # target repo. The string requirement stays exactly as strict.
    p = run([PYTEST_PY, "-m", "pytest", str(TESTS / "test_canary.py"), "-q",
             "-o", "addopts=", "-p", "no:cacheprovider", "-p", "no:warnings"],
            cwd=PYTEST_CWD)
    if p.returncode != 0 or "1 passed, 1 xfailed" not in p.stdout:
        fail("canary", "canary pair absent or wrong: a run that cannot show a "
                       "failure proves nothing")


def collected_count(stdout: str) -> int | None:
    """Number of tests pytest collected, in either output shape.

    `pytest --co -q` normally ends with "N tests collected". When the run emits
    warnings it prints only the per-file "path.py: N" lines and drops that
    summary, so a regex for the summary alone finds nothing on any repo with a
    deprecation warning - which is most of them. Summing the per-file lines is
    the same number.
    """
    m = re.search(r"(\d+) tests? collected", stdout)
    if m:
        return int(m.group(1))
    per_file = re.findall(r"^\S+\.py: (\d+)$", stdout, re.MULTILINE)
    if per_file:
        return sum(int(n) for n in per_file)
    return None


def untracked_test_files() -> list[str]:
    """Test files pytest will collect that git has never heard of.

    Not committed and not staged - a scratch file. `git ls-files --others` with
    --exclude-standard is exactly this set, and it excludes anything gitignored.
    """
    p = git("ls-files", "--others", "--exclude-standard", TESTS_REL)
    return [ln for ln in p.stdout.splitlines()
            if ln.strip().endswith(".py") and Path(ln).name != "conftest.py"]


def should_ratchet(count: int, floor: int, untracked: list[str]) -> bool:
    """Raise the floor only when every counted test is in the commit. Pure.

    Collection counts whatever is on disk, so a scratch test file inflates the
    floor for a commit that does not contain it - and deleting the scratch file
    afterwards then blocks the NEXT commit against a number nothing can satisfy.
    I did this to myself measuring per-test overhead: 200 throwaway tests, and
    the floor would have ratcheted to include them.

    Deliberately not a block. Writing a test before staging it is normal, and a
    gate that refuses that would be wrong far more often than it was right. It
    just declines to bank a number it cannot back.
    """
    return count > floor and not untracked


def check_collection_floor() -> None:
    p = run([PYTEST_PY, "-m", "pytest", str(TESTS), "--co", "-q",
             "-p", "no:cacheprovider"], cwd=PYTEST_CWD)
    if p.returncode == 5:
        fail("collect", "pytest exit 5: NO TESTS COLLECTED - empty green is not green")
    count = collected_count(p.stdout)
    if count is None:
        fail("collect", "could not read collected-test count from pytest output")
    floor_file = GATES / "min_test_count.txt"
    floor = int(floor_file.read_text("utf-8").strip())
    head = head_file("gates/min_test_count.txt")
    if head is not None and floor < int(head.strip()):
        fail("collect-floor", f"floor lowered {head.strip()} -> {floor}; the floor only goes up")
    if count < floor:
        fail("collect-floor", f"{count} tests collected < committed floor {floor}")
    stray = untracked_test_files()
    if count > floor and stray:
        print(f"PROOFMARK: floor left at {floor} - {len(stray)} untracked test file(s) "
              f"are in the count of {count}: {stray[:3]}")
        print("  Stage them and the floor rises with the commit that contains them.")
        note(f"floor not ratcheted; untracked tests counted: {stray}")
    if should_ratchet(count, floor, stray):
        # RATCHET, not a note. Printing "you could raise the floor" relies on
        # someone reading gate output and then doing something about it, which
        # is the same prose-shaped mechanism this whole system exists to
        # replace. Written and staged here so the higher floor lands in THIS
        # commit; otherwise a test added today can be silently deleted tomorrow
        # and still clear yesterday's stale floor.
        floor_file.write_text(f"{count}\n", encoding="utf-8", newline="\n")
        git("add", "gates/min_test_count.txt")
        note(f"floor ratcheted {floor} -> {count}")
        print(f"PROOFMARK: test floor raised {floor} -> {count} (staged into this commit)")


def filtered_collected_count(stdout: str) -> int | None:
    """Selected count from a marker-filtered collection.

    Filtered runs print "5/212 tests collected (207 deselected)" - the selected
    count is the FIRST number. collected_count() serves the unfiltered shape
    and reads 212 there, which made the first `levels` run report every marker
    as universal.
    """
    m = re.search(r"(\d+)(?:/\d+)? tests? collected", stdout)
    return int(m.group(1)) if m else collected_count(stdout)


def check_unmarked_ratchet() -> None:
    """New tests must declare a size; the unmarked count may only shrink.

    `new-source-needs-tests` makes change grow tests; this makes growth carry a
    size claim. Existing unmarked tests are grandfathered at the count in
    gates/unmarked_baseline.txt - created automatically the first time this
    gate runs, lowered automatically as labels appear, and a commit that would
    RAISE it is refused: the new test needs one honest small/medium/large
    marker. Mass-labelling stays banned; this only ever asks for a label on
    the test being added right now.
    """
    p = run([PYTEST_PY, "-m", "pytest", str(TESTS), "--co", "-q",
             "-m", "not small and not medium and not large",
             "-p", "no:cacheprovider"], cwd=PYTEST_CWD)
    count = 0 if p.returncode == 5 else filtered_collected_count(p.stdout)
    if count is None:
        fail("unmarked-ratchet", "could not read the unmarked-test count from pytest output")
    base_file = GATES / "unmarked_baseline.txt"
    head = head_file("gates/unmarked_baseline.txt")
    if not base_file.exists():
        # Auto-create is for a repo meeting the gate for the FIRST time. If the
        # file exists at HEAD, this is not a first run - it is a deletion, and
        # recreating the baseline at the current count would re-grandfather
        # every unmarked test in one unlogged move (Episode 4 review, M4).
        if head is not None:
            if os.environ.get("PROOFMARK_ACCEPT_DEBT") == "1":
                ledger_line("exception", "unmarked-ratchet",
                            "baseline file deleted; reset accepted via PROOFMARK_ACCEPT_DEBT")
            else:
                fail("unmarked-ratchet",
                     "gates/unmarked_baseline.txt exists at HEAD but is missing here: "
                     "deleting the baseline re-grandfathers every unmarked test at "
                     "the current count. Restore the file; the ratchet only goes "
                     "down. Logged escape: PROOFMARK_ACCEPT_DEBT=1")
        base_file.write_text(f"{count}\n", encoding="utf-8", newline="\n")
        git("add", "gates/unmarked_baseline.txt")
        ledger_line("admin", "unmarked-baseline", f"baseline created: {count} unmarked tests")
        print(f"PROOFMARK: unmarked-test baseline created at {count} (staged into this commit)")
        return
    base = int(base_file.read_text("utf-8").strip())
    if head is not None and base > int(head.strip()):
        fail("unmarked-ratchet",
             f"baseline raised {head.strip()} -> {base}; it only goes down")
    if count > base:
        stray = untracked_test_files()
        hint = (f" (untracked test file(s) in the count: {stray[:3]})" if stray else "")
        fail("unmarked-ratchet",
             f"{count} unmarked tests > baseline {base}: a new test must carry a "
             f"size marker - small (hermetic, enforced), medium (localhost and "
             f"tmp_path), or large{hint}")
    if count < base:
        base_file.write_text(f"{count}\n", encoding="utf-8", newline="\n")
        git("add", "gates/unmarked_baseline.txt")
        note(f"unmarked baseline ratcheted {base} -> {count}")
        print(f"PROOFMARK: unmarked-test baseline lowered {base} -> {count} "
              f"(staged into this commit)")


def check_e2e_floor() -> None:
    """gates/e2e_floor.txt: the e2e-marked test count only goes up.

    A repo that serves HTTP adopts this by committing the floor file; a repo
    without the file has not declared a surface and is exempt (stated by
    absence of the file in gates/, not silent). The floor must be at least 1 -
    an adopted surface with zero end-to-end tests is the Reachly condition
    this gate exists to prevent: a live web application whose suite never
    once drives it the way a user reaches it. Budget per research-brief.md:33:
    one in-repo test plus the post-deploy probe, not a browser suite.
    """
    floor_file = GATES / "e2e_floor.txt"
    head = head_file("gates/e2e_floor.txt")
    if not floor_file.exists():
        # Absent AND absent at HEAD means "no surface declared" - a statement.
        # Absent while HEAD has it is a deletion: de-adopting the floor is a
        # decision, not a file removal (Episode 4 review, M5).
        if head is not None:
            if os.environ.get("PROOFMARK_ACCEPT_DEBT") == "1":
                ledger_line("exception", "e2e-floor",
                            "adopted floor file deleted; de-adoption accepted via "
                            "PROOFMARK_ACCEPT_DEBT")
            else:
                fail("e2e-floor",
                     "gates/e2e_floor.txt exists at HEAD but is missing here: "
                     "deleting the floor file silently de-adopts the e2e gate. "
                     "Restore it, or de-adopt deliberately: PROOFMARK_ACCEPT_DEBT=1 "
                     "(logged)")
        return
    floor = int(floor_file.read_text("utf-8").strip())
    if floor < 1:
        fail("e2e-floor", "the committed e2e floor is below 1: adopting the "
                          "floor means having at least one end-to-end test")
    if head is not None and floor < int(head.strip()):
        fail("e2e-floor", f"floor lowered {head.strip()} -> {floor}; "
                          f"the e2e floor only goes up")
    p = run([PYTEST_PY, "-m", "pytest", str(TESTS), "--co", "-q",
             "-m", "e2e", "-p", "no:cacheprovider"], cwd=PYTEST_CWD)
    count = 0 if p.returncode == 5 else filtered_collected_count(p.stdout)
    if count is None:
        fail("e2e-floor", "could not read the e2e-marked test count")
    if count < floor:
        fail("e2e-floor",
             f"{count} test(s) marked e2e < committed floor {floor}: an "
             f"end-to-end test was deleted or unmarked, and the floor only "
             f"goes up")
    stray = untracked_test_files()
    if should_ratchet(count, floor, stray):
        floor_file.write_text(f"{count}\n", encoding="utf-8", newline="\n")
        git("add", "gates/e2e_floor.txt")
        note(f"e2e floor ratcheted {floor} -> {count}")
        print(f"PROOFMARK: e2e floor raised {floor} -> {count} "
              f"(staged into this commit)")
    elif count > floor:
        # Same guard collect-floor has had all along: never bank a number an
        # untracked scratch file inflated - deleting the file later would block
        # every commit against a floor nothing can satisfy.
        print(f"PROOFMARK: e2e floor left at {floor} - untracked test file(s) "
              f"are in the count of {count}: {stray[:3]}")
        note(f"e2e floor not ratcheted; untracked tests counted: {stray}")


def pytest_failure_tail(stdout: str) -> str:
    """The informative lines of a failing pytest run. Pure.

    Not blindly the last three lines: whenever warnings exist those are the
    warnings summary, and the failure scrolls out of the block message. And
    not the short-summary FAILED lines alone: pytest truncates those to the
    console width, so a long test id leaves "- T..." where the exception name
    should be. The "E " lines of the FAILURES section carry the reason in
    full, so both are quoted. Found by verify_install's hardened socket
    check: the refusal was real and the message quoted "2 warnings in 0.05s"
    instead of SocketBlockedError.
    """
    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    failed = [ln for ln in lines if ln.startswith(("FAILED", "ERROR"))][:3]
    reasons = [ln for ln in lines if ln.startswith("E ")][:3]
    count = lines[-1:] if lines else []
    if failed or reasons:
        return "; ".join(failed + reasons + count)
    return "; ".join(lines[-3:])


def check_tests() -> None:
    """Pre-commit: the fast suite. `large` is the pressure valve for big repos.

    A commit-time gate that takes a minute gets skipped, and a skipped gate dies
    to the kill rule - so tests marked `large` sit out this run. That valve is
    only safe because check_tests_full() runs them at push; see the warning
    there before touching either.
    """
    p = run([PYTEST_PY, "-m", "pytest", str(TESTS), "-q", "-m", "not large",
             "-p", "no:cacheprovider"], cwd=PYTEST_CWD, timeout=SUITE_HANG_BACKSTOP)
    if p.returncode == 5:
        fail("pytest", "pytest exit 5: no tests collected")
    if p.returncode != 0:
        fail("pytest", f"tests failed: {pytest_failure_tail(p.stdout)}")


def check_tests_full() -> None:
    """Pre-push: every test, `large` included. Closes the marker's escape hatch.

    Until 2026-08-04 `-m "not large"` appeared exactly once in this file, in the
    pre-commit run, and no gate anywhere ran the excluded tests. The floor
    COUNTS them - collection has no marker filter - so a slow test could be
    marked `large` and would then be tallied forever while never executing
    again. Green, floor satisfied, and silently not run: a vacuous pass at suite
    level, and precisely the move a big repo makes when its suite gets slow.

    Splitting fast/full is the right answer for a large application. It is only
    honest if the full half actually happens somewhere, and push is the last
    boundary before the code leaves this machine.
    """
    base_file = GATES / "untested_baseline.txt"
    measured = base_file.exists()
    if measured and run([PYTEST_PY, "-c", "import pytest_cov"]).returncode != 0:
        # The baseline is a commitment to measure. A push that skips the
        # measurement because the tool vanished is fail-open with extra steps.
        print("PROOFMARK BLOCK [untested-module] gates/untested_baseline.txt "
              "exists but pytest-cov is not installed in the suite venv - the "
              "gate cannot measure, and an unmeasured pass is not a pass")
        print(f"  install: \"{PYTEST_PY}\" -m pip install pytest-cov")
        ledger_line("block", "untested-module", "pytest-cov missing at push")
        sys.exit(1)
    if measured:
        p = cov_run()
    else:
        p = run([PYTEST_PY, "-m", "pytest", str(TESTS), "-q",
                 "-p", "no:cacheprovider"], cwd=PYTEST_CWD, timeout=SUITE_HANG_BACKSTOP)
    if p.returncode == 5:
        print("PROOFMARK BLOCK [pytest-full] pytest exit 5: no tests collected")
        ledger_line("block", "pytest-full", "exit 5: no tests collected at push")
        sys.exit(1)
    if p.returncode != 0:
        tail = pytest_failure_tail(p.stdout)
        print(f"PROOFMARK BLOCK [pytest-full] the full suite fails: {tail}")
        print("  These are the tests pre-commit skips (marked `large`).")
        print("  Break-glass (you, not the agent): git push --no-verify  (logged)")
        ledger_line("block", "pytest-full", f"full suite failed at push: {tail}")
        sys.exit(1)
    ledger_line("pass", "pytest-full", "full suite green at push, `large` included")
    if not measured:
        return
    persist_coverage_total(p.stdout)
    bad = coverage_parse_failure(p.stdout, parse_coverage_rows(p.stdout))
    if bad:
        print(f"PROOFMARK BLOCK [untested-module] {bad}. An adopted baseline is "
              f"a commitment to measure; an unparseable measurement is not one.")
        ledger_line("block", "untested-module", bad)
        sys.exit(1)
    zeros = zero_covered_modules(p.stdout)
    base = set(base_file.read_text("utf-8").split())
    new = sorted(set(zeros) - base)
    if new:
        if os.environ.get("PROOFMARK_ACCEPT_DEBT") == "1":
            ledger_line("exception", "untested-module",
                        f"new 0% module(s) pushed as debt: {new[:5]}")
        else:
            print(f"PROOFMARK BLOCK [untested-module] {len(new)} module(s) no "
                  f"test executes a single statement of, and not grandfathered: "
                  f"{new[:5]}")
            print("  Write a test that reaches the module. The baseline in "
                  "gates/untested_baseline.txt only shrinks.")
            print("  Logged escape hatch: PROOFMARK_ACCEPT_DEBT=1")
            ledger_line("block", "untested-module", f"new 0% modules: {new[:5]}")
            sys.exit(1)
    stale = sorted(base - set(zeros))
    if stale:
        print(f"PROOFMARK: {len(stale)} untested-baseline entr"
              f"{'y is' if len(stale) == 1 else 'ies are'} no longer at 0% - "
              f"remove from gates/untested_baseline.txt and commit: {stale[:5]}")
        note(f"untested baseline stale entries: {stale[:5]}")
    ledger_line("pass", "untested-module",
                f"{len(zeros)} untested module(s), all grandfathered")


def _substantive_added_source_lines() -> dict[str, int]:
    """Added lines per staged source file, ignoring blanks and comment-only lines.

    Blank and comment lines are excluded so that documenting existing code, or
    reformatting it, never demands a new test. What remains is code.
    """
    out: dict[str, int] = {}
    for path in git("diff", "--cached", "--name-only").stdout.splitlines():
        if not path.endswith(".py"):
            continue
        if not under_src(path):
            continue
        diff = git("diff", "--cached", "-U0", "--", path).stdout
        n = 0
        for line in diff.splitlines():
            if not line.startswith("+") or line.startswith("+++"):
                continue
            body = line[1:].strip()
            if not body or body.startswith("#"):
                continue
            n += 1
        if n:
            out[path] = n
    return out


def check_new_source_has_tests() -> None:
    """New source code must arrive with a test. The point of the whole system.

    Until this existed, only `fix:` commits had to touch a test. A feature
    commit could add branching logic wired into a live route with zero tests and
    pass every gate - demonstrated on Tad, commit ae23cdb, before this check was
    written. That is precisely the hole that matters when an agent is writing
    the functionality, because an agent will not volunteer a test it was not
    required to produce.

    Deliberately strict with a logged escape rather than a threshold. A line
    count would be arbitrary and gameable; PROOFMARK_SKIP_NEW_TESTS=1 is
    honest, appears in the ledger, and if it turns out to be needed every week
    then the gate is mistuned and the kill rule deletes it on the evidence.
    """
    added = _substantive_added_source_lines()
    if not added:
        return
    staged = git("diff", "--cached", "--name-only").stdout.splitlines()
    test_changed = [p for p in staged
                    if p.startswith(TESTS_REL + "/") and p.endswith(".py")
                    and Path(p).name != "test_canary.py"]
    if test_changed:
        return
    if os.environ.get("PROOFMARK_SKIP_NEW_TESTS"):
        ledger_line("exception", "new-source-needs-tests",
                    f"skipped for {sorted(added)[:5]}")
        return
    worst = ", ".join(f"{p} (+{n})" for p, n in sorted(added.items(), key=lambda kv: -kv[1])[:4])
    fail("new-source-needs-tests",
         f"source gained code and no test changed: {worst}. "
         f"Add or change a test under {TESTS_REL}/ that fails without this code. "
         f"Genuine refactor or config-only change? PROOFMARK_SKIP_NEW_TESTS=1 (logged).")


def reverse_pythonpath(tmp: str, environ: dict[str, str] | None = None) -> str:
    """PYTHONPATH that pins imports to the extracted HEAD tree at `tmp`.

    An editable install (`pip install -e`) writes a .pth into site-packages holding the
    absolute path of the LIVE source tree, and site-packages .pth entries join sys.path
    while the interpreter is starting. So inside the extraction `import <package>` resolved
    to the working copy, and the reverse test compared the new tests against the very source
    it exists to prove they exercise.

    Demonstrated on voxcast 2026-08-04: `tests/test_llm.py` imports `voxcast.llm`, which does
    not exist at HEAD, and it PASSED inside the extraction. That made the gate report "new
    tests PASS against HEAD source" for a module HEAD does not contain - a false block on
    every genuine feature commit, which is the failure direction that gets a gate bypassed.

    PYTHONPATH is consulted ahead of .pth paths, so pointing it at the extraction makes the
    extracted source win. The repo root goes on too, because a package may sit at the root
    rather than under a src/ dir. A file source root contributes nothing importable of its
    own - the root entry already covers it - so only directories are added.
    """
    environ = os.environ if environ is None else environ
    roots = [str(Path(tmp) / rel) for rel in SRC_RELS if (Path(tmp) / rel).is_dir()]
    roots.append(str(tmp))
    inherited = environ.get("PYTHONPATH", "")
    if inherited:
        roots.append(inherited)
    return os.pathsep.join(roots)


def check_reverse() -> None:
    staged = git("diff", "--cached", "--name-only").stdout.splitlines()
    tests_changed = [p for p in staged
                     if p.startswith(TESTS_REL + "/") and p.endswith(".py")
                     and Path(p).name not in ("test_canary.py", "conftest.py")]
    src_changed = [p for p in staged if under_src(p) and p.endswith(".py")]
    if not (tests_changed and src_changed):
        return
    if os.environ.get("PROOFMARK_SKIP_REVERSE"):
        ledger_line("exception", "reverse-test", f"skipped for {tests_changed}")
        return
    if git("rev-parse", "HEAD").returncode != 0:
        return
    archive = subprocess.run([GIT, "archive", "HEAD"], cwd=ROOT, capture_output=True, timeout=600)
    with tempfile.TemporaryDirectory(prefix="proofmark-rev-") as tmp:
        # filter="data" (3.12+) rejects absolute paths and parent-dir escapes;
        # the tar bytes come from `git archive HEAD` of this same repo.
        with tarfile.open(fileobj=BytesIO(archive.stdout)) as tf:
            for member in tf.getmembers():
                tf.extract(member, tmp, filter="data")
        for rel in tests_changed + [f"{TESTS_REL}/conftest.py"]:
            blob = git("show", f":{rel}")
            if blob.returncode == 0:
                dest = Path(tmp) / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(blob.stdout, encoding="utf-8")
        # The extracted tree mirrors the repo, so pytest must run from the same
        # relative position it does normally, with the test paths rebased to it.
        rev_cwd = Path(tmp) / PYTEST_CWD_REL if PYTEST_CWD_REL != "." else Path(tmp)
        rebased = [os.path.relpath(t, PYTEST_CWD_REL).replace("\\", "/")
                   if PYTEST_CWD_REL != "." else t for t in tests_changed]
        env = dict(os.environ)
        env["PYTHONPATH"] = reverse_pythonpath(tmp)
        p = run([PYTEST_PY, "-m", "pytest", *rebased, "-q",
                 "-p", "no:cacheprovider"], cwd=rev_cwd, env=env)
        if p.returncode == 0:
            fail("reverse-test", f"new/changed tests PASS against HEAD source; they do "
                                 f"not exercise this change: {tests_changed}. Pure "
                                 f"refactor? PROOFMARK_SKIP_REVERSE=1 (logged).")


def write_evidence() -> None:
    tree = git("write-tree").stdout.strip()
    import hashlib
    h = hashlib.sha256()
    for rel in CONFIG_FILES:
        f = ROOT / rel
        if f.exists():
            h.update(rel.encode() + b"\0" + f.read_bytes() + b"\0")
    EVIDENCE.write_text(json.dumps({
        "tree": tree, "config_sha256": h.hexdigest(),
        "ts": time.time(), "gate": "pre-commit", "result": "pass",
    }), encoding="utf-8")


# ----------------------------------------------------------- subcommands --

def pre_commit() -> None:
    started = time.time()
    check_doctor()
    check_ring_current()
    check_ring_manifest_current()
    check_layout()
    check_scope_complete()
    check_staged_matches_worktree()
    check_ruff()
    check_vulture()
    check_asserts()
    check_canary()
    check_collection_floor()
    check_unmarked_ratchet()
    check_untested_baseline_shrinks()
    check_e2e_floor()
    check_tests()
    check_new_source_has_tests()
    check_reverse()
    write_evidence()
    LOG.write_text("\n".join(_verbose), encoding="utf-8")
    elapsed = time.time() - started
    ledger_line("pass", "pre-commit", f"all gates green in {elapsed:.1f}s")
    print(f"PROOFMARK PASS ({elapsed:.1f}s)")


def post_commit() -> None:
    head_tree = git("rev-parse", "HEAD^{tree}").stdout.strip()
    ok = False
    if EVIDENCE.exists():
        ev = json.loads(EVIDENCE.read_text("utf-8"))
        ok = ev.get("tree") == head_tree and (time.time() - ev.get("ts", 0)) < 300
        EVIDENCE.unlink()
    # The SHA and subject are the whole point of this line. An override is the
    # one event the system flags but could not explain: three were recorded
    # against voxcast on 2026-08-04 and there was no way to ask WHICH commits
    # they were, so nobody could judge whether they were deliberate. A gate that
    # reports an anomaly it cannot attribute is only slightly better than one
    # that missed it.
    head = git("rev-parse", "HEAD").stdout.strip()
    subject = git("log", "-1", "--format=%s").stdout.strip()[:80]
    kind, detail = post_commit_line(ok, head, head_tree, subject)
    ledger_line(kind, "post-commit", detail)
    if not ok:
        print(f"PROOFMARK: override logged for {head[:12]} "
              f"(no matching gate evidence for this commit)")


def post_commit_line(ok: bool, head: str, tree: str, subject: str) -> tuple[str, str]:
    """The ledger line for a commit, bound or overridden. Pure, so it is tested.

    The SHA is the whole point. An override is the one event this system flags
    but could not explain: three were recorded against voxcast on 2026-08-04 and
    there was no way to ask WHICH commits they were, so nobody could judge
    whether they were deliberate. A gate that reports an anomaly it cannot
    attribute is only slightly better than one that missed it.
    """
    if ok:
        return "bound", f"{head[:12]} tree {tree[:12]} matches gate evidence: {subject}"
    return "override", (f"{head[:12]} landed without matching gate evidence "
                        f"(--no-verify, or the tree changed after the gate ran: "
                        f"TOCTOU): {subject}")


def fix_commit_class(msg: str) -> str | None:
    """The defect class of a fix commit, or None if this is not one. Pure.

    Case-insensitive: `Fix: handle nulls` is the same claim as `fix:` and used
    to bypass the gate entirely - no block, no escape row (Episode 4 review,
    M9). The class is slugged because the escape-report heading captures a
    single token: `fix(deploy thing):` used to write a class the answer
    round-trip could never match, so its rows stayed blank forever (m6).
    """
    m = re.match(r"^(fix|hotfix)(\(([^)]*)\))?[:!]", msg, re.IGNORECASE)
    if not m:
        return None
    cls = (m.group(3) or "unclassified").strip().lower()
    return re.sub(r"\s+", "-", cls) or "unclassified"


def feat_commit(msg: str) -> bool:
    """Is this a feature commit? Pure.

    Case-insensitive and `!`-tolerant for the same reason fix_commit_class is:
    `Feat: ...` and `feat!: ...` are the same claim, and a gate that reads only
    the lowercase form is bypassed by a shift key.
    """
    return bool(re.match(r"^(feat|feature)(\([^)]*\))?[:!]", msg, re.IGNORECASE))


def changelog_block(text: str, marker: str, closer: str = "]") -> str | None:
    """The changelog literal itself, from its marker line to the line that
    closes it. Pure. None when the marker is absent.

    Comparing this block between HEAD and the index is what tells a real entry
    apart from any other edit to the same file. Comparing whole files would be
    satisfied by the feature's own code, which lives in that file too, so the
    gate would pass on every commit it exists to catch.
    """
    if text is None:
        return None
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if marker in ln), None)
    if start is None:
        return None
    for i in range(start + 1, len(lines)):
        if lines[i].strip() == closer:
            return "\n".join(lines[start:i + 1])
    # Marker found and never closed: the file is not the shape the config
    # claims. Returning the tail would make the gate compare something
    # arbitrary, so the caller is told the block is unreadable instead.
    return None


def check_changelog_entry(msg: str) -> None:
    """A feature commit must also add or change a changelog entry.

    Written 2026-08-13 after FinDigger shipped twelve work packages in one
    night and its What is new page, the page whose only job is telling its
    reader what changed, sat a day and twelve features stale. Nothing noticed,
    because nothing could: every gate asked whether the code was tested, and
    none asked whether the change was announced. Tests were then added pinning
    that those twelve are announced, which does exactly nothing for the
    thirteenth - a census, not an invariant. This is the invariant.

    Scoped to `feat:` deliberately. A fix already has to move a test, and
    firing on every fix would put a changelog demand on commits that change
    nothing a reader would notice, which is how a gate earns a reputation for
    crying wolf and gets bypassed on the day it is right.

    Self-disabling: a repo with no [proofmark.changelog] block has no such
    surface and this never runs. That is what lets the ring carry it while only
    the repos with a user-facing changelog are held to it.
    """
    cfg = CONFIG.get("changelog")
    if not cfg or not feat_commit(msg):
        return
    rel = cfg.get("file")
    marker = cfg.get("marker")
    if not rel or not marker:
        fail("changelog-config",
             "proofmark.toml has a [proofmark.changelog] block without both "
             "'file' and 'marker'. A gate that cannot locate what it checks "
             "must not pass quietly.")
    closer = cfg.get("closer", "]")
    label = cfg.get("label", "the changelog")
    staged = git("diff", "--cached", "--name-only").stdout.splitlines()
    if not any(under_src(p) for p in staged):
        # A feature commit touching no source is a rename, a doc, or a revert
        # tidy. Nothing shipped, so there is nothing to announce.
        return
    head = changelog_block(head_file(rel) or "", marker, closer)
    index = changelog_block(git("show", f":{rel}").stdout, marker, closer)
    if index is None:
        fail("changelog-config",
             f"could not read the {label} block in {rel} (marker {marker!r}, "
             f"closer {closer!r}). The gate refuses to report green on a file "
             f"it cannot parse.")
    if head != index:
        return
    if os.environ.get("PROOFMARK_SKIP_CHANGELOG"):
        ledger_line("exception", "changelog-needs-entry",
                    f"skipped: {msg.splitlines()[0][:80]}")
        return
    ledger_line("block", "changelog-needs-entry",
                f"feat commit with no {label} entry: {msg.splitlines()[0][:80]}")
    print(f"PROOFMARK BLOCK [changelog-needs-entry] a 'feat:' commit must add or "
          f"change an entry in {label}")
    print(f"  it lives in {rel}, at {marker!r}")
    print(f"  say what changed and why it matters to whoever reads it, not what the code does")
    print(f"  genuinely invisible to a reader? PROOFMARK_SKIP_CHANGELOG=1 (logged)")
    sys.exit(1)


def _files_in_head() -> list[str]:
    """The files the commit at HEAD changed against its own parent.

    `git show --name-only` rather than a diff against HEAD~1, because it is
    also correct for a root commit, which has no HEAD~1 to diff against.
    A failure returns nothing, which blocks - the safe direction.
    """
    p = git("show", "--pretty=format:", "--name-only", "HEAD")
    if p.returncode != 0:
        return []
    return [ln for ln in p.stdout.splitlines() if ln.strip()]


def commit_msg(path: str) -> None:
    msg = Path(path).read_text(encoding="utf-8")
    check_changelog_entry(msg)
    cls = fix_commit_class(msg)
    if cls is None:
        return
    staged = git("diff", "--cached", "--name-only").stdout.splitlines()
    if not staged:
        # Nothing staged against HEAD means `git commit --amend` with no new
        # changes: a rewrite of the message alone. A normal commit with an
        # empty index never reaches this hook, because git refuses it first.
        #
        # The amended commit keeps HEAD's parent, so the honest question is
        # what HEAD already changed, not what was staged since. Reading the
        # index alone blocked every message-only amend of a fix commit, even
        # one carrying three new tests (found on PolicyPulse, 2026-08-28,
        # amending c54fe9b). The only ways past were --no-verify or
        # relabelling the commit type, and a gate that can only be satisfied
        # by lying about the commit type is worse than no gate.
        #
        # Two limits, both narrower than the bug they replace. An amend that
        # ALSO stages new files is judged on those files alone, because from
        # inside a hook there is no way to tell that case from an ordinary
        # commit. And `git commit --allow-empty` looks identical to a
        # message-only amend here, so an empty fix commit is judged on the
        # commit before it. Neither is reachable by accident.
        staged = _files_in_head()
    # A test change means a path under the TESTS DIR, not any filename with
    # "test" in it - `latest_prices.py` satisfied the old substring match
    # (Episode 4 review, M9). JS/TS tests are recognized by their runner
    # suffixes (Jest `*.test.*`, Playwright `*.spec.*`) - a suffix a source
    # file cannot wear without the runner collecting it as a test, so the
    # M9 gaming path stays closed.
    touched_tests = [p for p in staged
                     if (p.startswith(TESTS_REL + "/") and p.endswith(".py")
                         and Path(p).name != "test_canary.py")
                     or p.endswith(JS_TEST_SUFFIXES)]
    if not touched_tests:
        ledger_line("block", "never-fix-twice", f"fix commit with no test change: {msg.splitlines()[0][:80]}")
        print("PROOFMARK BLOCK [never-fix-twice] a 'fix:' commit must add or change a test")
        print(f"  the test belongs under {TESTS_REL}/ or ends in .test.*/.spec.* (js|jsx|ts|tsx);")
        print("  break-glass: git commit --no-verify (logged)")
        sys.exit(1)
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    with ESCAPES.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "date": datetime.now(timezone.utc).date().isoformat(),
            "repo": ROOT.name,
            "defect_class": cls,
            "caught_where": "human",
            "gate_should_have": "",
            "gate_existed_and_passed": "",
        }) + "\n")


# The two questions that turn a fix into a gate. They are asked in the ship
# report rather than here because a commit-msg hook cannot prompt, and because
# push is where a person is already writing judgment and where pre-push already
# refuses a blank - proven machinery, six real blocks behind it.
#
# Named as a constant for the same reason as SHIP_PROMPTS: a test asserts
# neither quietly disappears. The second one is the whole point of the file. A
# `yes` means a gate ran, passed, and let the defect through, which is the worst
# finding this system can produce and the one it exists to surface.
ESCAPE_PROMPTS = (
    "Which gate should have caught it (name one, or `none`):",
    "Did that gate already exist and pass anyway (yes/no):",
)

# Every gate name this file can block under.
#
# NOT a validation allowlist for the escape question - see normalise_gate_answer
# for why that would be wrong. This exists so the ledger report can tell a real
# gate from an admin bookkeeping name (`vulture-baseline`, `swallow-stale`) when
# deciding what to call unproven.
#
# test_gate_names_matches_the_source keeps this list honest by reading gate.py.
GATE_NAMES = frozenset({
    "assert-quality", "canary", "changelog-config", "changelog-needs-entry",
    "collect", "collect-floor", "doctor",
    "escape-analysis", "gate-error", "layout", "never-fix-twice",
    "new-source-needs-tests", "push-toctou", "pytest", "pytest-full", "reverse-test",
    "ring-manifest", "ring-stale", "ruff-swallow", "scope", "scope-empty-root",
    "e2e-floor", "scope-stale-exclusion", "ship-report", "swallow-ratchet",
    "toctou", "unmarked-ratchet", "untested-module", "vulture-orphan",
    "vulture-ratchet",
})


# A gate name is a slug. Not an allowlist on purpose.
GATE_ANSWER = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")


def normalise_gate_answer(raw: str) -> str | None:
    """The gate name, or None if the answer is prose rather than a name. Pure.

    Deliberately a SHAPE check and not a membership check against GATE_NAMES.
    "Which gate should have caught it" is legitimately answerable with a gate
    that does not exist yet - naming one is how the promote rule turns an escape
    into work, and the swallow escape's real answer was a body-reading check
    nobody had written. An allowlist would reject exactly the answers worth
    having, which is why the first question was left open in the first place.

    What it is not answerable with is a sentence. On 2026-08-05 the ledger took
    "`test_every_voice_ships_a_preview_clip` in tests/test_catalog.py - the
    preview contract." and rendered it as "VACUOUS GATE: '<all of that>' ran and
    passed" - naming a test, which is not a thing that can be a vacuous gate.
    The answer is interpolated straight into the loudest finding this system
    produces, so it has to be a name.

    Backticks and a trailing full stop are stripped rather than rejected:
    refusing `ruff-swallow` over its quoting would be the gate being clever at
    the writer's expense, which is the same reason `Yes` is accepted for yes.
    """
    v = raw.strip().strip("`'\"").strip().rstrip(".").strip("`'\"").strip().lower()
    return v if GATE_ANSWER.match(v) and len(v) <= 40 else None

ESCAPE_HEADING = re.compile(r"^###\s+escape\s+(\S+)\s+(\S+)")


def read_escapes() -> list[dict]:
    if not ESCAPES.exists():
        return []
    return [json.loads(ln) for ln in ESCAPES.read_text("utf-8").splitlines() if ln.strip()]


def escape_groups(rows: list[dict], repo: str | None = None) -> list[dict]:
    """Escapes collapsed to one entry per (date, defect class). Pure, so tested.

    Seven `fix(deploy):` commits in one debugging session are ONE escape, not
    seven. `defect_class` is only the conventional-commit scope, so counting
    rows counts commits - which is what made the promote rule demand a
    mechanical gate for a class that had just been given fifteen tests. A rule
    that fires after it has been satisfied is furniture, and furniture is what
    you stop reading right before the one time it mattered.

    Grouping by date as well as class is deliberate: the same class escaping
    again next month is a second escape and should say so.
    """
    groups: dict[tuple[str, str, str, str], dict] = {}
    for r in rows:
        if repo is not None and r.get("repo") != repo:
            continue
        # An ANSWERED row and an UNANSWERED row of the same class on the same
        # day are two escapes, not one, and merging them loses the second.
        #
        # Every row of a genuine multi-commit escape is written at commit time
        # and answered together at push time, so within one escape the rows are
        # uniformly blank until the push fills all of them. A blank row sitting
        # beside an answered one therefore means a SECOND escape of that class
        # landed after the first was analysed - and merging it made it inherit
        # an answer written about different work, and vanish from
        # render_escapes, which only asks about groups with no answer.
        #
        # Observed twice in two days. On 2026-08-05 it swallowed a `deploy`
        # escape whose honest answer was a vacuous `pytest` gate - a suite
        # written specifically to prevent a pipe into `crontab -`, which ran
        # green while the script that actually did it went uncovered. It
        # silently inherited `scope`/`no` from seven unrelated deploy commits
        # earlier the same day. Losing a vacuous-gate finding is losing the most
        # valuable thing this file produces.
        #
        # At most two buckets per (date, class), so the report headings that
        # parse_escape_answers keys on stay unique: render_escapes only ever
        # asks about the unanswered one.
        # Keyed on the ANSWER, not merely on whether there is one. Splitting
        # answered from blank was not enough: the recovered `deploy` escape was
        # answered `pytest`/`yes` and landed in a bucket with seven rows already
        # answered `scope`/`no`, where first-non-empty-wins made it invisible
        # again. Its finding - a vacuous pytest gate - was in the ledger and
        # absent from the report, which is the same loss by a shorter route.
        #
        # Rows of one genuine escape are all written by the same
        # apply_escape_answers call and therefore carry identical answers, so
        # this still collapses seven fix commits into one escape. Two different
        # answers were always two different escapes.
        key = (r.get("date", ""), r.get("defect_class") or "unclassified",
               r.get("gate_should_have") or "", r.get("gate_existed_and_passed") or "")
        g = groups.setdefault(key, {"date": key[0], "defect_class": key[1],
                                    "repo": r.get("repo", ""), "commits": 0,
                                    "gate_should_have": "",
                                    "gate_existed_and_passed": ""})
        g["commits"] += 1
        # One answer covers the group. The rows are split only because git
        # commits are, and the analysis is per escape, not per commit.
        for f in ("gate_should_have", "gate_existed_and_passed"):
            g[f] = g[f] or (r.get(f) or "")
    return sorted(groups.values(),
                  key=lambda g: (g["date"], g["defect_class"],
                                 not g["gate_should_have"]))


def render_escapes(groups: list[dict]) -> list[str]:
    """The report section, asking only about escapes nobody has analysed."""
    open_ = [g for g in groups if not g["gate_should_have"]]
    if not open_:
        return []
    out = ["", "## Escapes to analyse - to be written by a person",
           "",
           "One block per escape. A run of fix commits on one day for one thing is",
           "one escape, not several. `none` is a real answer to the first question -",
           "some defects no mechanical gate could have caught, and saying so is the",
           "analysis. The second is yes or no."]
    for g in open_:
        n = g["commits"]
        out += ["", f"### escape {g['date']} {g['defect_class']} "
                    f"({n} fix commit{'' if n == 1 else 's'})"]
        out += [f"- {p}" for p in ESCAPE_PROMPTS]
    return out


def answer_after(lines: list[str], idx: int, prompt: str) -> str:
    """The answer to a prompt on line `idx`: same line, or indented beneath it.

    Shared by the judgment prompts and the escape questions because both accept
    both shapes. The judgment check's first version demanded the same line and
    so rejected the one format its own template invites - a numbered list - and
    a gate that blocks the intended answer shape gets skipped.
    """
    head = lines[idx].strip().lstrip("-").strip()[len(prompt):].strip()
    if head:
        return head
    nxt = lines[idx + 1] if idx + 1 < len(lines) else ""
    # Indented and non-empty: the answer continues below. A blank line or a line
    # at the left margin means the next section started and nothing was written.
    return nxt.strip() if (nxt.strip() and nxt[:1].isspace()) else ""


def parse_escape_answers(text: str) -> dict[tuple[str, str], list[str]]:
    """Read the escape answers back out of a filled-in report. Pure, so tested."""
    answers: dict[tuple[str, str], list[str]] = {}
    lines = text.splitlines()
    key: tuple[str, str] | None = None
    for i, ln in enumerate(lines):
        m = ESCAPE_HEADING.match(ln.strip())
        if m:
            key = (m.group(1), m.group(2))
            answers[key] = ["", ""]
            continue
        if key is None:
            continue
        if ln.startswith("#"):          # a new section; this escape block ended
            key = None
            continue
        body = ln.strip().lstrip("-").strip()
        for j, p in enumerate(ESCAPE_PROMPTS):
            if body.startswith(p):
                answers[key][j] = answer_after(lines, i, p)
    return answers


def _yes_no(raw: str) -> str | None:
    v = raw.strip().lower().rstrip(".")
    return {"yes": "yes", "y": "yes", "no": "no", "n": "no"}.get(v)


def unanswered_escapes(text: str) -> list[str]:
    """Which escape blocks are still blank or unreadable. Pure, so it is tested.

    The first question takes any non-empty text, for the same reason the
    judgment prompts do: a gate cannot tell a good answer from a bad one and
    pretending otherwise makes it probabilistic.

    The second does NOT, because it is not a judgment - it is a closed set of
    two. Accepting anything there is how the field ends up reading "n/a" on
    every row, which is how it ended up blank on every row.
    """
    bad = []
    for (date, cls), (named, passed) in sorted(parse_escape_answers(text).items()):
        if not named:
            bad.append(f"{date} {cls}: which gate should have caught it")
        elif normalise_gate_answer(named) is None:
            bad.append(f"{date} {cls}: {named[:60]!r} is not a gate NAME. It may name "
                       f"a gate that does not exist yet - that is how an escape "
                       f"becomes work - but it has to be a name like "
                       f"`handler-body-trace`, or `none`. This answer is printed "
                       f"straight into 'VACUOUS GATE: <it> ran and passed', and a "
                       f"sentence there is not a claim anyone can act on.")
        if _yes_no(passed) is None:
            bad.append(f"{date} {cls}: did that gate exist and pass - needs yes or "
                       f"no, got {passed or '(nothing)'!r}")
    return bad


def apply_escape_answers(rows: list[dict], repo: str,
                         answers: dict[tuple[str, str], list[str]]) -> list[dict]:
    """Merge the report's answers into the escape rows. Pure, so it is tested.

    Only ever fills a blank. An answered row is history and a later report must
    not quietly restate it.
    """
    out = []
    for r in rows:
        key = (r.get("date", ""), r.get("defect_class") or "unclassified")
        ans = answers.get(key)
        if r.get("repo") == repo and ans and ans[0] and not r.get("gate_should_have"):
            # Store the canonical name, not the typing. `Ruff-Swallow.` and
            # `ruff-swallow` are the same answer, and escape_findings groups on
            # this value - two spellings would read as two different gates.
            r = dict(r, gate_should_have=normalise_gate_answer(ans[0]) or ans[0],
                     gate_existed_and_passed=_yes_no(ans[1]) or "")
        out.append(r)
    return out


def write_escapes(rows: list[dict]) -> None:
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    ESCAPES.write_text("".join(json.dumps(r) + "\n" for r in rows),
                       encoding="utf-8", newline="\n")


def escape_findings(groups: list[dict]) -> list[str]:
    """What the escape ledger has to say. Pure, so it is tested.

    Three separate things, deliberately not collapsed into one PROMOTE line:

    A gate that existed, ran, passed, and let the defect through is a vacuous
    gate, and that is the loudest thing this file can tell you - it means a
    green run proves less than you thought. It gets said first and it never
    stops being said, because deleting the row is the only way to make it go
    away and that should feel like the deliberate act it is.

    A class with no gate yet, escaping twice or more, is the promotion rule.

    An escape nobody has analysed is not a finding at all - it is a question,
    and the answer is a command to run.
    """
    out: list[str] = []
    for g in groups:
        if g["gate_existed_and_passed"] == "yes":
            out.append(f"VACUOUS GATE: '{g['gate_should_have']}' ran and passed while "
                       f"{g['defect_class']} escaped on {g['date']} - a green run from "
                       f"that gate proves less than it looks like")
    missing: dict[str, int] = {}
    for g in groups:
        if g["gate_should_have"] and g["gate_existed_and_passed"] == "no" \
                and g["gate_should_have"].strip().lower() != "none":
            missing[g["defect_class"]] = missing.get(g["defect_class"], 0) + 1
    for c, n in sorted(missing.items()):
        if n >= 2:
            out.append(f"PROMOTE: '{c}' has escaped {n} times with no gate - it gets a "
                       f"mechanical gate this week (promotion rule, no deliberation)")
    unanalysed = [g for g in groups if not g["gate_should_have"]]
    if unanalysed:
        by_repo: dict[str, int] = {}
        for g in unanalysed:
            by_repo[g["repo"]] = by_repo.get(g["repo"], 0) + 1
        listed = ", ".join(f"{r or '?'} {n}" for r, n in sorted(by_repo.items()))
        out.append(f"UNANALYSED: {len(unanalysed)} escapes never had their two "
                   f"questions answered ({listed}) - `gate.py ship` asks them, in the "
                   f"repo they belong to")
    return out


def ledger_summary(entries: list[dict]) -> dict:
    """Per-gate counts and the kill-rule verdicts. Pure, so it can be tested.

    Extracted because the kill rule is the one piece of this system that will
    DELETE a gate, unread, on a fixed date - and it decides that by comparing two
    numbers it reads out of the ledger. Those numbers were being assembled inside
    a print loop where nothing could check them.

    The distinction that matters: an `exception` is a gate SKIPPED by a human
    who wanted past it, which is the signal the kill rule is built on. An `admin`
    is a maintenance action like regenerating the vulture baseline. Both used to
    be written as "exception", so nine baseline regenerations sat in the column
    that decides whether a gate gets deleted. It could not fire yet, because
    vulture-baseline has no blocks and the rule needs `exception > block > 0`,
    but a metric that is wrong in the safe direction is still wrong.
    """
    stats: dict[str, dict[str, int]] = {}
    for entry in entries:
        gate = stats.setdefault(entry["gate"], {})
        gate[entry["kind"]] = gate.get(entry["kind"], 0) + 1

    rows, kills = [], []
    total_blocks = total_overrides = 0
    for gate, k in sorted(stats.items()):
        total_blocks += k.get("block", 0)
        total_overrides += k.get("override", 0)
        rows.append({
            "gate": gate,
            "pass": k.get("pass", 0) + k.get("bound", 0),
            "block": k.get("block", 0),
            "exception": k.get("exception", 0),
            "admin": k.get("admin", 0),
        })
        # post-commit is exempt: its "exceptions" are override records about
        # other gates, not a decision to skip post-commit itself.
        if gate != "post-commit" and k.get("exception", 0) > k.get("block", 0) > 0:
            kills.append(gate)

    # Skipped at least once, never blocked anything. NOT a kill: the rule
    # deliberately needs `block > 0` first, because a gate that has been stepped
    # around twice and never triggered is unproven rather than disproven, and
    # deleting it on that evidence would be the rule eating its own young
    # (test_should_not_fire_on_a_gate_that_has_never_blocked pins that).
    #
    # But it is the one state the summary could not describe at all, so a gate
    # being routed around every week scored exactly as healthy as one nobody has
    # ever needed to skip. Saying so is not deleting it. Restricted to real
    # blocking gates so the admin ledger names - vulture-baseline carries ten
    # historical "exception" lines from before that bug was fixed - stay out.
    unproven = sorted(
        r["gate"] for r in rows
        if r["gate"] in GATE_NAMES and r["exception"] > 0 and r["block"] == 0
    )
    return {
        "rows": rows,
        "kills": kills,
        "unproven": unproven,
        "total_blocks": total_blocks,
        "total_overrides": total_overrides,
        "suite_kill": total_overrides > total_blocks > 0,
    }


def ledger_files() -> list[Path]:
    """Every ledger file visible from this repo.

    The live per-repo ledger, the frozen pre-split archive (ledger/ at the
    repo root, where six repos wrote until 2026-08-05), and - in canonical
    only - each ring install's live ledger, so `report` and the kill rule
    still judge the whole ring from one place.
    """
    files = [LEDGER, ROOT / "ledger" / "ledger.jsonl"]
    if IS_CANONICAL:
        try:
            ring_cfg = tomllib.loads((GATES / "ring.toml").read_text("utf-8"))
            for repo in ring_cfg["ring"]["repos"]:
                files.append(Path(repo) / "gates" / "ledger" / "ledger.jsonl")
        except (OSError, ValueError, KeyError):
            pass  # a ring.toml that cannot be read narrows the report, loudly below
    return [f for f in files if f.exists()]


def report() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    entries = []
    sources = ledger_files()
    for f in sources:
        for line in f.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            if datetime.fromisoformat(e["ts"]) >= cutoff:
                entries.append(e)
    s = ledger_summary(entries)
    print(f"Proofmark ledger, last 30 days ({len(sources)} source file(s))")
    print(f"{'gate':18} {'pass':>5} {'block':>5} {'skipped':>8} {'admin':>6}")
    for row in s["rows"]:
        print(f"{row['gate']:18} {row['pass']:>5} {row['block']:>5} "
              f"{row['exception']:>8} {row['admin']:>6}")
        if row["gate"] in s["kills"]:
            print(f"  KILL RULE (dated 2026-09-02): {row['gate']} was skipped more often "
                  f"than it blocked - demote to advisory or delete, do not argue with it")
    print(f"overrides (--no-verify / evidence mismatch, not attributable per-gate): "
          f"{s['total_overrides']}")
    if s["unproven"]:
        print(f"UNPROVEN AND SKIPPED: {', '.join(s['unproven'])} - stepped around at "
              f"least once, never blocked anything. The kill rule cannot reach these "
              f"(it needs one real block first), so they will sit here indefinitely "
              f"unless someone proves them by fault injection or removes them.")
    if s["suite_kill"]:
        print("KILL RULE (dated 2026-09-02): more overrides than real blocks across "
              "the suite - the gates are wrong, not you; cut until this inverts")
    for finding in escape_findings(escape_groups(read_escapes())):
        print(finding)


def scope() -> None:
    """The coverage picture, on demand. Exit 1 if anything Python is uncovered.

    Separate from the commit gate because the two questions have different
    answers. The gate asks "can this commit go in", and only Python can block.
    This asks "what does the ring actually reach", which is the question at
    install time and during a review, and there the non-Python inventory is the
    interesting part - it is how a 3,971-line frontend stops being invisible
    without pretending a Python toolchain is going to lint it.
    """
    excludes = _excludes()
    uncovered, stale, empty_roots, other = scope_rules.findings(
        _tracked_files(), SRC_RELS, TESTS_REL, excludes
    )
    print(f"Proofmark scope, {ROOT.name}")
    print(f"  src_dirs  : {list(SRC_RELS)}")
    print(f"  tests_dir : {TESTS_REL}")
    for pattern, reason in sorted(excludes.items()):
        builtin = " [built in]" if pattern in scope_rules.BUILTIN_EXCLUDE else ""
        print(f"  exclude   : {pattern}{builtin}  - {reason}")

    print(f"\nPython outside every root: {len(uncovered)}")
    for path in uncovered:
        print(f"  {path}")
    if empty_roots:
        print(f"\nDeclared roots holding no tracked file: {empty_roots}")
    if stale:
        print(f"\nExclusions matching nothing: {stale}")

    if other:
        total = sum(other.values())
        print(f"\nCode no gate can read: {total} file(s), anywhere in the repo. "
              "Advisory, never blocking - every Phase 1 gate is a Python tool, so "
              "being inside src_dirs buys these nothing.")
        for suffix, count in sorted(other.items(), key=lambda kv: -kv[1])[:10]:
            print(f"  {suffix:<16} {count}")

    if uncovered:
        print("\n" + scope_rules.uncovered_message(uncovered, SRC_RELS, TESTS_REL))
    sys.exit(1 if uncovered or stale or empty_roots else 0)


def coverage(seed: bool = False) -> None:
    """Measured statement coverage, on demand. A reported fact, never a gate.

    --seed-untested additionally writes gates/untested_baseline.txt from the
    modules measured at 0% - the adoption step for the untested-module gate
    (Phase C). Committing that file arms enforcement at the next push.

    Phase A of ROADMAP.md. Coverage is deliberately not wired into any hook and
    never blocks: a blocking coverage number is a training signal for vacuous
    tests (research-brief.md, decision kept 2026-08-05). The completeness gate
    is the untested-module check, Phase C.

    Exit 0 means the table above is a real measurement of the full suite.
    Nonzero means it could NOT be measured, stated by name - exiting 0 over
    garbage would be absence dressed as success, which is the one failure mode
    this system exists to prevent.
    """
    probe = run([PYTEST_PY, "-c", "import pytest_cov"])
    if probe.returncode != 0:
        print("PROOFMARK coverage: pytest-cov is not installed in the venv "
              "that runs this repo's tests")
        print(f"  interpreter : {PYTEST_PY}")
        print(f"  install     : \"{PYTEST_PY}\" -m pip install pytest-cov")
        sys.exit(1)
    p = cov_run()
    print(p.stdout)
    if p.returncode != 0:
        print("PROOFMARK coverage: the suite did not pass, so the numbers "
              "above describe a FAILING run, not a baseline")
        sys.exit(1)
    persist_coverage_total(p.stdout)
    if seed:
        zeros = zero_covered_modules(p.stdout)
        (GATES / "untested_baseline.txt").write_text(
            "".join(f"{z}\n" for z in zeros), encoding="utf-8", newline="\n")
        ledger_line("admin", "untested-baseline",
                    f"baseline seeded: {len(zeros)} module(s) at 0%")
        print(f"PROOFMARK: untested-module baseline seeded with {len(zeros)} "
              f"module(s) - commit gates/untested_baseline.txt to adopt the gate")
    sys.exit(0)


def cov_run():
    """The FULL suite under coverage - what pre-push proves, measured."""
    cov_args = [f"--cov={os.path.relpath(ROOT / rel, PYTEST_CWD)}"
                for rel in SRC_RELS]
    return run([PYTEST_PY, "-m", "pytest", str(TESTS), "-q", *cov_args,
                "--cov-report=term", "-p", "no:cacheprovider"], cwd=PYTEST_CWD,
               timeout=SUITE_HANG_BACKSTOP)


def persist_coverage_total(stdout: str) -> None:
    total = next((ln for ln in stdout.splitlines() if ln.startswith("TOTAL")), None)
    if total:
        # Runtime artifact like last_run.log: the ship report quotes it as the
        # last measured fact, so a measurement outlives the terminal it ran in.
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        (GATES / "last_coverage.txt").write_text(
            f"{' '.join(total.split())}  measured {stamp}\n", encoding="utf-8")


def parse_coverage_rows(stdout: str) -> list[tuple[str, int, int]]:
    """(path, statements, percent) for every module row in a term report. Pure.

    Tolerant of coverage's report VARIANTS, not just the default three-column
    table: `branch = true` adds two columns before the percent, and
    `show_missing` appends a missing-lines column after it. The first version
    matched only the default shape, so a repo adding either option to its own
    coverage config would have silenced the untested-module gate permanently -
    zero rows parsed, zero modules at 0%, "all grandfathered" forever
    (Episode 4 review, M6 - the direct sibling of the 5/212 denominator bug).
    """
    rows = []
    for ln in stdout.splitlines():
        m = re.match(r"(\S+?\.py)\s+(\d+)\s+\d+(?:\s+\d+)*\s+(\d+)%(?:\s+[\d\s,-]*)?$",
                     ln.rstrip())
        if m:
            rows.append((m.group(1).replace("\\", "/"), int(m.group(2)), int(m.group(3))))
    return rows


def coverage_parse_failure(stdout: str, rows: list) -> str | None:
    """Why this coverage output cannot be trusted, or None if it can. Pure.

    A commitment to measure that silently stops parsing is fail-open in
    disguise - the same sentence the pytest-cov-missing block uses. TOTAL
    proves coverage actually reported; at least one module row proves the
    table format was understood.
    """
    if not any(ln.startswith("TOTAL") for ln in stdout.splitlines()):
        return "no TOTAL line in the coverage output - coverage did not report"
    if not rows:
        return ("a TOTAL line but no parseable module rows - the report format "
                "is not one this gate understands")
    return None


def zero_covered_modules(stdout: str) -> list[str]:
    """Source modules the suite never executed a single statement of.

    Read from coverage's term report, which - because --cov names source DIRS -
    also lists files no test ever imported. This is the measured instrument;
    the import-reachability proxy was wrong in both directions on 2026-08-05
    (called WineCellar's covered app.py unreached, called Tad's 27% scribe.py
    clean) and must not be used for this.
    """
    tests_prefix = os.path.relpath(ROOT / TESTS_REL, PYTEST_CWD).replace("\\", "/")
    zeros = []
    for path, stmts, pct in parse_coverage_rows(stdout):
        if path == tests_prefix or path.startswith(tests_prefix + "/"):
            continue
        if pct == 0 and stmts > 0:
            zeros.append(path)
    return sorted(zeros)


def check_untested_baseline_shrinks() -> None:
    """Pre-commit: gates/untested_baseline.txt gains no entries.

    The baseline is the grandfathered list of modules no test reaches. New
    debt is refused - the honest move is writing a test that reaches the
    module. Initial adoption (no baseline at HEAD) is allowed, and
    PROOFMARK_ACCEPT_DEBT=1 is the logged exception, same as the other
    ratchets.
    """
    base_file = GATES / "untested_baseline.txt"
    head = head_file("gates/untested_baseline.txt")
    if not base_file.exists():
        if head is None:
            return  # never adopted - the plain unmeasured push, stated not silent
        # Adopted at HEAD, missing here: deleting the baseline silently
        # de-adopts the untested-module gate (Episode 4 review, M5).
        if os.environ.get("PROOFMARK_ACCEPT_DEBT") == "1":
            ledger_line("exception", "untested-module",
                        "adopted baseline deleted; de-adoption accepted via "
                        "PROOFMARK_ACCEPT_DEBT")
            return
        fail("untested-module",
             "gates/untested_baseline.txt exists at HEAD but is missing here: "
             "deleting the baseline silently de-adopts the untested-module gate "
             "and the next push runs unmeasured. Restore it, or de-adopt "
             "deliberately: PROOFMARK_ACCEPT_DEBT=1 (logged)")
        return
    if head is None:
        return  # adoption commit
    added = sorted(
        set(base_file.read_text("utf-8").split()) - set(head.split()))
    if not added:
        return
    if os.environ.get("PROOFMARK_ACCEPT_DEBT") == "1":
        ledger_line("exception", "untested-module",
                    f"baseline additions accepted as debt: {added[:5]}")
        return
    fail("untested-module",
         f"baseline gained {len(added)} entr{'y' if len(added) == 1 else 'ies'} "
         f"({added[:5]}): the untested list only shrinks - write a test that "
         f"reaches the module instead")


def coverage_fact() -> str:
    """The last measured coverage, for the ship report. A fact, never a gate."""
    f = GATES / "last_coverage.txt"
    if not f.exists():
        return ("never measured in this clone - run "
                "`gate.py coverage` (reported fact, blocks nothing)")
    return f.read_text("utf-8").strip()


def levels() -> None:
    """The test mix by size and level, on demand. Report-only, Phase B.

    Size (small/medium/large) is what the machine enforces; level
    (unit/integration/e2e) is the vocabulary humans use, registered but
    advisory. Unmarked tests are grandfathered - legal, counted, and the
    count is the number that should only ever shrink.
    """
    def count(expr: str | None) -> int | None:
        cmd = [PYTEST_PY, "-m", "pytest", str(TESTS), "--co", "-q",
               "-p", "no:cacheprovider"]
        if expr:
            cmd += ["-m", expr]
        p = run(cmd, cwd=PYTEST_CWD)
        if p.returncode == 5:
            return 0
        return filtered_collected_count(p.stdout)

    total = count(None)
    print(f"Proofmark levels, {ROOT.name}  ({total} tests)")
    print("  size  (enforced)")
    for m in ("small", "medium", "large"):
        print(f"    {m:12} {count(m)}")
    unmarked = count("not small and not medium and not large")
    print(f"    {'unmarked':12} {unmarked}   <- ratcheted by unmarked-ratchet: may only shrink")
    print("  level (reported)")
    for m in ("unit", "integration", "e2e"):
        print(f"    {m:12} {count(m)}")
    sys.exit(0)


def ship(since: str | None = None) -> None:
    """Emit the factual half of the end-of-work report. Facts only, from record.

    The report shape comes from docs/SESSION_BRIEF.md: what shipped with commit
    hashes, what you found and did not fix, the diagnosis you got wrong first,
    numbered open questions, rollback instructions.

    Half of that is judgment and no tool can write it. The other half is fact -
    commit hashes, the test delta, what the gates actually did, the rollback
    command - and that half kept being written from memory. This project's own
    board records the same failure three times: "a stated number turned out to
    be the wrong quantity for the sentence it was in." So the numbers come from
    git and the ledger here, and the judgment sections are left blank on purpose
    for a human or an agent to fill in.
    """
    if since is None:
        up = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        since = up.stdout.strip() if up.returncode == 0 else None
    if not since:
        print("PROOFMARK ship: no upstream; pass an explicit ref, e.g. "
              "`gate.py ship HEAD~5`")
        sys.exit(2)

    rng = f"{since}..HEAD"
    commits = git("log", "--oneline", "--no-decorate", rng).stdout.strip().splitlines()
    if not commits:
        print(f"PROOFMARK ship: nothing in {rng}")
        return
    oldest = git("rev-parse", f"{rng.split('..')[0]}").stdout.strip()[:12]

    floor_now = (GATES / "min_test_count.txt").read_text("utf-8").strip()
    prev = git("show", f"{since}:gates/min_test_count.txt")
    floor_then = prev.stdout.strip() if prev.returncode == 0 else "n/a"

    stat = git("diff", "--stat", rng).stdout.strip().splitlines()
    since_ts = git("log", "-1", "--format=%cI", since).stdout.strip()

    kinds: dict[str, int] = {}
    detail: list[str] = []
    for f in ledger_files():
        for line in f.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            if e.get("repo") != ROOT.name or not entry_is_in_range(e["ts"], since_ts):
                continue
            kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
            if e["kind"] in ("block", "override", "exception"):
                detail.append(f"    {e['kind']:9} {e['gate']}: {e['detail'][:110]}")

    head = git("rev-parse", "HEAD").stdout.strip()
    escapes = render_escapes(escape_groups(read_escapes(), ROOT.name))
    text = render_ship(ROOT.name, rng, commits, stat[-1].strip() if stat else "",
                       floor_then, floor_now, kinds, detail, oldest, escapes)
    body = f"<!-- proofmark-ship head={head} -->\n{text}\n"
    REPORTS.mkdir(parents=True, exist_ok=True)
    target = REPORTS / f"{head[:12]}.md"
    target.write_text(body, encoding="utf-8", newline="\n")
    print(text)
    print(f"\n---\nwritten to {target.relative_to(ROOT)}")
    print("Fill in the four sections above, commit the report, then push.")


def entry_is_in_range(entry_ts: str, since_ts: str) -> bool:
    """Was this ledger entry written at or after `since_ts`? Pure, so it is tested.

    Compares instants, not strings. The ledger stamps UTC (`+00:00`) and git's
    %cI returns the committer's local offset (`-05:00` here), and ISO-8601 only
    sorts lexically when the offsets match. It read `15:23:04+00:00` as later
    than `14:05:05-05:00` when it is in fact five hours earlier - so an override
    belonging to an already-pushed commit was reported against three new ones.

    A false attribution in the report is worse than a missing one: the whole
    point of naming the SHA on an override was that an anomaly you cannot trace
    to a commit cannot be judged, and an anomaly traced to the WRONG commit
    sends you to look at work that is not the cause.
    """
    if not since_ts:
        return True
    try:
        return datetime.fromisoformat(entry_ts) >= datetime.fromisoformat(since_ts)
    except ValueError:
        # An unparseable stamp must not silently drop the entry. Over-reporting
        # a gate event is recoverable by reading it; losing one is not.
        return True


def report_is_current(report_head: str, tips: list[str]) -> bool:
    """Does this report describe the commit being pushed? Pure, so it is tested.

    `tips` is the last two commits of the ref being pushed. Two, not one,
    because the workflow forces that order: ship runs at HEAD, then committing
    the report moves HEAD past it.

    The first version of this gate asked whether the report's head appeared
    ANYWHERE in the pushed range, and Proofmark's own first push passed on a
    report two commits stale describing entirely different work. On an initial
    push of thirty commits a single old report vouched for all of them.
    """
    return bool(report_head) and report_head in tips


def unanswered_prompts(text: str) -> list[str]:
    """Which judgment prompts are still blank. Pure, so it can be tested.

    A report that exists but was never filled in is worse than none: it looks
    like the work was done. The check is deliberately shallow - any non-empty
    text after the prompt counts - because a gate cannot judge whether an answer
    is a good one, and pretending otherwise would be a probabilistic gate.

    An answer may either follow the prompt on its line or run on beneath it in
    indented lines. The first version demanded the same line, which rejected the
    one format the template actively invites: "Numbered open questions:" asks
    for a list, and a list does not fit on the prompt line. A gate that blocks
    the intended answer shape gets skipped, and a gate that gets skipped dies to
    the kill rule - so this is a defect in the check, not strictness.

    Prompt matching itself stays exact. A wrapped prompt still reads as missing,
    because matching questions fuzzily is how a gate starts guessing.
    """
    lines = text.splitlines()
    blank = []
    for prompt in SHIP_PROMPTS:
        idx = next((i for i, ln in enumerate(lines)
                    if ln.strip().lstrip("-").strip().startswith(prompt)), None)
        if idx is None:
            blank.append(prompt)               # deleting the question is not answering it
            continue
        if not answer_after(lines, idx, prompt):
            blank.append(prompt)
    return blank


def push_toctou_findings(tips: list[str], head: str,
                         porcelain: list[str]) -> list[str]:
    """Why this push's worktree cannot vouch for the pushed commits. Pure.

    Pre-push runs the suite against the WORKING TREE, but what ships is the
    pushed commit. Pre-commit closed this gap with check_staged_matches_worktree
    (the toctou gate); push never had the analogue - so a broken committed test
    with an uncommitted fix on disk pushed green while the ledger recorded a
    pass (Episode 4 review, C1). Same defect class, one boundary later.

    Two conditions make the suite run evidence about the push: the tip being
    pushed is the commit the worktree sits on, and no tracked file differs from
    it. Untracked files are deliberately allowed - scratch files are normal,
    and collect-floor already declines to ratchet on them.
    """
    findings = []
    for tip in tips:
        if head and tip != head:
            findings.append(f"pushing {tip[:12]} but the worktree is at "
                            f"{head[:12]} - the suite about to run proves "
                            f"nothing about the pushed commit")
    dirty = [ln for ln in porcelain
             if len(ln) >= 2 and (ln[0] not in " ?" or ln[1] not in " ?")]
    if dirty:
        findings.append(f"tracked file(s) differ from HEAD: {dirty[:5]}")
    return findings


def pre_push() -> None:
    """Refuse a push whose commits have no filled-in report.

    Push is the ship boundary, so this is where the end-of-work report stops
    being something someone remembers and starts being something that happens.
    Git hands us the refs on stdin; anything we cannot parse is treated as
    "needs a report" rather than waved through.
    """
    # Only the TIP counts, not "somewhere in the pushed range". The first
    # version accepted any report whose head appeared among the pushed commits,
    # and Proofmark's own first push sailed through on a report two commits
    # stale that described entirely different work. A report has to describe the
    # thing being pushed or it is decoration.
    #
    # HEAD~1 is accepted because of the order the workflow forces: `ship` runs
    # at HEAD, then the report is committed, which moves HEAD past it. Anything
    # older means commits landed after the report was written.
    pushing: list[str] = []
    tips_only: list[str] = []
    for line in sys.stdin.read().splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        _, local_sha, _, _remote = parts
        if local_sha.strip("0") == "":       # branch deletion, nothing to report
            continue
        pair = git("rev-list", "-2", local_sha).stdout.split()
        if pair:
            tips_only.append(pair[0])
        pushing += pair
    if not pushing:
        return

    # Before anything runs: the suite must be about to test what is being
    # pushed. Cheap, and pointless to run the full suite when it is not.
    head_now = git("rev-parse", "HEAD").stdout.strip()
    porcelain = git("status", "--porcelain").stdout.splitlines()
    findings = push_toctou_findings(tips_only, head_now, porcelain)
    if findings:
        if os.environ.get("PROOFMARK_SKIP_PUSH_TOCTOU") == "1":
            ledger_line("exception", "push-toctou", "; ".join(findings)[:300])
        else:
            print("PROOFMARK BLOCK [push-toctou] the pre-push suite would not "
                  "test what is being pushed:")
            for f in findings[:5]:
                print(f"    - {f}")
            print("  Commit or stash first, then push. "
                  "Logged escape: PROOFMARK_SKIP_PUSH_TOCTOU=1")
            ledger_line("block", "push-toctou", "; ".join(findings)[:300])
            sys.exit(1)

    check_tests_full()

    # The report half of pre-push. Scoped so the escape hatch skips ONLY this:
    # until 2026-08-06 the env check sat at the top of pre_push and silently
    # skipped pytest-full and untested-module too, logged under one gate name
    # the kill rule would misjudge (Episode 4 review, M2).
    if os.environ.get("PROOFMARK_SKIP_SHIP_REPORT"):
        ledger_line("exception", "ship-report", "skipped via PROOFMARK_SKIP_SHIP_REPORT")
        return

    for report in sorted(REPORTS.glob("*.md")) if REPORTS.is_dir() else []:
        text = report.read_text(encoding="utf-8")
        m = re.search(r"proofmark-ship head=([0-9a-f]{7,40})", text)
        if not m or not report_is_current(m.group(1), pushing):
            continue
        blank = unanswered_prompts(text)
        if blank:
            print(f"PROOFMARK BLOCK [ship-report] {report.name} exists but these "
                  f"are still blank:")
            for b in blank:
                print(f"    - {b}")
            print("  A report nobody filled in looks like the work was done.")
            ledger_line("block", "ship-report", f"unfilled report {report.name}: {blank}")
            sys.exit(1)

        # Its own gate name, not folded into ship-report. A gate that reports
        # under another gate's name is invisible to the kill rule, and the kill
        # rule is the only thing that ever removes a gate from this system.
        open_escapes = unanswered_escapes(text)
        if open_escapes:
            print(f"PROOFMARK BLOCK [escape-analysis] {report.name} lists escapes "
                  f"whose two questions are not answered:")
            for b in open_escapes:
                print(f"    - {b}")
            print("  These two answers are the entire point of the escape ledger:")
            print("  which gate should have caught it, and did that gate pass anyway.")
            print("  Without them the file is a tally of fix commits.")
            ledger_line("block", "escape-analysis",
                        f"{report.name}: {len(open_escapes)} unanswered")
            sys.exit(1)

        # The report must be IN the pushed history, not merely on disk. A
        # report read from the worktree can be filled in, pushed past, and
        # deleted - the enforced artifact was never durable evidence
        # (Episode 4 review, M10). cat-file -e asks the pushed tip's tree.
        committed = any(
            git("cat-file", "-e", f"{tip}:gates/reports/{report.name}").returncode == 0
            for tip in tips_only)
        if not committed:
            print(f"PROOFMARK BLOCK [ship-report] {report.name} covers this push "
                  f"but is not committed in the pushed history - an uncommitted "
                  f"report is not durable evidence.")
            print("  Commit the report, then push again.")
            ledger_line("block", "ship-report",
                        f"{report.name} filled in but not committed in the pushed tree")
            sys.exit(1)

        answers = parse_escape_answers(text)
        if answers:
            write_escapes(apply_escape_answers(read_escapes(), ROOT.name, answers))
            ledger_line("pass", "escape-analysis",
                        f"{report.name} analysed {len(answers)} escape(s)")
        ledger_line("pass", "ship-report", f"{report.name} covers this push")
        return

    print("PROOFMARK BLOCK [ship-report] no end-of-work report covers the commit "
          "being pushed.")
    print("  A report from earlier in the branch does not count: it describes "
          "different work.")
    print(f"  Run:  .venv-proofmark\\Scripts\\python.exe gates\\gate.py ship")
    print("  then answer the four questions it leaves blank, commit the report, "
          "and push again.")
    print("  Genuinely not a ship? PROOFMARK_SKIP_SHIP_REPORT=1 (logged).")
    ledger_line("block", "ship-report", f"no report for {len(pushing)} commit(s)")
    sys.exit(1)


# The judgment sections a tool cannot fill in. Named as a constant so a test can
# assert none of them ever quietly disappears from the report: dropping one is
# how "the diagnosis you got wrong first" stops being asked for, and that line is
# the most valuable thing in the whole template.
SHIP_PROMPTS = (
    "What you found and did NOT fix:",
    "The diagnosis you got wrong first, and what corrected it:",
    "Numbered open questions:",
    "Verified live by fetching real content (not a status code)? what, and what did it say:",
)


def render_ship(repo: str, rng: str, commits: list[str], stat: str,
                floor_then: str, floor_now: str, kinds: dict[str, int],
                detail: list[str], oldest: str, escapes: list[str] | None = None) -> str:
    """Pure: turn gathered facts into the report. Separated so it can be tested.

    Everything above this reads git and the ledger; this function reads nothing.
    """
    out = [f"# Report - {repo}, {rng}", "", "## What shipped"]
    out += [f"- {c}" for c in commits]
    out += ["", stat or "no file changes"]
    out += ["", "## Test floor",
            f"{floor_then} -> {floor_now}"
            + ("  (unchanged)" if floor_then == floor_now else "  (ratcheted)")]
    out += ["", "## Coverage (reported fact, never a gate)", coverage_fact()]
    out += ["", "## What the gates did",
            f"passes {kinds.get('pass', 0)}, bound {kinds.get('bound', 0)}, "
            f"blocks {kinds.get('block', 0)}, overrides {kinds.get('override', 0)}, "
            f"exceptions {kinds.get('exception', 0)}"]
    out += detail
    if kinds.get("override"):
        out.append("  NOTE: an override means a commit landed without matching gate "
                   "evidence. Say so in the report rather than letting it sit in the ledger.")
    out += ["", "## Rollback", f"    git revert --no-edit {oldest}..HEAD"]
    if floor_then != floor_now:
        out.append(f"    # then restore gates/min_test_count.txt to {floor_then}; "
                   f"the floor does not fall on its own")
    out += ["", "## To be written by a person - the tool cannot know these"]
    out += [f"- {p}" for p in SHIP_PROMPTS]
    out += escapes or []
    return "\n".join(out)


def baseline() -> None:
    wl = GATES / "vulture_whitelist.py"
    paths = SRC_DIRS + ([str(wl)] if wl.exists() else [])
    p = run([sys.executable, "-m", "vulture", *paths, *_vulture_exclude(),
             "--min-confidence", "60"])
    if p.returncode not in (0, 3):
        print(f"vulture did not run (exit {p.returncode}); baseline NOT written")
        sys.exit(1)
    keys = sorted(vulture_keys(p.stdout))
    (GATES / "vulture_baseline.txt").write_text("\n".join(keys) + ("\n" if keys else ""), "utf-8")
    # "admin", not "exception". Regenerating a baseline is maintenance, not a
    # human deciding to get past a gate, and only the latter should reach the
    # kill rule.
    ledger_line("admin", "vulture-baseline", f"baseline regenerated: {len(keys)} entries")
    print(f"vulture baseline: {len(keys)} grandfathered findings written")

    # The L002 baseline, same idea and the same reason: let a repo that predates
    # the gate adopt it without either a hundred judgement calls or a hundred
    # scattered noqa comments, then block anything new.
    r = run([sys.executable, "-m", "ruff", "check", "--no-cache", "--isolated",
             "--ignore-noqa",
             "--select", "E722,S110,BLE001", "--output-format", "json", *SRC_DIRS])
    if r.returncode not in (0, 1):
        print(f"ruff did not run (exit {r.returncode}); swallow baseline NOT written")
        sys.exit(1)
    swallows = swallow_keys(r.stdout)
    if swallows is None:
        print("ruff output was not parseable JSON; swallow baseline NOT written")
        sys.exit(1)
    (GATES / "swallow_baseline.txt").write_text(
        format_swallow_baseline(swallows), encoding="utf-8", newline="\n")
    ledger_line("admin", "swallow-baseline",
                f"baseline regenerated: {len(swallows)} sites, "
                f"{sum(swallows.values())} findings")
    print(f"swallow baseline: {sum(swallows.values())} grandfathered findings "
          f"across {len(swallows)} sites")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "pre-commit"
    try:
        if cmd == "pre-commit":
            pre_commit()
        elif cmd == "post-commit":
            post_commit()
        elif cmd == "commit-msg":
            commit_msg(sys.argv[2])
        elif cmd == "report":
            report()
        elif cmd == "baseline":
            baseline()
        elif cmd == "ship":
            ship(sys.argv[2] if len(sys.argv) > 2 else None)
        elif cmd == "pre-push":
            pre_push()
        elif cmd == "scope":
            scope()
        elif cmd == "coverage":
            coverage(seed="--seed-untested" in sys.argv[2:])
        elif cmd == "levels":
            levels()
        else:
            print(f"unknown subcommand {cmd}")
            sys.exit(2)
    except SystemExit:
        raise
    # noqa justified, not silenced: BLE001 exists to catch a handler that
    # swallows a failure and carries on, which is L002. This one does the
    # opposite - it converts ANY unexpected error into a block. Narrowing it to
    # named exception types would let an unanticipated one escape and abort the
    # hook, and a hook that aborts is a hook that does not block. Fail closed is
    # the single property the whole system rests on.
    except Exception as exc:  # noqa: BLE001 - fail CLOSED: an erroring gate blocks
        try:
            LOG.write_text("\n".join(_verbose) + f"\n\nGATE ERROR: {exc!r}", encoding="utf-8")
            ledger_line("block", "gate-error", repr(exc))
        finally:
            print(f"PROOFMARK BLOCK [gate-error] the gate itself failed: {exc!r}")
            print("  a gate that cannot run must fail red, never skip green")
            sys.exit(1)


if __name__ == "__main__":
    main()
