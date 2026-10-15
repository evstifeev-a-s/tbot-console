from __future__ import annotations

import os
import sys

import psutil
import pytest

from tbot_console.control import procs

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="psutil adjusts only on macOS")


def step_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    from psutil import _psosx

    monkeypatch.setattr(_psosx, "INIT_BOOT_TIME", psutil.boot_time() - 30.0)


def test_identity_survives_a_clock_step_after_the_console_started(monkeypatch):
    recorded = procs.create_time(os.getpid())
    assert recorded is not None
    step_clock(monkeypatch)
    assert psutil.Process(os.getpid()).create_time() != pytest.approx(recorded, abs=1.0)
    assert procs.is_alive(os.getpid(), recorded)


def test_create_time_does_not_move_with_the_clock(monkeypatch):
    before = procs.create_time(os.getpid())
    step_clock(monkeypatch)
    assert procs.create_time(os.getpid()) == before
