from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from tbot_console.control import live, paths, procs, state
from tbot_console.control.files import write_json_atomic
from tbot_console.control.manifest import UnitManifest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="process control is POSIX-only")

MONITOR = UnitManifest.model_validate(
    {
        "id": "alpha",
        "kind": "monitor",
        "title": "Альфа",
        "processes": {"main": {"title": "Процесс", "module": "alpha.main"}},
    }
)
TRADING = UnitManifest.model_validate(
    {
        "id": "beta",
        "kind": "strategy",
        "title": "Бета",
        "places_orders": True,
        "processes": {"main": {"title": "Процесс", "module": "beta.main", "check": True}},
    }
)
SYSTEM = UnitManifest.model_validate(
    {
        "id": "gamma",
        "kind": "system",
        "title": "Гамма",
        "api": {"process": "main", "port": 18555},
        "processes": {"main": {"title": "Процесс", "module": "gamma.main"}},
    }
)
ASKING = UnitManifest.model_validate(
    {
        "id": "delta",
        "kind": "monitor",
        "title": "Дельта",
        "processes": {"main": {"title": "Процесс", "module": "delta.main", "confirm": "Точно?"}},
    }
)
STARTED = {"pid": 101, "create_time": 1.0, "start_mark": 2.0, "started_at": 100.0}


@dataclass
class Machine:
    alive: bool = False
    lock_holder: int | None = None
    instances: list[int] = field(default_factory=list)
    port_holder: int | None = None
    asked: list[tuple[Any, ...]] = field(default_factory=list)


@pytest.fixture
def machine(monkeypatch: pytest.MonkeyPatch) -> Machine:
    fake = Machine()

    def owned(base: Path, pid: int | None, started: float | None, mark: float | None = None):
        fake.asked.append(("owned", pid, started, mark))
        return fake.alive and pid is not None

    def lock_holder(lock: Path, module: str, base: Path | None = None) -> int | None:
        fake.asked.append(("lock_holder", lock, module))
        return fake.lock_holder

    def find_instances(base: Path, spec: Any, exclude: set[int] | None = None) -> list[int]:
        fake.asked.append(("find_instances", exclude))
        return [pid for pid in fake.instances if pid not in (exclude or set())]

    def port_holder(port: int) -> int | None:
        fake.asked.append(("port_holder", port))
        return fake.port_holder

    monkeypatch.setattr(procs, "owned", owned)
    monkeypatch.setattr(procs, "lock_holder", lock_holder)
    monkeypatch.setattr(procs, "find_instances", find_instances)
    monkeypatch.setattr(procs, "port_holder", port_holder)
    return fake


def write_record(base: Path, unit: UnitManifest, record: dict[str, Any]) -> None:
    write_json_atomic(paths.state_path(base, unit.id, "main"), record)


def write_op(base: Path, unit: UnitManifest, phase: str, **extra: Any) -> None:
    op = {"op_id": "op-1", "action": "stop", "phase": phase, "error": "сбой"} | extra
    write_json_atomic(paths.op_path(base, unit.id, "main"), op)


def view_of(base: Path, unit: UnitManifest, *, busy: bool = False) -> dict[str, Any]:
    if not busy:
        return state.process_view(base, unit, "main")
    with state.transition_lock(paths.lock_path(base, unit.id, "main")):
        return state.process_view(base, unit, "main")


def stopped(**changes: Any) -> dict[str, Any]:
    view = {
        "name": "main",
        "title": "Процесс",
        "state": "stopped",
        "label": "остановлен",
        "pid": None,
        "since": None,
        "adopted": False,
        "can": ["start"],
        "confirm": None,
        "op": None,
        "problem": None,
        "output": False,
        "live_trading": None,
    }
    return view | changes


def op_summary(phase: str, action: str = "stop", error: str | None = "сбой") -> dict[str, Any]:
    return {"op_id": "op-1", "action": action, "phase": phase, "error": error}


RUNNING = {"state": "running", "label": "работает", "pid": 101, "since": 100.0}


class TestStopped:
    def test_a_unit_never_started_can_be_started(self, root: Path, machine: Machine):
        assert view_of(root, MONITOR) == stopped()
        assert ("owned", None, None, None) in machine.asked

    def test_an_empty_record_is_no_record(self, root: Path, machine: Machine):
        write_record(root, MONITOR, {})
        assert view_of(root, MONITOR) == stopped()

    def test_a_dead_pid_reads_as_exited_by_itself(self, root: Path, machine: Machine):
        write_record(root, MONITOR, STARTED | {"adopted": True})
        assert view_of(root, MONITOR) == stopped(
            state="exited",
            label="упал",
            adopted=True,
            problem="процесс 101 завершился сам — причина в выводе процесса",
        )

    def test_a_failed_operation_names_its_error(self, root: Path, machine: Machine):
        write_op(root, MONITOR, "failed", error="не запустился")
        assert view_of(root, MONITOR) == stopped(
            op=op_summary("failed", error="не запустился"), problem="не запустился"
        )

    def test_an_exit_outranks_the_error_of_a_failed_operation(self, root: Path, machine: Machine):
        write_record(root, MONITOR, STARTED)
        write_op(root, MONITOR, "failed")
        assert view_of(root, MONITOR)["problem"] == (
            "процесс 101 завершился сам — причина в выводе процесса"
        )

    def test_a_done_operation_leaves_no_problem(self, root: Path, machine: Machine):
        write_op(root, MONITOR, "done", error=None)
        assert view_of(root, MONITOR) == stopped(op=op_summary("done", error=None))

    def test_the_output_file_is_reported_where_the_record_says(self, root: Path, machine: Machine):
        write_record(root, MONITOR, {"output": "logs/own.out"})
        (root / "logs").mkdir()
        (root / "logs/own.out").write_text("x")
        assert view_of(root, MONITOR)["output"] is True
        (root / "logs/own.out").unlink()
        (root / "logs/units").mkdir()
        (root / "logs/units/alpha.main.out").write_text("x")
        assert view_of(root, MONITOR)["output"] is False
        write_record(root, MONITOR, {})
        assert view_of(root, MONITOR)["output"] is True


class TestInProgress:
    @pytest.mark.parametrize(
        ("phase", "shown", "label"),
        [
            ("checking", "checking", "проверяется"),
            ("stopping", "stopping", "останавливается"),
            ("starting", "starting", "запускается"),
            ("waiting_ready", "starting", "запускается"),
            ("spawned", "starting", "запускается"),
        ],
    )
    def test_a_held_operation_shows_its_phase(
        self, root: Path, machine: Machine, phase: str, shown: str, label: str
    ):
        write_op(root, MONITOR, phase)
        assert view_of(root, MONITOR, busy=True) == stopped(
            state=shown, label=label, can=[], op=op_summary(phase)
        )

    def test_a_held_operation_shows_the_pid_only_while_it_lives(self, root: Path, machine: Machine):
        write_record(root, MONITOR, STARTED)
        write_op(root, MONITOR, "stopping")
        assert view_of(root, MONITOR, busy=True)["pid"] is None
        machine.alive = True
        held = view_of(root, MONITOR, busy=True)
        assert (held["state"], held["pid"], held["since"], held["can"]) == (
            "stopping",
            101,
            None,
            [],
        )

    @pytest.mark.parametrize("phase", ["done", "failed"])
    def test_a_finished_operation_is_not_in_progress_while_the_lock_is_held(
        self, root: Path, machine: Machine, phase: str
    ):
        write_op(root, MONITOR, phase)
        assert view_of(root, MONITOR, busy=True)["state"] == "stopped"

    def test_an_operation_record_without_a_phase_is_in_progress(self, root: Path, machine: Machine):
        write_json_atomic(paths.op_path(root, MONITOR.id, "main"), {"action": "start"})
        assert view_of(root, MONITOR, busy=True)["state"] == "starting"


class TestRunning:
    def test_a_running_process_can_stop_and_restart(self, root: Path, machine: Machine):
        write_record(root, MONITOR, STARTED)
        machine.alive = True
        assert view_of(root, MONITOR) == stopped(**RUNNING, can=["stop", "restart"])
        assert ("owned", 101, 1.0, 2.0) in machine.asked
        assert not any(name == "find_instances" for name, *_ in machine.asked)

    def test_a_stop_that_timed_out_leaves_it_stuck(self, root: Path, machine: Machine):
        write_record(root, MONITOR, STARTED)
        write_op(root, MONITOR, "failed", error_code="stop_timeout", finished_at=50.0)
        machine.alive = True
        assert view_of(root, MONITOR) == stopped(
            state="stuck",
            label="завис при остановке",
            pid=101,
            since=100.0,
            can=["stop"],
            op=op_summary("failed"),
            problem="сбой",
        )

    @pytest.mark.parametrize(
        ("finished_at", "problem"), [(150.0, "сбой"), (100.0, "сбой"), (50.0, None), (None, None)]
    )
    def test_a_failure_shows_only_when_it_came_after_the_start(
        self, root: Path, machine: Machine, finished_at: float | None, problem: str | None
    ):
        write_record(root, MONITOR, STARTED)
        write_op(root, MONITOR, "failed", finished_at=finished_at)
        machine.alive = True
        assert view_of(root, MONITOR) == stopped(
            **RUNNING, can=["stop", "restart"], op=op_summary("failed"), problem=problem
        )

    def test_a_failure_counts_from_zero_when_the_start_time_is_unknown(
        self, root: Path, machine: Machine
    ):
        write_record(root, MONITOR, {"pid": 101})
        write_op(root, MONITOR, "failed", finished_at=None)
        machine.alive = True
        assert view_of(root, MONITOR)["problem"] == "сбой"

    def test_an_operation_that_died_midway_is_marked_interrupted(
        self, root: Path, machine: Machine
    ):
        write_record(root, MONITOR, STARTED)
        write_op(root, MONITOR, "stopping")
        machine.alive = True
        assert view_of(root, MONITOR) == stopped(
            **RUNNING,
            can=["stop", "restart"],
            op=op_summary("interrupted"),
            problem="операция «stop» прервалась на шаге «stopping», но процесс работает",
        )

    def test_a_done_operation_keeps_the_running_view_clean(self, root: Path, machine: Machine):
        write_record(root, MONITOR, STARTED)
        write_op(root, MONITOR, "done", error=None)
        machine.alive = True
        assert view_of(root, MONITOR) == stopped(
            **RUNNING, can=["stop", "restart"], op=op_summary("done", error=None)
        )


class TestInterrupted:
    def test_an_operation_whose_runner_died_is_reported(self, root: Path, machine: Machine):
        write_record(root, MONITOR, STARTED)
        write_op(root, MONITOR, "starting", action="start")
        assert view_of(root, MONITOR) == stopped(
            state="interrupted",
            label="операция прервана",
            op=op_summary("starting", action="start"),
            problem="операция «start» прервалась на шаге «starting»: процесс, который её "
            "выполнял, завершился. Проверьте состояние и повторите.",
        )


class TestForeign:
    FOREIGN_PROBLEM = (
        "процесс {pid} работает, но консоль его не запускала — нажмите «Принять», "
        "чтобы управлять им отсюда"
    )

    def test_a_process_found_by_its_module_can_be_adopted(self, root: Path, machine: Machine):
        machine.instances = [303]
        assert view_of(root, MONITOR) == stopped(
            state="foreign",
            label="работает без присмотра консоли",
            pid=303,
            can=["adopt"],
            problem=self.FOREIGN_PROBLEM.format(pid=303),
        )
        assert ("find_instances", None) in machine.asked

    def test_the_own_dead_pid_is_excluded_from_the_scan(self, root: Path, machine: Machine):
        write_record(root, MONITOR, STARTED)
        machine.instances = [101]
        assert view_of(root, MONITOR)["state"] == "exited"
        assert ("find_instances", {101}) in machine.asked

    def test_a_foreign_process_outranks_an_interrupted_operation(
        self, root: Path, machine: Machine
    ):
        write_op(root, MONITOR, "starting")
        machine.instances = [303]
        found = view_of(root, MONITOR)
        assert (found["state"], found["pid"], found["problem"]) == (
            "foreign",
            303,
            self.FOREIGN_PROBLEM.format(pid=303),
        )

    def test_a_checked_process_is_found_by_its_lock(self, root: Path, machine: Machine):
        write_record(root, TRADING, STARTED | {"identity": {"lock": "state/beta.lock"}})
        machine.lock_holder = 202
        found = view_of(root, TRADING)
        assert (found["state"], found["pid"]) == ("foreign", 202)
        assert ("lock_holder", root / "state/beta.lock", "beta.main") in machine.asked

    def test_the_own_pid_on_the_lock_falls_back_to_the_scan(self, root: Path, machine: Machine):
        write_record(root, TRADING, STARTED | {"identity": {"lock": "state/beta.lock"}})
        machine.lock_holder = 101
        assert view_of(root, TRADING)["state"] == "exited"
        machine.instances = [303]
        assert view_of(root, TRADING)["pid"] == 303

    def test_an_unchecked_process_ignores_the_lock(self, root: Path, machine: Machine):
        write_record(root, MONITOR, STARTED | {"identity": {"lock": "state/alpha.lock"}})
        machine.lock_holder = 202
        assert view_of(root, MONITOR)["state"] == "exited"
        assert not any(name == "lock_holder" for name, *_ in machine.asked)

    def test_a_service_is_found_by_its_port(self, root: Path, machine: Machine):
        machine.port_holder = 404
        found = view_of(root, SYSTEM)
        assert (found["state"], found["pid"]) == ("foreign", 404)
        assert ("port_holder", 18555) in machine.asked

    def test_the_own_pid_on_the_port_is_not_foreign(self, root: Path, machine: Machine):
        write_record(root, SYSTEM, STARTED)
        machine.port_holder = 101
        assert view_of(root, SYSTEM)["state"] == "exited"


class TestConfirmAndLiveTrading:
    def test_the_confirm_text_follows_the_unit(self, root: Path, machine: Machine):
        assert view_of(root, ASKING)["confirm"] == "Точно?"
        assert view_of(root, TRADING)["confirm"] == state.TRADING_CONFIRM
        assert view_of(root, SYSTEM)["confirm"] == state.CONSOLE_CONFIRM
        assert view_of(root, MONITOR)["confirm"] is None

    def test_a_trading_process_shows_its_live_trading_switch(self, root: Path, machine: Machine):
        write_record(root, TRADING, STARTED | {"live_trading": True})
        assert view_of(root, TRADING)["live_trading"] == {"started": None, "restart": False}
        machine.alive = True
        assert view_of(root, TRADING)["live_trading"] == {"started": True, "restart": True}
        live.change(root, TRADING.id, True, "cli")
        assert view_of(root, TRADING)["live_trading"] == {"started": True, "restart": False}

    def test_an_unknown_start_flag_never_asks_for_a_restart(self, root: Path, machine: Machine):
        write_record(root, TRADING, STARTED | {"live_trading": "yes"})
        machine.alive = True
        live.change(root, TRADING.id, True, "cli")
        assert view_of(root, TRADING)["live_trading"] == {"started": None, "restart": False}


def test_without_posix_nothing_can_be_done(
    root: Path, machine: Machine, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(procs, "POSIX", False)
    write_record(root, MONITOR, STARTED)
    machine.alive = True
    assert view_of(root, MONITOR) == stopped(
        **RUNNING, can=[], problem="управление процессами работает только на macOS и Linux"
    )
