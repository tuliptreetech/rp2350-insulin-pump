"""
pytest fixtures for the pump scenarios.

Every test gets its own emulator session, freshly booted. That is what makes
the suite safe to run under `pytest -n`, and it also buys real isolation: no
scenario can leave a latched alarm, a drained reservoir or a part-filled
rolling-hour window behind for the next one. The previous sequential harness
had to unwind all of that by hand between tests, and the rolling-hour counter
in particular could not be reset at all without a test-only backdoor into the
firmware's safety limits.
"""

import os

import pytest
from emerson import EmulatorController

from pump import Pump

HOST = os.environ.get("EMERSON_HOST", "http://localhost:10314")
PROJECT = os.environ.get("EMERSON_PROJECT", "rp2350")


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: needs many minutes of wall clock")


def pytest_addoption(parser):
    parser.addoption("--runslow", action="store_true",
                     help="also run scenarios marked slow")


# Roughly longest-first. xdist hands tests out in collection order, so a long
# scenario collected last becomes the tail of the whole run while the other
# workers sit idle. Ordering here rather than in the test file keeps the file
# readable and grouped by subject.
_LONGEST_FIRST = [
    "test_occlusion_suspends",
    "test_under_delivery_detected",
    "test_recovery_requires_the_fault_to_clear",
    "test_bolus_cancel_bills_only_what_moved",
    "test_bolus_delivers",
]


def pytest_collection_modifyitems(config, items):
    rank = {name: i for i, name in enumerate(_LONGEST_FIRST)}
    items.sort(key=lambda item: rank.get(item.name, len(rank)))

    if config.getoption("--runslow"):
        return
    skip = pytest.mark.skip(reason="slow scenario; pass --runslow to include it")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def pump():
    """A freshly booted pump on an emulator session of its own.

    Each worker builds its own Connection: one Connection can only be attached
    to one session at a time, so sharing one across parallel tests would fail
    the second attach.

    Note what is deliberately *not* here - clearing leaked sessions. That has
    to happen once before the run starts (see run_tests.sh); doing it per test
    would stop sibling workers' sessions that are legitimately in flight.
    """
    with EmulatorController(HOST).connect() as conn:
        session_id = conn.run_project(PROJECT)
        try:
            with conn.attach(session_id) as machine:
                p = Pump(machine)
                p.boot()
                yield p
        finally:
            try:
                conn.stop_project(session_id)
            except RuntimeError:
                # A failure inside attach() can leave this Connection unusable.
                # The session still has to go: a leaked one silently stalls the
                # next session on the same project.
                with EmulatorController(HOST).connect() as rescue:
                    rescue.stop_project(session_id)
