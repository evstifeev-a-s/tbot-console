from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from tbot_console.control import paths, procs
from tbot_console.control.files import flock_held, read_object
from tbot_console.control.manifest import UnitManifest

TERMINAL_PHASES = frozenset({"done", "failed"})
LOCK_RETRY_S = 0.2
PHASE_STATES = {
    "checking": "checking",
    "stopping": "stopping",
    "starting": "starting",
    "waiting_ready": "starting",
}

STATE_NAMES = {
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


def _op_summary(op: dict[str, Any] | None) -> dict[str, Any] | None:
    if not op:
        return None
    return {
        "op_id": op.get("op_id"),
        "action": op.get("action"),
        "phase": op.get("phase"),
        "error": op.get("error"),
    }


def process_view(base: Path, unit: UnitManifest, proc: str) -> dict[str, Any]:
    spec = unit.processes[proc]
    record = read_object(paths.state_path(base, unit.id, proc))
    op = read_object(paths.op_path(base, unit.id, proc))
    output = output_file(base, unit, proc, record or {})
    view: dict[str, Any] = {
        "name": proc,
        "title": spec.title,
        "state": "stopped",
        "pid": None,
        "since": None,
        "adopted": bool(record and record.get("adopted")),
        "can": [],
        "confirm": confirm_text(unit, proc),
        "op": _op_summary(op),
        "problem": None,
        "output": output.is_file(),
    }
    pid = record.get("pid") if record else None
    alive = procs.owned(base, pid, record.get("create_time") if record else None)
    busy = flock_held(paths.lock_path(base, unit.id, proc)) is True
    phase = str(op.get("phase")) if op else ""
    if busy and op and phase not in TERMINAL_PHASES:
        view["state"] = PHASE_STATES.get(phase, "starting")
        view["pid"] = pid if alive else None
        return _finish(view)
    if alive:
        stuck = bool(op and phase == "failed" and op.get("error_code") == "stop_timeout")
        view.update(
            state="stuck" if stuck else "running",
            pid=pid,
            since=record.get("started_at") if record else None,
            can=["stop"] if stuck else ["stop", "restart"],
        )
        if op and phase == "failed":
            started = float((record or {}).get("started_at") or 0.0)
            if stuck or float(op.get("finished_at") or 0.0) >= started:
                view["problem"] = op.get("error")
        elif op and phase not in TERMINAL_PHASES:
            view["op"] = {**(view["op"] or {}), "phase": "interrupted"}
            view["problem"] = (
                f"операция «{op.get('action')}» прервалась на шаге «{phase}», но процесс работает"
            )
        return _finish(view)
    if op and phase not in TERMINAL_PHASES:
        view["state"] = "interrupted"
        view["problem"] = (
            f"операция «{op.get('action')}» прервалась на шаге «{phase}»: процесс, который её "
            "выполнял, завершился. Проверьте состояние и повторите."
        )
    elif pid:
        view["state"] = "exited"
        view["problem"] = f"процесс {pid} завершился сам — причина в выводе процесса"
    foreign = foreign_pid(base, unit, proc, record)
    if foreign is not None:
        view.update(state="foreign", pid=foreign, can=["adopt"])
        view["problem"] = (
            f"процесс {foreign} работает, но консоль его не запускала — нажмите «Принять», "
            "чтобы управлять им отсюда"
        )
        return _finish(view)
    view["can"] = ["start"]
    if op and phase == "failed" and view["problem"] is None:
        view["problem"] = op.get("error")
    return _finish(view)


def _finish(view: dict[str, Any]) -> dict[str, Any]:
    view["label"] = STATE_NAMES[view["state"]]
    if procs.POSIX:
        return view
    view["can"] = []
    view["problem"] = "управление процессами работает только на macOS и Linux"
    return view
