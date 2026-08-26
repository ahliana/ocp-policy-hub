"""Scope completeness: do the configured roots actually cover the repo?

`check_layout` in gate.py proves the directories named in proofmark.toml EXIST.
Nothing proved they were COMPLETE, and those are two different failures that
produce the same green result:

    a scan of NOTHING reporting green   - closed by check_layout
    a scan of SOME   reporting green    - closed by this

The second one was live. Measured across the four installs on 2026-08-04:

    voxcast     src_dirs = ["src"]                              covers the tree
    Tad         src_dirs = ["backend"]                          covers the tree
    WineCellar  src_dirs = ["intake_app", "intake", "scripts"]  covers the tree
    Reachly     src_dirs = ["backend/reachly"]                  leaves backend/scripts out

Four repos, four different shapes, and nothing could tell which one was wrong.
Reachly's uncovered directory holds its production data migrations - the only
code in that repo that irreversibly rewrites live rows, and the least gated code
in it. Inverted risk weighting, arrived at by nobody deciding anything:
`install.py` takes `--src` as a hand-typed argument and writes it down verbatim.

The industry name for the failure is **silent scope reduction**. The control
reports on the subset it was pointed at and says nothing about the remainder, so
its output is indistinguishable from full coverage.

This module is the pure half, deliberately separate from gate.py so the same
implementation runs at commit time AND at install time. Two copies of a coverage
rule drift, and the copy that drifts is the one nobody is watching.
"""

import fnmatch
from pathlib import Path

# The gate's own code, in a repo that did not write it. Copied in by install.py,
# version-controlled in the Proofmark repo, and never that repo's source.
#
# The exclusion is about ownership, not quality. Proofmark now gates itself with
# src_dirs = ["gates"], so this code IS scanned - just in the one repo that can
# act on the findings. Everywhere else, folding it into src_dirs would make ruff
# report BLE001 on main()'s deliberate catch-all `except Exception` in every
# install, leaving five repos each carrying a suppression for a file they do not
# own and cannot fix. Fix it once, upstream.
#
# An earlier version of this comment also claimed vulture reports every
# subcommand in gate.py as unwired. That was wrong, and self-gating is what
# disproved it: `main()` calls `pre_commit()`, `report()` and the rest by name,
# so vulture sees them used. Measured across the whole gates/ tree, the real
# count is one BLE001 and one dead ternary. Corrected rather than deleted,
# because a justification nobody rechecked is how a carve-out outlives its
# reason.
#
# Printed by `gate.py scope` rather than applied invisibly. A built-in carve-out
# nobody can see is the same defect this module exists to catch.
BUILTIN_EXCLUDE = {
    "gates/*": "Proofmark's own gate code, versioned in the Proofmark repo",
}

# Code that Phase 1 cannot read anywhere in the repo, inside a configured root
# or outside one. Every gate in the ring is a Python tool - ruff, vulture and
# pytest - so a root "covering" a directory only ever meant its .py files.
#
# Counted repo-wide rather than only outside the roots, and the first draft of
# this module got that wrong in a way worth recording: Reachly's index.html
# lives under backend/reachly, so it was reported as covered while nothing on
# earth was reading it. That is silent scope reduction, committed by the module
# written to detect silent scope reduction. Measured: 3,971 lines and 260 JS
# functions, holding the payload-shaping logic behind the S1 cadence defect.
#
# Deliberately not markdown, JSON, YAML or config. Those are not source, and an
# advisory that reports 17 .md files is one nobody finishes reading.
UNGATEABLE_CODE_SUFFIXES = {
    ".html", ".htm", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx",
    ".css", ".scss", ".sql", ".sh", ".ps1", ".psm1", ".bat",
}


def norm(rel: str) -> str:
    """Repo-relative path in one shape, whatever the OS or the config wrote."""
    return rel.replace("\\", "/").strip("/")


def validate_excludes(raw) -> dict[str, str]:
    """Parse [proofmark.exclude], which is a table of pattern = "reason".

    A table rather than a list, on purpose. A bare list of paths records WHAT
    was carved out and never WHY, so the reason lives in whoever's head typed
    it and the carve-out outlives the situation that justified it. Requiring a
    sentence is the cheapest available defence against a permanent exception.

    Raises ValueError; the caller decides whether that is a SystemExit (gate) or
    an argparse error (installer).
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(
            "[proofmark.exclude] must be a table of pattern = \"reason\", got "
            f"{type(raw).__name__}. A list records what was carved out and never why."
        )
    for pattern, reason in raw.items():
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(
                f"exclusion {pattern!r} has no reason. Every carve-out states why, "
                "or it outlives the situation that justified it."
            )
    return {**BUILTIN_EXCLUDE, **{norm(k): reason for k, reason in raw.items()}}


def findings(tracked, src_rels, tests_rel, excludes):
    """What the configured roots do not cover.

    Returns (uncovered_py, stale_exclusions, empty_roots, ungateable_code) where
    ungateable_code maps a file extension to a count - reported, never blocked,
    and counted across the whole index rather than only outside the roots,
    because no Phase 1 gate reads those files in either place.

    `empty_roots` is the mirror image of an uncovered directory: a root declared
    in proofmark.toml that holds no tracked file at all. check_layout only asks
    whether the directory EXISTS ON DISK, which a gitignored runtime directory
    does. Found on WineCellar, whose src_dirs listed `intake` - a phone-capture
    data folder, gitignored, zero Python, passed to a hand-typed --src and never
    questioned again. Harmless there by luck. The same typo against a directory
    that did hold code would have pointed ruff and vulture at files git cannot
    see, making the gate's verdict depend on untracked local state.

    `tracked` is the git index, not a filesystem walk. That matters three ways:
    the index is exactly the set of files that can be committed, it already
    omits .venv and build output with no ignore list to maintain, and a newly
    staged file is already in it - so an uncovered directory fails on the commit
    that introduces it rather than whenever somebody next thinks to look.

    Patterns are matched with fnmatchcase, so `*` crosses directory separators
    (`backend/scripts/*` covers `backend/scripts/sub/x.py`) and matching does not
    change meaning between Windows and Linux.
    """
    roots = [norm(d) for d in src_rels] + [norm(tests_rel)]
    uncovered_py: list[str] = []
    ungateable: dict[str, int] = {}
    used: set[str] = set()
    populated: set[str] = set()

    for entry in tracked:
        rel = norm(entry)
        if not rel:
            continue
        suffix = Path(rel).suffix.lower()
        if suffix in UNGATEABLE_CODE_SUFFIXES:
            ungateable[suffix] = ungateable.get(suffix, 0) + 1
        covering = [r for r in roots if rel == r or rel.startswith(r + "/")]
        if covering:
            populated.update(covering)
            continue
        hit = next((p for p in excludes if fnmatch.fnmatchcase(rel, p)), None)
        if hit is not None:
            used.add(hit)
            continue
        if rel.endswith(".py"):
            uncovered_py.append(rel)

    # An exclusion matching nothing is dead config, and dead config in a carve-out
    # list is worse than dead code: it reads as a live reason to skip something.
    # Built-ins are exempt because a fresh install has not staged gates/ yet.
    stale = sorted(p for p in excludes if p not in used and p not in BUILTIN_EXCLUDE)
    empty_roots = sorted(set(roots) - populated)
    return sorted(uncovered_py), stale, empty_roots, ungateable


def uncovered_message(uncovered, src_rels, tests_rel) -> str:
    """The block text. Names both ways out, because a gate that only says no
    teaches nothing and gets bypassed on the second occurrence."""
    shown = "\n".join(f"    {p}" for p in uncovered[:12])
    more = f"\n    ... and {len(uncovered) - 12} more" if len(uncovered) > 12 else ""
    return (
        f"{len(uncovered)} Python file(s) sit outside every configured root.\n"
        f"  src_dirs = {list(src_rels)}, tests_dir = {tests_rel!r}\n"
        f"{shown}{more}\n"
        "  These are linted by nothing, scanned for unwired code by nothing, and\n"
        "  can be committed without a test. Two ways to resolve, both honest:\n"
        "    1. widen src_dirs in proofmark.toml to include them (usually right)\n"
        "    2. declare them under [proofmark.exclude] with a reason:\n"
        "         [proofmark.exclude]\n"
        "         \"path/or/glob/*\" = \"why this is not application source\"\n"
    )
