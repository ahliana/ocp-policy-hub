"""Proofmark doctor: prove every gate tool resolves to the pinned project env.

Run:  <repo>/.venv-proofmark/Scripts/python.exe gates/doctor.py
Exit 0 only when the interpreter is the gate venv named in proofmark.toml
([proofmark].venv) and every tool version matches [proofmark.pins].
Any mismatch exits 1.

A gate that cannot prove its toolchain must fail red, never skip green. On this
machine that is not theoretical: bare `pytest` and `python -m pytest` resolve to
different interpreters, so a gate that trusted PATH would be checking a
different environment from the one it reports on.

PORTABLE VERSION: reads proofmark.toml rather than pyproject.toml, so installing
gates never edits a config the application already depends on.
"""

import importlib.metadata
import os
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def venv_python(venv: Path) -> Path:
    """The interpreter inside a venv, either layout. Windows proven; the POSIX
    branch keeps a stranger's clone from being wrong by construction."""
    win = venv / "Scripts" / "python.exe"
    return win if win.exists() or os.name == "nt" else venv / "bin" / "python"

# Asks the target interpreter for every requested version in ONE subprocess.
# MISSING is printed by the child rather than raised, so one absent dist does
# not hide the versions of the others.
_VERSIONS_PROBE = (
    "import importlib.metadata as m, sys\n"
    "for d in sys.argv[1:]:\n"
    "    try:\n"
    "        print(d, m.version(d))\n"
    "    except Exception:\n"
    "        print(d, 'MISSING')\n"
)


def app_pin_failures(app_py: str, pins_app: dict) -> list:
    """Prove [proofmark.pins.app] against the interpreter that runs the suite.

    The main pins table describes the GATE venv (this interpreter). Tools the
    suite itself needs - pytest-cov, pytest-socket - live in the APP venv,
    which doctor previously never proved. An unproven toolchain is how a
    version drifts for weeks while every report says green.
    """
    if not pins_app:
        return []
    import subprocess
    r = subprocess.run([app_py, "-c", _VERSIONS_PROBE, *pins_app],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return [f"app venv version probe failed: {r.stderr.strip()[:200]}"]
    have = dict(line.split(None, 1) for line in r.stdout.splitlines() if line.strip())
    failures = []
    for dist, pin in pins_app.items():
        got = have.get(dist, "MISSING")
        print(f"app {dist:14}: {got}  (pin {pin})")
        if got != pin:
            failures.append(f"app venv {dist} {got} != pinned {pin}")
    return failures


def main() -> int:
    cfg = ROOT / "proofmark.toml"
    if not cfg.exists():
        print(f"DOCTOR: FAIL - no proofmark.toml at {cfg}")
        return 1
    proofmark = tomllib.loads(cfg.read_text("utf-8"))["proofmark"]
    pins = proofmark["pins"]
    failures = []

    exe = Path(sys.executable).resolve()
    venv = (ROOT / proofmark.get("venv", ".venv-proofmark")).resolve()
    print(f"interpreter   : {exe}")
    if venv not in exe.parents:
        failures.append(f"interpreter is NOT the gate venv ({venv})")

    py = f"{sys.version_info.major}.{sys.version_info.minor}"
    print(f"python        : {py}  (pin {pins['python']})")
    if py != pins["python"]:
        failures.append(f"python {py} != pinned {pins['python']}")

    for dist in ("pytest", "ruff", "vulture", "pytest-socket"):
        try:
            have = importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            failures.append(f"{dist} is not installed in this venv")
            print(f"{dist:14}: MISSING  (pin {pins[dist]})")
            continue
        print(f"{dist:14}: {have}  (pin {pins[dist]})")
        if have != pins[dist]:
            failures.append(f"{dist} {have} != pinned {pins[dist]}")

    app = proofmark.get("app_venv")
    pins_app = pins.get("app", {})
    if app:
        app_py = venv_python(ROOT / app)
        print(f"app venv      : {app_py}")
        if not app_py.exists():
            failures.append(f"app_venv python missing: {app_py}")
        else:
            import subprocess
            r = subprocess.run([str(app_py), "-c", "import pytest, sys; print(pytest.__version__)"],
                               capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                failures.append(f"app_venv cannot import pytest: {r.stderr.strip()[:200]}")
            else:
                print(f"app pytest    : {r.stdout.strip()}  (runs the suite; NOT pinned here)")
            failures += app_pin_failures(str(app_py), pins_app)
    elif pins_app:
        # No separate app venv (Proofmark itself): the suite runs in THIS
        # interpreter, so the app pins are proven right here.
        failures += app_pin_failures(sys.executable, pins_app)

    if failures:
        print("\nDOCTOR: FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nDOCTOR: OK - all tools resolve to the pinned project environment")
    return 0


if __name__ == "__main__":
    sys.exit(main())
