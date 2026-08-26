"""Bootstrap a fresh CLONE of a gated repo. Distributed file - edit in canonical.

    python gates/wire.py

Git does not clone .git/hooks, so a clone of a gated repo holds the gate files
and ZERO enforcement, silently - every commit sails through and nothing says
so. This is the one documented command that fixes it: create the gate venv,
install the pinned tools named in proofmark.toml, wire the four hook shims,
and prove the toolchain with doctor. Idempotent; touches no baseline, no
config, no source.

Requires the Python version the repo pins ([proofmark.pins].python) - it says
so up front rather than letting doctor refuse every commit afterwards.

Proven end-to-end on Windows (verify_install.py's stranger-clone proof). The
POSIX paths - bin/ layout, hook executable bits, layout-probing shims - are
implemented and untested on a real POSIX machine; the cold-network pip path
has also never run (the proof pre-seeds the venv to stay offline). Stated
here so absence of proof is never mistaken for proof.

This file exists because the first stranger-clone proof failed: the bootstrap
lived in install.py, which is NOT distributed - so the documented command was
impossible in every repo except canonical itself.
"""

import os
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def sh(args, cwd, check=True):
    r = subprocess.run([str(a) for a in args], cwd=str(cwd),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if check and r.returncode != 0:
        raise SystemExit(f"command failed ({r.returncode}): {args}\n"
                         f"{(r.stdout + r.stderr).strip()[-800:]}")
    return r


def venv_python(venv: Path) -> Path:
    """The interpreter a venv will hold, by platform - the venv may not exist yet."""
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def hooks_dir(repo: Path) -> Path:
    """Where the hooks live. `.git` is a FILE in a worktree, so ask git there
    rather than guessing - writing hooks into a path that is not a directory
    was a NotADirectoryError traceback for anyone using worktrees."""
    dotgit = repo / ".git"
    if dotgit.is_dir():
        return dotgit / "hooks"
    import shutil
    git = shutil.which("git")
    if not git:
        raise SystemExit(f"{dotgit} is not a directory (a worktree?) and git is "
                         "not on PATH to resolve the real hooks path")
    r = sh([git, "-C", repo, "rev-parse", "--git-path", "hooks"], repo)
    return (repo / r.stdout.strip()).resolve()


def write_hook_shims(repo: Path, vpy_abs: str | None = None) -> None:
    """The four hook shims, LF endings, executable on POSIX. Repo-relative by
    default via `git rev-parse --show-toplevel` so the repo can move; an
    external venv (verify_install's reuse) has to be absolute. The shim probes
    both venv layouts because the shim is written once and the clone may later
    be bootstrapped on a different platform than the one that wired it."""
    hooks = hooks_dir(repo)
    hooks.mkdir(parents=True, exist_ok=True)
    if vpy_abs:
        resolve = f'py="{Path(vpy_abs).as_posix()}"\n'
    else:
        resolve = (
            'd="$(git rev-parse --show-toplevel)"\n'
            'py="$d/.venv-proofmark/Scripts/python.exe"\n'
            '[ -x "$py" ] || py="$d/.venv-proofmark/bin/python"\n'
        )
    gp = '"$(git rev-parse --show-toplevel)/gates/gate.py"'
    shims = {
        "pre-commit": f"# Proofmark commit gate. Break-glass for a human: git commit --no-verify (logged by post-commit).\n{resolve}exec \"$py\" {gp} pre-commit\n",
        "post-commit": f"# Proofmark evidence binding. Logs an override line for any commit without matching gate evidence.\n{resolve}exec \"$py\" {gp} post-commit\n",
        "commit-msg": f"# Proofmark never-fix-twice plus escape-log capture.\n{resolve}exec \"$py\" {gp} commit-msg \"$1\"\n",
        # Push is the ship boundary. Everything before this point was prose
        # someone had to remember; this is where the end-of-work report stops
        # depending on memory.
        "pre-push": f"# Proofmark: refuse a push whose commits have no filled-in report.\n{resolve}exec \"$py\" {gp} pre-push\n",
    }
    for name, body in shims.items():
        target = hooks / name
        target.write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
        if os.name != "nt":
            # git silently IGNORES a non-executable hook on POSIX - the exact
            # silent-open hazard this file exists to close.
            os.chmod(target, 0o755)
        print(f"  {name}")


def main() -> int:
    cfg = ROOT / "proofmark.toml"
    if not cfg.exists():
        raise SystemExit("no proofmark.toml at the repo root - this repo is not "
                         "gated. A fresh gating is canonical's install.py, not wire.")
    pins = tomllib.loads(cfg.read_text("utf-8"))["proofmark"]["pins"]
    flat = {k: v for k, v in pins.items() if isinstance(v, str) and k != "python"}

    print("== wire 1/3: gate venv ==")
    venv = ROOT / ".venv-proofmark"
    vpy = venv_python(venv)
    if not vpy.exists():
        # Check BEFORE creating: the venv inherits this interpreter's version,
        # and a mismatch means doctor refuses every commit with no hint that
        # the problem started here.
        running = f"{sys.version_info.major}.{sys.version_info.minor}"
        pinned = pins.get("python")
        if pinned and running != pinned:
            raise SystemExit(
                f"this repo pins python {pinned} and you are running {running}. "
                f"Re-run wire.py with python {pinned} - a venv built from "
                f"{running} would be refused by doctor on every commit.")
        sh([sys.executable, "-m", "venv", venv], ROOT)
        print(f"  created {venv} from {sys.executable}")
    sh([vpy, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
        *[f"{k}=={v}" for k, v in flat.items()]], ROOT)
    print(f"  pinned tools present: {', '.join(sorted(flat))}")

    print("== wire 2/3: hook shims ==")
    write_hook_shims(ROOT)

    print("== wire 3/3: doctor proves it ==")
    r = sh([vpy, ROOT / "gates" / "doctor.py"], ROOT, check=False)
    print(r.stdout)
    if r.returncode != 0:
        if r.stderr.strip():
            print(r.stderr)      # a doctor traceback lives here, not in stdout
        print("doctor FAILED - read its lines above. The usual gap on a fresh "
              "clone: the APP venv and its [proofmark.pins.app] tools, which "
              "this command deliberately does not create for you.")
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
