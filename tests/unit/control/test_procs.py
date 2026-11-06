from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psutil
import pytest

from tbot_console.control import procs
from tbot_console.control.manifest import ProcessSpec

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


class TestRepoFolder:
    def test_the_repo_and_its_subfolders_count_as_its_own(self, tmp_path: Path):
        repo = tmp_path / "Repo"
        (repo / "data" / "deep").mkdir(parents=True)
        assert procs.in_repo(repo, repo)
        assert procs.in_repo(repo / "data" / "deep", repo)
        assert procs.in_repo(None, repo)

    def test_a_folder_outside_the_repo_is_not_its_own(self, tmp_path: Path):
        repo = tmp_path / "Repo"
        copy = repo / "copy"
        copy.mkdir(parents=True)
        assert not procs.in_repo(tmp_path, repo)
        assert not procs.in_repo(repo, copy)
        assert not procs.in_repo(repo, tmp_path / "missing")

    def test_a_folder_is_compared_by_identity_not_by_spelling(self, tmp_path: Path):
        repo = tmp_path / "Repo"
        repo.mkdir()
        (tmp_path / "link").symlink_to(repo)
        assert procs.in_repo(repo, tmp_path / "link")
        twin = tmp_path / "repo"
        if twin.exists():
            assert procs.in_repo(repo, twin)


@pytest.fixture
def listener() -> Iterator[int]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        yield int(server.getsockname()[1])


def lsof_answer(stdout: str, code: int = 0) -> Callable[..., subprocess.CompletedProcess[str]]:
    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert argv[0] == "lsof" and argv[2].startswith("-tiTCP:")
        assert kwargs["timeout"] == 5
        done = subprocess.CompletedProcess(argv, code, stdout, "")
        if kwargs.get("check"):
            done.check_returncode()
        return done

    return run


def lsof_fails(error: BaseException) -> Callable[..., subprocess.CompletedProcess[str]]:
    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise error

    return run


def connections(*found: Any) -> Callable[..., list[Any]]:
    def net_connections(kind: str) -> list[Any]:
        assert kind == "inet"
        return list(found)

    return net_connections


def conn(port: int, pid: int | None, status: str = psutil.CONN_LISTEN) -> Any:
    address = SimpleNamespace(port=port) if port else None
    return SimpleNamespace(status=status, laddr=address, pid=pid)


@pytest.mark.skipif(sys.platform == "win32", reason="the console looks up ports with lsof")
class TestPortHolder:
    def test_a_closed_port_has_no_holder(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(subprocess, "run", lsof_fails(AssertionError("lsof never runs")))
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            closed = int(probe.getsockname()[1])
        assert procs.port_holder(closed) is None

    def test_the_listener_is_this_process(self, listener: int):
        assert procs.port_holder(listener) == os.getpid()

    def test_lsof_names_the_first_pid_it_prints(
        self, listener: int, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(subprocess, "run", lsof_answer("warning\n4242\n4343\n"))
        assert procs.port_holder(listener) == 4242

    @pytest.mark.parametrize(
        "failure",
        [
            lsof_answer("", code=1),
            lsof_answer("no pid here\n"),
            lsof_fails(OSError("no lsof")),
            lsof_fails(subprocess.TimeoutExpired("lsof", 5)),
        ],
    )
    def test_without_an_answer_from_lsof_the_socket_table_decides(
        self, listener: int, monkeypatch: pytest.MonkeyPatch, failure: Callable[..., Any]
    ):
        monkeypatch.setattr(subprocess, "run", failure)
        monkeypatch.setattr(
            psutil,
            "net_connections",
            connections(
                conn(listener, 1, psutil.CONN_ESTABLISHED),
                conn(0, 2),
                conn(listener + 1, 3),
                conn(listener, 5151),
                conn(listener, 6161),
            ),
        )
        assert procs.port_holder(listener) == 5151

    def test_a_listener_without_a_known_pid_reads_as_none(
        self, listener: int, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(subprocess, "run", lsof_answer(""))
        monkeypatch.setattr(psutil, "net_connections", connections(conn(listener, None)))
        assert procs.port_holder(listener) is None

    @pytest.mark.parametrize("error", [psutil.AccessDenied(), OSError("refused")])
    def test_an_unreadable_socket_table_reads_as_no_holder(
        self, listener: int, monkeypatch: pytest.MonkeyPatch, error: BaseException
    ):
        monkeypatch.setattr(subprocess, "run", lsof_answer(""))

        def refused(kind: str) -> list[Any]:
            raise error

        monkeypatch.setattr(psutil, "net_connections", refused)
        assert procs.port_holder(listener) is None

    def test_no_listener_in_the_socket_table_reads_as_no_holder(
        self, listener: int, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(subprocess, "run", lsof_answer(""))
        monkeypatch.setattr(psutil, "net_connections", connections())
        assert procs.port_holder(listener) is None


class TestLockHolder:
    def test_the_live_pid_in_the_lock_running_the_module_holds_it(self, tmp_path: Path):
        holder = subprocess.Popen(
            [sys.executable, "-m", "timeit", "-n", "1", "-r", "1", "import time; time.sleep(30)"]
        )
        try:
            deadline = time.monotonic() + 10
            while "timeit" not in procs.cmdline(holder.pid) and time.monotonic() < deadline:
                time.sleep(0.02)
            lock = tmp_path / "unit.lock"
            lock.write_text(f" {holder.pid}\n")
            assert procs.lock_holder(lock, "timeit") == holder.pid
            assert procs.lock_holder(lock, "alpha.main") is None
        finally:
            holder.kill()
            holder.wait()

    def test_a_missing_or_garbled_lock_has_no_holder(self, tmp_path: Path):
        lock = tmp_path / "unit.lock"
        assert procs.lock_holder(lock, "pytest") is None
        lock.write_text("pid=12")
        assert procs.lock_holder(lock, "pytest") is None

    def test_a_dead_pid_in_the_lock_has_no_holder(self, tmp_path: Path):
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        lock = tmp_path / "unit.lock"
        lock.write_text(str(gone.pid))
        assert procs.lock_holder(lock, "pytest") is None


@dataclass
class FakeProcess:
    pid: int
    argv: list[str] | BaseException
    folder: str | BaseException = ""
    env: dict[str, str] | BaseException = field(default_factory=dict)

    def cmdline(self) -> list[str]:
        return self._answer(self.argv)

    def cwd(self) -> str:
        return self._answer(self.folder)

    def environ(self) -> dict[str, str]:
        return self._answer(self.env)

    @staticmethod
    def _answer(value: Any) -> Any:
        if isinstance(value, BaseException):
            raise value
        return value


class TestFindInstances:
    SPEC = ProcessSpec(title="Процесс", module="alpha.main", env={"TBOT_MODE": "paper"})

    @pytest.fixture
    def table(self, monkeypatch: pytest.MonkeyPatch) -> list[FakeProcess]:
        found: list[FakeProcess] = []
        monkeypatch.setattr(psutil, "process_iter", lambda: iter(found))
        monkeypatch.setattr(procs, "console_scripts", lambda module, base=None: {"alpha-live"})
        return found

    def test_a_python_running_the_module_in_the_repo_is_an_instance(
        self, tmp_path: Path, table: list[FakeProcess]
    ):
        (tmp_path / "data").mkdir()
        table += [
            FakeProcess(30, ["/venv/bin/python3.15", "-m", "alpha.main"], str(tmp_path / "data")),
            FakeProcess(20, ["python", "/venv/bin/alpha-live", "--x"], str(tmp_path)),
            FakeProcess(10, ["python", "-m", "alpha.main"], str(tmp_path), {"TBOT_MODE": "paper"}),
        ]
        assert procs.find_instances(tmp_path, self.SPEC) == [10, 20, 30]
        assert procs.find_instances(tmp_path, self.SPEC, {20}) == [10, 30]

    def test_this_process_is_never_its_own_instance(self, tmp_path: Path, table: list[FakeProcess]):
        table.append(FakeProcess(os.getpid(), ["python", "-m", "alpha.main"], str(tmp_path)))
        assert procs.find_instances(tmp_path, self.SPEC) == []

    @pytest.mark.parametrize(
        "argv",
        [
            [],
            ["python", "-m", "alpha.main", "--check"],
            ["/bin/sh", "-m", "alpha.main"],
            ["python", "-m", "beta.main"],
            ["python", "alpha.main"],
            psutil.AccessDenied(),
            psutil.NoSuchProcess(1),
            OSError("gone"),
            SystemError("returned a result with an exception set"),
        ],
    )
    def test_anything_else_is_not_an_instance(
        self, tmp_path: Path, table: list[FakeProcess], argv: Any
    ):
        table.append(FakeProcess(10, argv, str(tmp_path)))
        assert procs.find_instances(tmp_path, self.SPEC) == []

    @pytest.mark.parametrize("folder", ["", "OUTSIDE", psutil.AccessDenied(), OSError("gone")])
    def test_a_python_elsewhere_or_of_unknown_folder_is_not_an_instance(
        self, tmp_path: Path, table: list[FakeProcess], folder: Any
    ):
        outside = tmp_path.parent / f"{tmp_path.name}-other"
        outside.mkdir()
        where = str(outside) if folder == "OUTSIDE" else folder
        table.append(FakeProcess(10, ["python", "-m", "alpha.main"], where))
        assert procs.find_instances(tmp_path, self.SPEC) == []

    def test_a_contradicting_environment_rules_it_out_and_an_unreadable_one_does_not(
        self, tmp_path: Path, table: list[FakeProcess]
    ):
        argv = ["python", "-m", "alpha.main"]
        table += [
            FakeProcess(10, argv, str(tmp_path), {"TBOT_MODE": "live"}),
            FakeProcess(20, argv, str(tmp_path), psutil.AccessDenied()),
            FakeProcess(30, argv, str(tmp_path), {"OTHER": "1"}),
        ]
        assert procs.find_instances(tmp_path, self.SPEC) == [20, 30]
        plain = ProcessSpec(title="Процесс", module="alpha.main")
        assert procs.find_instances(tmp_path, plain) == [10, 20, 30]
