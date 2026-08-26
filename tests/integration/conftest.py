"""Everything under tests/integration/ carries the `large` size marker.

These are the full-pipeline suites - slow by nature (~45s for the tier).
`large` tests sit out the pre-commit gate's fast run and still run in full
at push (see the Proofmark block in CLAUDE.md). Marking here, at the
directory level, keeps the label from drifting file by file.

pytest_collection_modifyitems is a session-scope hook even when defined in
a subdirectory conftest - it receives every collected item - so the filter
on this directory's path is what scopes the marker, not the file location.
"""

from pathlib import Path

import pytest

_HERE = Path(__file__).parent


def pytest_collection_modifyitems(items):
    for item in items:
        if _HERE in Path(item.fspath).parents:
            item.add_marker(pytest.mark.large)
