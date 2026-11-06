from __future__ import annotations

import contextlib
import os
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tbot_console.control import live, paths, procs
from tbot_console.control.files import flock_held, read_object
from tbot_console.control.manifest import UnitManifest

TERMINAL_PHASES = frozenset({"done", "failed"})
LOCK_RETRY_S = 0.2
PHASE_STATES = frozendict(
    {
        "checking": "checking",
        "stopping": "stopping",
        "starting": "starting",
        "waiting_ready": "starting",
    }
)

STATE_NAMES = frozendict(
    {
        "running": "работает",
        "starting": "запускается",
        "stopping": "останавливается",
        "checking": "проверяется",
        "stopped": "остановлен",
        "exited": "упал",
        "stuck": "завис при остановке",
        "interrupted": "операция прервана",
        "foreign": "работает без присмотра консоли",
    }
)

TRADING_CONFIRM = (
    "Это торговый процесс: он сам отправляет заявки. Пока он остановлен, его позиции никто "
    "не ведёт; перезапуск занимает несколько секунд."
)
CONSOLE_CONFIRM = (
    "Перезапуск: страница на несколько секунд потеряет связь и восстановится сама. "
    "Остановка: консоль сама не вернётся — запустить её снова можно только из терминала "
    "командой `uv run --no-sync tbot start console`. Стратегии и мониторы это не затрагивает."
)


class BusyError(RuntimeError):
    pass


@contextlib.contextmanager
def transition_lock(path: Path) -> Iterator[None]:
    procs.require_posix()
    if sys.platform == "win32":
        return
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + LOCK_RETRY_S
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as e:
                if time.monotonic() >= deadline:
                    raise BusyError("с этим процессом уже идёт другая операция") from e
                time.sleep(0.01)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def confirm_text(unit: UnitManifest, proc: str) -> str | None:
    if unit.processes[proc].confirm is not None:
        return unit.processes[proc].confirm
    if unit.trades(proc):
        return TRADING_CONFIRM
    if unit.kind == "system":
        return CONSOLE_CONFIRM
    return None


def instances(base: Path, unit: UnitManifest, proc: str, own: int | None = None) -> list[int]:
    port = unit.listen_port(proc)
    if port is None:
        return procs.find_instances(base, unit.processes[proc], {own} if own else None)
    holder = procs.port_holder(port)
    return [holder] if holder is not None and holder != own else []


def foreign_pid(
    base: Path, unit: UnitManifest, proc: str, record: dict[str, Any] | None
) -> int | None:
    spec = unit.processes[proc]
    own = record.get("pid") if record else None
    lock = ((record or {}).get("identity") or {}).get("lock")
    if spec.check and lock:
        holder = procs.lock_holder(paths.resolve(base, str(lock)), spec.module, base)
        if holder is not None and holder != own:
            return holder
    return next(iter(instances(base, unit, proc, own)), None)


def output_file(
    base: Path, unit: UnitManifest, proc: str, record: dict[str, Any] | None = None
) -> Path:
    if record is None:
        record = read_object(paths.state_path(base, unit.id, proc))
    return paths.resolve(base, str((record or {}).get("output") or unit.output_path(proc)))


def live_trading_view(
    unit: UnitManifest, proc: str, record: dict[str, Any] | None, alive: bool
) -> dict[str, Any] | None:
    if not unit.trades(proc):
        return None
    started = (record or {}).get("live_trading") if alive else None
    known = started if isinstance(started, bool) else None
    enabled = live.enabled(paths.root(), unit.id)
    return {"started": known, "restart": known is not None and known != enabled}


def _op_summary(op: dict[str, Any] | None) -> dict[str, Any] | None:
    if not op:
        return None
    return {
        "op_id": op.get("op_id"),
        "action": op.get("action"),
        "phase": op.get("phase"),
        "error": op.get("error"),
    }


@dataclass(frozen=True, slots=True)
class _Seen:
    record: dict[str, Any]
    op: dict[str, Any]
    alive: bool

    @property
    def pid(self) -> Any:
        return self.record.get("pid")

    @property
    def phase(self) -> str:
        return str(self.op.get("phase")) if self.op else ""

    @property
    def pending(self) -> bool:
        return bool(self.op) and self.phase not in TERMINAL_PHASES

    @property
    def failed(self) -> bool:
        return bool(self.op) and self.phase == "failed"

    @property
    def stuck(self) -> bool:
        return self.failed and self.op.get("error_code") == "stop_timeout"

    @property
    def action(self) -> Any:
        return self.op.get("action")


def process_view(base: Path, unit: UnitManifest, proc: str) -> dict[str, Any]:
    record = read_object(paths.state_path(base, unit.id, proc)) or {}
    op = read_object(paths.op_path(base, unit.id, proc)) or {}
    view = _fresh_view(base, unit, proc, record, op)
    alive = procs.owned(
        base, record.get("pid"), record.get("create_time"), record.get("start_mark")
    )
    seen = _Seen(record, op, alive)
    view["live_trading"] = live_trading_view(unit, proc, record, alive)
    busy = flock_held(paths.lock_path(base, unit.id, proc)) is True
    if busy and seen.pending:
        view.update(state=PHASE_STATES.get(seen.phase, "starting"), pid=seen.pid if alive else None)
    elif alive:
        _show_running(view, seen)
    else:
        _show_stopped(view, seen, foreign_pid(base, unit, proc, record))
    return _finish(view)


def _fresh_view(
    base: Path, unit: UnitManifest, proc: str, record: dict[str, Any], op: dict[str, Any]
) -> dict[str, Any]:
    output = output_file(base, unit, proc, record)
    return {
        "name": proc,
        "title": unit.processes[proc].title,
        "state": "stopped",
        "pid": None,
        "since": None,
        "adopted": bool(record.get("adopted")),
        "can": [],
        "confirm": confirm_text(unit, proc),
        "op": _op_summary(op),
        "problem": None,
        "output": output.is_file(),
    }


def _show_running(view: dict[str, Any], seen: _Seen) -> None:
    stuck = seen.stuck
    view.update(
        state="stuck" if stuck else "running",
        pid=seen.pid,
        since=seen.record.get("started_at"),
        can=["stop"] if stuck else ["stop", "restart"],
    )
    if seen.failed:
        started = float(seen.record.get("started_at") or 0.0)
        if stuck or float(seen.op.get("finished_at") or 0.0) >= started:
            view["problem"] = seen.op.get("error")
    elif seen.pending:
        view["op"] = {**view["op"], "phase": "interrupted"}
        view["problem"] = (
            f"операция «{seen.action}» прервалась на шаге «{seen.phase}», но процесс работает"
        )


def _show_stopped(view: dict[str, Any], seen: _Seen, foreign: int | None) -> None:
    if seen.pending:
        view["state"] = "interrupted"
        view["problem"] = (
            f"операция «{seen.action}» прервалась на шаге «{seen.phase}»: процесс, который её "
            "выполнял, завершился. Проверьте состояние и повторите."
        )
    elif seen.pid:
        view["state"] = "exited"
        view["problem"] = f"процесс {seen.pid} завершился сам — причина в выводе процесса"
    if foreign is not None:
        view.update(state="foreign", pid=foreign, can=["adopt"])
        view["problem"] = (
            f"процесс {foreign} работает, но консоль его не запускала — нажмите «Принять», "
            "чтобы управлять им отсюда"
        )
        return
    view["can"] = ["start"]
    if seen.failed and view["problem"] is None:
        view["problem"] = seen.op.get("error")


def _finish(view: dict[str, Any]) -> dict[str, Any]:
    view["label"] = STATE_NAMES[view["state"]]
    if procs.POSIX:
        return view
    view["can"] = []
    view["problem"] = "управление процессами работает только на macOS и Linux"
    return view
