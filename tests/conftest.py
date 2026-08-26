"""Shared test fixtures.

src/api/app.py loads the project .env at import time (override=True), so a
developer's real credentials leak into the test process and break tests that
assume a clean environment (the admin gate flips on; Sheets/keys look
configured). Strip the ambient config by default; tests that need a value set
it themselves via monkeypatch.setenv.
"""

import sys
from pathlib import Path

import pytest

# Env vars a developer may have in .env that tests assume are unset unless the
# test sets them explicitly. Keep this list to config that changes behavior.
_AMBIENT_ENV = (
    "ADMIN_TOKEN",
    "SPREADSHEET_ID",
    "GOOGLE_CREDENTIALS",
)


@pytest.fixture(autouse=True)
def _no_ambient_env(monkeypatch):
    for name in _AMBIENT_ENV:
        monkeypatch.delenv(name, raising=False)

# Proofmark size taxonomy: importing the autouse fixture registers it
# suite-wide. The plugin lives in gates/ (distributed file, never edited here),
# so gates/ must be on sys.path before the import - hence the noqa'd position.
_pm_gates = str(Path(__file__).resolve().parents[1] / "gates")
if _pm_gates not in sys.path:
    sys.path.insert(0, _pm_gates)
from proofmark_sizes import _proofmark_size_guard  # noqa: E402,F401
