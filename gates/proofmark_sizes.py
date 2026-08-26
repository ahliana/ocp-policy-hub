"""Proofmark size taxonomy - distributed file, edit only in canonical Proofmark.

Sizes are ENFORCED, levels are REPORTED. A test marked `small` claims it is
hermetic - no I/O, no network, no sleep, no clock - and this fixture makes the
network half of that claim mechanical: a small test that opens a socket fails
with SocketBlockedError instead of quietly depending on the weather.

Wire-up (done by the ring install, one line in the repo's tests/conftest.py):

    from proofmark_sizes import _proofmark_size_guard  # noqa: F401

DELIBERATE deviation from REPORT.md F9, which auto-marked every unmarked test
as `small`: that was right for a greenfield repo and is mass-labelling for a
retrofit one. A wrong `small` on a test that quietly needs the network is a
false failure, and false failures are how gates get switched off. Here
unmarked tests are grandfathered - legal, unenforced, and counted by
`gate.py levels`; the count is what should shrink, one honest label at a time.

`medium` and the level markers (unit/integration/e2e) are declarations only in
Phase B: the localhost allowlist for medium waits on verifying pytest-socket's
--allow-hosts semantics (REPORT.md bucket 2), and levels are a judgement no
program can referee.
"""

import pytest

# The fixture is consumed by a `from proofmark_sizes import ...` line in each
# repo's test conftest, which vulture (scoped to gates/) cannot see.
__all__ = ["_proofmark_size_guard"]

try:
    import pytest_socket
except ImportError:  # enforced per-test below - never silently off
    pytest_socket = None


@pytest.fixture(autouse=True)
def _proofmark_size_guard(request):
    if request.node.get_closest_marker("small"):
        if pytest_socket is None:
            # Fail the CLAIMING test rather than skip: a `small` marker that
            # cannot be enforced reporting green is absence dressed as success.
            pytest.fail(
                "test is marked `small` but pytest-socket is not installed in "
                "the venv running it, so the no-network claim cannot be "
                "enforced. Install pytest-socket (see [proofmark.pins.app]) "
                "or remove the marker."
            )
        pytest_socket.disable_socket()
    yield
    if pytest_socket is not None:
        pytest_socket.enable_socket()
