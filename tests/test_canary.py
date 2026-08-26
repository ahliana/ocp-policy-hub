"""The canary pair. A run that cannot show a failure proves nothing.

If the collection floor, the runner or the reporting ever breaks in a way that
turns a real failure into a silent pass, these two tests say so: the gate
requires the literal string "1 passed, 1 xfailed" and nothing else satisfies it.
Do not delete, do not mark skip, do not "fix" the xfail.
"""

import pytest


def test_canary_passes():
    assert True


@pytest.mark.xfail(strict=True, reason="proves a failing test is still reported")
def test_canary_fails():
    assert False
