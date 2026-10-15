from __future__ import annotations

import os
import sys

import psutil
import pytest

from tbot_console.control import procs

macos = pytest.mark.skipif(sys.platform != "darwin", reason="psutil shifts this only on macOS")
linux = pytest.mark.skipif(sys.platform != "linux", reason="boot time comes from /proc/stat")


def step_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform == "darwin":
        from psutil import _psosx

        monkeypatch.setattr(_psosx, "INIT_BOOT_TIME", psutil.boot_time() - 30.0)
    else:
        from psutil import _pslinux

        moved = _pslinux.boot_time() + 2.0
        monkeypatch.setattr(_pslinux, "boot_time", lambda: moved)


@pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="no kernel start time")
class TestClockStep:
    def test_wall_create_time_moves_with_the_clock(self, monkeypatch):
        recorded = procs.create_time(os.getpid())
        assert recorded is not None
        step_clock(monkeypatch)
        assert psutil.Process(os.getpid()).create_time() != pytest.approx(recorded, abs=1.0)

    def test_start_mark_survives_a_clock_step(self, monkeypatch):
        mark = procs.start_mark(os.getpid())
        assert mark is not None
        step_clock(monkeypatch)
        assert procs.start_mark(os.getpid()) == mark
        assert procs.is_alive(os.getpid(), 0.0, mark)

    def test_a_different_mark_is_another_process(self):
        mark = procs.start_mark(os.getpid())
        assert mark is not None
        assert not procs.is_alive(os.getpid(), None, mark + 1.0)

    @macos
    def test_a_record_without_a_mark_survives_a_clock_step_on_macos(self, monkeypatch):
        recorded = procs.create_time(os.getpid())
        step_clock(monkeypatch)
        assert procs.is_alive(os.getpid(), recorded)

    @linux
    def test_a_record_without_a_mark_still_compares_wall_time_on_linux(self):
        recorded = procs.create_time(os.getpid())
        assert recorded is not None
        assert procs.is_alive(os.getpid(), recorded)
        assert not procs.is_alive(os.getpid(), recorded + 1.0)
