from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from tbot_console.control import ops, paths, procs, state
from tbot_console.control.files import read_object, write_json_atomic
from tbot_console.control.manifest import UnitManifest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="process control is POSIX-only")


def manifest(*, places_orders: bool = False, check: bool = False, **process: Any) -> UnitManifest:
    return UnitManifest.model_validate(
        {
            "id": "alpha",
            "kind": "strategy" if places_orders else "monitor",
            "title": "Альфа",
            "places_orders": places_orders,
            "processes": {
                "main": {
                    "title": "Процесс",
                    "module": "alpha.main",
                    "check": check,
                    "stop_timeout_s": 1,
                    **process,
                }
            },
        }
    )


MONITOR = manifest()
CHECKED = manifest(check=True)
TRADING = manifest(places_orders=True, check=True)


@dataclass
class FakeChild:
    pid: int
    codes: list[int | None] = field(default_factory=list)

    def poll(self) -> int | None:
        return self.codes.pop(0) if self.codes else None


@dataclass
class Machine:
    base: Path
    alive: set[int] = field(default_factory=set)
    deaf: set[int] = field(default_factory=set)
    immortal: set[int] = field(default_factory=set)
    lock_holder: int | None = None
    instances: list[int] = field(default_factory=list)
    argv: dict[int, list[str]] = field(default_factory=dict)
    cwd: dict[int, Path] = field(default_factory=dict)
    stdout: dict[int, Path] = field(default_factory=dict)
    flags: dict[int, bool | None] = field(default_factory=dict)
    check: tuple[bool, dict[str, Any]] = (True, {})
    child: FakeChild | None = None
    http_ok: bool = False
    signals: list[tuple[str, int]] = field(default_factory=list)
    linger: int = 0
    dying: dict[int, int] = field(default_factory=dict)

    def runs(self, pid: int, cwd: Path | None = None) -> None:
        self.alive.add(pid)
        self.argv[pid] = ["python", "-m", "alpha.main"]
        self.cwd[pid] = cwd or self.base

    def owned(self, pid: int | None) -> bool:
        if pid in self.dying:
            if self.dying[pid] == 0:
                self.alive.discard(pid)
                del self.dying[pid]
            else:
                self.dying[pid] -= 1
        return pid in self.alive

    def terminate(self, pid: int) -> None:
        self.signals.append(("TERM", pid))
        if pid not in self.deaf:
            self._doom(pid)

    def kill(self, pid: int) -> None:
        self.signals.append(("KILL", pid))
        if pid not in self.immortal:
            self._doom(pid)

    def _doom(self, pid: int) -> None:
        if self.linger:
            self.dying[pid] = self.linger
        else:
            self.alive.discard(pid)


@dataclass
class FakeClock:
    step: float
    wall: float = 1000.0
    now: float = 0.0

    def monotonic(self) -> float:
        self.now += self.step
        return self.now

    def time(self) -> float:
        return self.wall

    def sleep(self, seconds: float) -> None:
        pass


@pytest.fixture
def machine(root: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    fake = Machine(root)
    monkeypatch.setattr(procs, "owned", lambda base, pid, *_: fake.owned(pid))
    monkeypatch.setattr(procs, "lock_holder", lambda *_, **__: fake.lock_holder)
    monkeypatch.setattr(
        procs,
        "find_instances",
        lambda base, spec, exclude=None: [p for p in fake.instances if p not in (exclude or ())],
    )
    monkeypatch.setattr(procs, "port_holder", lambda port: None)
    monkeypatch.setattr(procs, "cmdline", lambda pid: fake.argv.get(pid, []))
    monkeypatch.setattr(procs, "cwd_of", lambda pid: fake.cwd.get(pid))
    monkeypatch.setattr(procs, "stdout_path", lambda pid: fake.stdout.get(pid))
    monkeypatch.setattr(procs, "create_time", lambda pid: 1.0)
    monkeypatch.setattr(procs, "start_mark", lambda pid: 2.0)
    monkeypatch.setattr(procs, "env_flag", lambda pid, key: fake.flags.get(pid))
    monkeypatch.setattr(procs, "terminate", fake.terminate)
    monkeypatch.setattr(procs, "kill", fake.kill)
    monkeypatch.setattr(procs, "spawn", lambda *_: fake.child)
    monkeypatch.setattr(
        ops, "run_check", lambda *_: ops.CheckResult(fake.check[0], fake.check[1], "")
    )
    monkeypatch.setattr(ops, "_http_ok", lambda port, path: fake.http_ok)
    monkeypatch.setattr(ops, "POLL_S", 0.001)
    monkeypatch.setattr(ops, "KILL_WAIT_S", 0.05)
    monkeypatch.setattr(ops, "ALIVE_GRACE_S", 0.0)
    return fake


def execute(
    base: Path, unit: UnitManifest, action: str, pid: int | None = None
) -> tuple[int, list[str]]:
    lines: list[str] = []
    operation = ops.Operation(base, unit, "main", action, "op-1", echo=lines.append)
    return operation.execute(pid), lines


def op_record(base: Path) -> dict[str, Any]:
    return read_object(paths.op_path(base, "alpha", "main")) or {}


def saved(base: Path) -> dict[str, Any]:
    return read_object(paths.state_path(base, "alpha", "main")) or {}


def running(base: Path, machine: Machine, pid: int = 101) -> None:
    machine.runs(pid)
    write_json_atomic(paths.state_path(base, "alpha", "main"), {"pid": pid, "started_at": 5.0})


def failed_with(base: Path, code: str) -> str:
    record = op_record(base)
    assert (record["phase"], record["error_code"]) == ("failed", code)
    assert record["finished_at"] is not None
    return str(record["error"])


class TestExecute:
    def test_starting_a_running_process_changes_nothing(self, root: Path, machine: Machine):
        running(root, machine)
        assert execute(root, MONITOR, "start") == (
            ops.EXIT_OK,
            ["alpha:main: уже работает (процесс 101)"],
        )
        assert op_record(root) == {}

    def test_adopting_the_process_already_watched_changes_nothing(
        self, root: Path, machine: Machine
    ):
        running(root, machine)
        assert execute(root, MONITOR, "adopt", 101)[0] == ops.EXIT_OK
        assert op_record(root) == {}
        code, lines = execute(root, MONITOR, "adopt", 202)
        assert code == ops.EXIT_FAILED
        assert "уже под присмотром консоли (процесс 101)" in lines[-1]
        assert failed_with(root, "already_running")

    def test_a_done_operation_is_recorded_with_its_message(self, root: Path, machine: Machine):
        code, lines = execute(root, MONITOR, "stop")
        assert (code, lines) == (ops.EXIT_OK, ["alpha:main: уже остановлен"])
        record = op_record(root)
        assert record["phase"] == "done" and record["error"] is None
        assert record["finished_at"] >= record["started_at"]
        assert (record["op_id"], record["action"]) == ("op-1", "stop")

    def test_an_unknown_action_fails(self, root: Path, machine: Machine):
        code, lines = execute(root, MONITOR, "pause")
        assert code == ops.EXIT_FAILED
        assert lines == ["alpha:main: не получилось — неизвестное действие pause"]
        assert failed_with(root, "bad_action") == "неизвестное действие pause"

    def test_an_env_policy_refusal_fails_the_operation(
        self, root: Path, machine: Machine, monkeypatch: pytest.MonkeyPatch
    ):
        def refuse(*_: Any) -> Any:
            raise procs.EnvPolicyError(".env: не читается")

        monkeypatch.setattr(procs, "child_env", refuse)
        assert execute(root, MONITOR, "start")[0] == ops.EXIT_FAILED
        assert failed_with(root, "env_policy") == ".env: не читается"

    def test_an_interrupted_operation_is_recorded_and_the_interrupt_goes_on(
        self, root: Path, machine: Machine, monkeypatch: pytest.MonkeyPatch
    ):
        def interrupted(self: ops.Operation) -> str:
            raise KeyboardInterrupt

        monkeypatch.setattr(ops.Operation, "_stop", interrupted)
        with pytest.raises(KeyboardInterrupt):
            execute(root, MONITOR, "stop")
        assert failed_with(root, "interrupted") == (
            "операция прервана до конца — проверьте состояние процесса"
        )

    def test_a_busy_process_refuses_a_second_operation(self, root: Path, machine: Machine):
        with state.transition_lock(paths.lock_path(root, "alpha", "main")):
            code, lines = execute(root, MONITOR, "stop")
        assert (code, lines) == (
            ops.EXIT_BUSY,
            ["alpha:main: с этим процессом уже идёт другая операция"],
        )
        assert op_record(root) == {}

    def test_without_posix_no_operation_runs(
        self, root: Path, machine: Machine, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(procs, "POSIX", False)
        assert execute(root, MONITOR, "stop") == (
            ops.EXIT_FAILED,
            ["alpha:main: управление процессами работает только на macOS и Linux"],
        )


class TestStop:
    def test_a_stopped_process_stays_stopped(self, root: Path, machine: Machine):
        assert execute(root, MONITOR, "stop")[1] == ["alpha:main: уже остановлен"]
        assert saved(root) == {}

    def test_a_dead_pid_is_cleared(self, root: Path, machine: Machine):
        write_json_atomic(paths.state_path(root, "alpha", "main"), {"pid": 101, "output": "o"})
        assert execute(root, MONITOR, "stop")[1] == ["alpha:main: уже остановлен"]
        assert saved(root) == {"pid": None, "create_time": None, "start_mark": None, "output": "o"}

    def test_a_foreign_process_is_not_stopped(self, root: Path, machine: Machine):
        machine.instances = [303]
        code, lines = execute(root, MONITOR, "stop")
        assert code == ops.EXIT_FAILED
        assert "работает процесс 303, которым консоль не управляет" in lines[-1]
        assert failed_with(root, "foreign")
        assert machine.signals == []

    def test_a_process_that_obeys_sigterm_stops(self, root: Path, machine: Machine):
        running(root, machine)
        code, lines = execute(root, MONITOR, "stop")
        assert (code, machine.signals) == (ops.EXIT_OK, [("TERM", 101)])
        assert lines == [
            "alpha:main: прошу процесс 101 завершиться (SIGTERM)",
            "alpha:main: остановлен (процесс 101)",
        ]
        assert saved(root)["pid"] is None

    def test_a_deaf_monitor_is_killed_after_its_timeout(self, root: Path, machine: Machine):
        running(root, machine)
        machine.deaf.add(101)
        code, lines = execute(root, MONITOR, "stop")
        assert (code, machine.signals) == (ops.EXIT_OK, [("TERM", 101), ("KILL", 101)])
        assert "alpha:main: не остановился вовремя — завершаю принудительно" in lines
        assert saved(root)["pid"] is None

    def test_a_deaf_trading_process_is_never_killed(self, root: Path, machine: Machine):
        running(root, machine)
        machine.deaf.add(101)
        code, _ = execute(root, TRADING, "stop")
        assert (code, machine.signals) == (ops.EXIT_FAILED, [("TERM", 101)])
        assert failed_with(root, "stop_timeout").startswith(
            "процесс 101 не остановился за 1 с. Разбираться руками"
        )
        assert saved(root)["pid"] == 101

    def test_a_process_slow_to_obey_sigterm_is_waited_for_not_killed(
        self, root: Path, machine: Machine
    ):
        running(root, machine)
        machine.linger = 3
        assert execute(root, MONITOR, "stop")[1][-1] == "alpha:main: остановлен (процесс 101)"
        assert machine.signals == [("TERM", 101)]

    def test_after_the_kill_the_stop_waits_for_the_process_to_go(
        self, root: Path, machine: Machine
    ):
        running(root, machine)
        machine.deaf.add(101)
        machine.linger = 3
        assert execute(root, MONITOR, "stop")[0] == ops.EXIT_OK
        assert machine.signals == [("TERM", 101), ("KILL", 101)]
        assert 101 not in machine.alive

    def test_a_process_that_survives_the_kill_is_still_marked_stopped(
        self, root: Path, machine: Machine
    ):
        running(root, machine)
        machine.deaf.add(101)
        machine.immortal.add(101)
        assert execute(root, MONITOR, "stop")[1][-1] == "alpha:main: остановлен (процесс 101)"
        assert saved(root)["pid"] is None


class TestWaitReady:
    def test_a_child_that_dies_fails_the_start_with_its_last_output(
        self, root: Path, machine: Machine
    ):
        machine.child = FakeChild(404, [None, 3])
        output = root / MONITOR.output_path("main")
        output.parent.mkdir(parents=True)
        output.write_text("line one\nboom\n")
        unit = manifest(ready={"kind": "http", "port": 18600})
        code, _ = execute(root, unit, "start")
        assert code == ops.EXIT_FAILED
        assert failed_with(root, "died_on_start") == (
            "процесс завершился при запуске с кодом 3. Последние строки вывода:\nline one\nboom"
        )
        assert saved(root)["pid"] is None

    def test_an_alive_child_is_ready_after_the_grace(self, root: Path, machine: Machine):
        machine.child = FakeChild(404)
        code, lines = execute(root, MONITOR, "start")
        assert (code, lines[-1]) == (ops.EXIT_OK, "alpha:main: запущен, процесс 404")
        assert saved(root)["pid"] == 404 and saved(root)["adopted"] is False

    def test_an_http_child_is_ready_when_it_answers(self, root: Path, machine: Machine):
        machine.child = FakeChild(404)
        machine.http_ok = True
        unit = manifest(ready={"kind": "http", "port": 18600})
        assert execute(root, unit, "start")[1][-1] == "alpha:main: запущен и отвечает, процесс 404"

    def test_a_descriptor_child_is_ready_when_it_names_itself(self, root: Path, machine: Machine):
        machine.child = FakeChild(404)
        machine.check = (True, {"ok": True, "identity": {"descriptor": "state/run.json"}})
        write_json_atomic(root / "state/run.json", {"pid": 404})
        unit = manifest(check=True, ready={"kind": "descriptor"})
        assert execute(root, unit, "start")[1][-1] == "alpha:main: запущен и работает, процесс 404"

    @pytest.mark.parametrize(
        "ready", [{"kind": "http", "port": 18600}, {"kind": "descriptor"}, {"kind": "http"}]
    )
    def test_a_child_that_never_confirms_is_left_running_with_a_note(
        self, root: Path, machine: Machine, monkeypatch: pytest.MonkeyPatch, ready: dict[str, Any]
    ):
        machine.child = FakeChild(404)
        monkeypatch.setattr(ops, "time", FakeClock(step=100))
        unit = manifest(ready={**ready, "timeout_s": 150})
        code, lines = execute(root, unit, "start")
        assert (code, lines[-1]) == (
            ops.EXIT_OK,
            "alpha:main: процесс 404 жив, но не подтвердил готовность за 150 с",
        )

    def test_the_ready_deadline_itself_ends_the_wait(
        self, root: Path, machine: Machine, monkeypatch: pytest.MonkeyPatch
    ):
        machine.child = FakeChild(404, [None, None, None, 3])
        monkeypatch.setattr(ops, "time", FakeClock(step=50))
        unit = manifest(ready={"kind": "http", "port": 18600, "timeout_s": 150})
        assert execute(root, unit, "start")[1][-1] == (
            "alpha:main: процесс 404 жив, но не подтвердил готовность за 150 с"
        )

    @pytest.mark.parametrize(
        ("grace", "last"),
        [
            (0.0, "alpha:main: запущен, процесс 404"),
            (3.0, "alpha:main: процесс 404 жив, но не подтвердил готовность за 150 с"),
        ],
    )
    def test_an_alive_child_is_ready_once_the_grace_has_passed(
        self,
        root: Path,
        machine: Machine,
        monkeypatch: pytest.MonkeyPatch,
        grace: float,
        last: str,
    ):
        machine.child = FakeChild(404)
        monkeypatch.setattr(ops, "time", FakeClock(step=100))
        monkeypatch.setattr(ops, "ALIVE_GRACE_S", grace)
        assert execute(root, manifest(ready={"timeout_s": 150}), "start")[1][-1] == last


class TestAdopt:
    def test_the_only_instance_is_adopted_with_its_output_and_switch(
        self, root: Path, machine: Machine
    ):
        machine.runs(303)
        machine.instances = [303]
        machine.stdout[303] = root / "logs/own.out"
        machine.flags[303] = True
        code, lines = execute(root, MONITOR, "adopt")
        assert (code, lines) == (ops.EXIT_OK, ["alpha:main: принят под присмотр, процесс 303"])
        assert saved(root) == {
            "pid": 303,
            "create_time": 1.0,
            "start_mark": 2.0,
            "started_at": 1.0,
            "output": "logs/own.out",
            "adopted": True,
            "identity": {},
            "live_trading": True,
        }

    def test_an_unknown_stdout_falls_back_to_the_unit_output(self, root: Path, machine: Machine):
        machine.runs(303)
        assert execute(root, MONITOR, "adopt", 303)[0] == ops.EXIT_OK
        assert saved(root)["output"] == "logs/units/alpha.main.out"
        assert saved(root)["live_trading"] is None

    def test_the_lock_holder_from_the_check_is_the_candidate(self, root: Path, machine: Machine):
        machine.runs(303)
        machine.runs(304)
        machine.instances = [303, 304]
        identity = {"lock": "state/a.lock", "lock_holder": 304}
        machine.check = (False, {"ok": False, "identity": identity})
        assert execute(root, CHECKED, "adopt")[1][-1].endswith("процесс 304")
        assert saved(root)["identity"] == identity

    def test_an_unreadable_lock_holder_needs_an_explicit_pid(self, root: Path, machine: Machine):
        machine.runs(303)
        machine.check = (True, {"ok": True, "identity": {"lock": "state/a.lock", "lock_holder": 0}})
        assert execute(root, CHECKED, "adopt")[0] == ops.EXIT_FAILED
        assert "state/a.lock занят процессом, номер которого прочитать не удалось" in (
            failed_with(root, "lock_held")
        )
        assert execute(root, CHECKED, "adopt", 303)[0] == ops.EXIT_OK

    def test_several_instances_need_an_explicit_pid(self, root: Path, machine: Machine):
        machine.instances = [303, 304]
        assert execute(root, MONITOR, "adopt")[0] == ops.EXIT_FAILED
        assert failed_with(root, "ambiguous") == (
            "подходят несколько процессов: 303, 304 — укажите нужный: "
            "`uv run tbot adopt alpha:main --pid N`"
        )

    def test_nothing_to_adopt_fails(self, root: Path, machine: Machine):
        assert execute(root, MONITOR, "adopt")[0] == ops.EXIT_FAILED
        assert failed_with(root, "not_found") == (
            "не нашёл работающий процесс, который можно принять"
        )

    def test_a_process_of_another_module_is_refused(self, root: Path, machine: Machine):
        machine.runs(303)
        machine.argv[303] = ["python", "-m", "beta.main"]
        assert execute(root, MONITOR, "adopt", 303)[0] == ops.EXIT_FAILED
        assert failed_with(root, "mismatch") == (
            "процесс 303 запускает не alpha.main: python -m beta.main"
        )

    def test_a_process_from_another_folder_is_refused(
        self, root: Path, machine: Machine, tmp_path_factory: pytest.TempPathFactory
    ):
        elsewhere = tmp_path_factory.mktemp("elsewhere")
        machine.runs(303, elsewhere)
        assert execute(root, MONITOR, "adopt", 303)[0] == ops.EXIT_FAILED
        assert failed_with(root, "mismatch") == (
            f"процесс 303 работает из другой папки: {elsewhere}"
        )

    def test_a_descriptor_naming_another_process_is_refused(self, root: Path, machine: Machine):
        machine.runs(303)
        machine.check = (True, {"ok": True, "identity": {"descriptor": "state/run.json"}})
        write_json_atomic(root / "state/run.json", {"pid": 909})
        assert execute(root, CHECKED, "adopt", 303)[0] == ops.EXIT_FAILED
        assert failed_with(root, "mismatch") == "дескриптор бота называет процесс 909, а не 303"
        write_json_atomic(root / "state/run.json", {"pid": 303})
        assert execute(root, CHECKED, "adopt", 303)[0] == ops.EXIT_OK
