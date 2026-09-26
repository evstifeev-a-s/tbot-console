from __future__ import annotations

import contextlib
import json
import os
import shlex
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from tbot_console.control import paths, procs
from tbot_console.control.files import read_object, write_json_atomic
from tbot_console.control.manifest import ProcessSpec, UnitManifest
from tbot_console.control.state import BusyError, foreign_pid, instances, transition_lock

ACTIONS = ("start", "stop", "restart", "adopt")
CHECK_TIMEOUT_S = 120
POLL_S = 0.5
ALIVE_GRACE_S = 3.0
KILL_WAIT_S = 5.0

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BUSY = 3

Echo = Callable[[str], None]


def new_op_id() -> str:
    return uuid.uuid4().hex


def stamp_echo(line: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {line}", flush=True)


class OpFailedError(Exception):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass(slots=True)
class CheckResult:
    ok: bool
    payload: dict[str, Any]
    stderr: str

    @property
    def identity(self) -> dict[str, Any]:
        identity = self.payload.get("identity")
        return identity if isinstance(identity, dict) else {}

    def describe(self) -> list[str]:
        lines = []
        summary = self.payload.get("summary")
        if summary:
            lines.append(f"проверка: {summary}")
        for item in self.payload.get("checks") or []:
            if isinstance(item, dict):
                mark = "ок" if item.get("ok") else "НЕ ПРОЙДЕНО"
                lines.append(f"  {mark}: {item.get('id')} — {item.get('detail', '')}")
        return lines

    def failures(self) -> str:
        failed = [
            f"{item.get('id')}: {item.get('detail', '')}"
            for item in self.payload.get("checks") or []
            if isinstance(item, dict) and not item.get("ok")
        ]
        if failed:
            return "; ".join(failed)
        tail = self.stderr.strip().splitlines()[-5:]
        return " / ".join(tail) or "проверка не вернула результата"


def _parse_check(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        with contextlib.suppress(ValueError):
            data = json.loads(line)
            if isinstance(data, dict):
                return data
    return None


def run_check(base: Path, unit: UnitManifest, proc: str) -> CheckResult:
    spec = unit.processes[proc]
    env, _ = procs.child_env(base, unit, proc)
    try:
        completed = subprocess.run(
            [str(procs.interpreter(base)), "-m", spec.module, *spec.args, "--check"],
            cwd=base,
            env=env,
            capture_output=True,
            text=True,
            timeout=CHECK_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(False, {}, f"проверка не уложилась в {CHECK_TIMEOUT_S} с")
    payload = _parse_check(completed.stdout) or {}
    ok = completed.returncode == 0 and bool(payload.get("ok"))
    return CheckResult(ok, payload, completed.stderr)


def tail_lines(path: Path, count: int) -> list[str]:
    if count <= 0 or not path.is_file():
        return []
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        block = 8192
        data = b""
        position = size
        while position > 0 and data.count(b"\n") <= count:
            step = min(block, position)
            position -= step
            handle.seek(position)
            data = handle.read(step) + data
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[-count:]


def _http_ok(port: int, path: str) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
            return int(response.status) == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


@dataclass(slots=True)
class Operation:
    base: Path
    unit: UnitManifest
    proc: str
    action: str
    op_id: str
    echo: Echo = stamp_echo
    record: dict[str, Any] = field(default_factory=dict)

    @property
    def spec(self) -> ProcessSpec:
        return self.unit.processes[self.proc]

    @property
    def label(self) -> str:
        return f"{self.unit.id}:{self.proc}"

    def _op_file(self) -> Path:
        return paths.op_path(self.base, self.unit.id, self.proc)

    def _state_file(self) -> Path:
        return paths.state_path(self.base, self.unit.id, self.proc)

    def phase(self, name: str) -> None:
        self.record["phase"] = name
        write_json_atomic(self._op_file(), self.record)

    def execute(self, pid: int | None = None) -> int:
        try:
            with transition_lock(paths.lock_path(self.base, self.unit.id, self.proc)):
                current = self._current()
                if (
                    self.action in ("start", "adopt")
                    and self._alive(current)
                    and pid in (None, current.get("pid"))
                ):
                    self.echo(f"{self.label}: уже работает (процесс {current.get('pid')})")
                    return EXIT_OK
                self.record = {
                    "op_id": self.op_id,
                    "action": self.action,
                    "phase": "checking",
                    "started_at": time.time(),
                    "error": None,
                    "error_code": None,
                    "finished_at": None,
                }
                write_json_atomic(self._op_file(), self.record)
                try:
                    message = self._dispatch(pid)
                except procs.EnvPolicyError as e:
                    return self._fail(OpFailedError(str(e), "env_policy"))
                except OpFailedError as e:
                    return self._fail(e)
                except BaseException:
                    self.record.update(
                        error="операция прервана до конца — проверьте состояние процесса",
                        error_code="interrupted",
                        finished_at=time.time(),
                    )
                    self.phase("failed")
                    raise
                self.record.update(finished_at=time.time())
                self.phase("done")
                self.echo(f"{self.label}: {message}")
                return EXIT_OK
        except BusyError as e:
            self.echo(f"{self.label}: {e}")
            return EXIT_BUSY
        except procs.ControlUnsupportedError as e:
            self.echo(f"{self.label}: {e}")
            return EXIT_FAILED

    def _fail(self, error: OpFailedError) -> int:
        self.record.update(error=error.message, error_code=error.code, finished_at=time.time())
        self.phase("failed")
        self.echo(f"{self.label}: не получилось — {error.message}")
        return EXIT_FAILED

    def _dispatch(self, pid: int | None) -> str:
        if self.action == "start":
            return self._start()
        if self.action == "stop":
            return self._stop()
        if self.action == "restart":
            return self._restart()
        if self.action == "adopt":
            return self._adopt(pid)
        raise OpFailedError(f"неизвестное действие {self.action}", "bad_action")

    def _current(self) -> dict[str, Any]:
        return read_object(self._state_file()) or {}

    def _alive(self, current: dict[str, Any]) -> bool:
        return procs.owned(self.base, current.get("pid"), current.get("create_time"))

    def _check(self, *, before: str, strict: bool = True) -> CheckResult:
        self.phase("checking")
        self.echo(f"{self.label}: проверяю конфиг и правила безопасности")
        result = run_check(self.base, self.unit, self.proc)
        for line in result.describe():
            self.echo(line)
        if strict and not result.ok:
            raise OpFailedError(
                f"проверка перед {before} не пройдена: {result.failures()}", "check_failed"
            )
        return result

    def _guard_lock(self, identity: dict[str, Any], own: int | None = None) -> None:
        holder = identity.get("lock_holder")
        if holder == 0 and own is None:
            raise OpFailedError(self._unknown_holder(identity), "lock_held")
        if holder and holder != own:
            raise OpFailedError(self._foreign_message(holder), "lock_held")

    def _descriptor_pid(self, identity: dict[str, Any]) -> Any:
        descriptor = identity.get("descriptor")
        if not descriptor:
            return None
        return (read_object(paths.resolve(self.base, str(descriptor))) or {}).get("pid")

    def _save(
        self,
        pid: int,
        started_at: float | None,
        output: str,
        identity: dict[str, Any],
        *,
        adopted: bool,
    ) -> None:
        write_json_atomic(
            self._state_file(),
            {
                "pid": pid,
                "create_time": procs.create_time(pid),
                "started_at": started_at,
                "output": output,
                "adopted": adopted,
                "identity": identity,
            },
        )

    def _start(self) -> str:
        identity: dict[str, Any] = {}
        if self.spec.check:
            identity = self._check(before="запуском").identity
            self._guard_lock(identity)
        found = instances(self.base, self.unit, self.proc)
        port = self.unit.listen_port(self.proc)
        if found and port is None:
            raise OpFailedError(self._foreign_message(found[0]), "foreign")
        if found:
            command = " ".join(procs.cmdline(found[0])) or "неизвестная программа"
            raise OpFailedError(
                f"порт {port} уже занят процессом {found[0]} ({command})", "port_busy"
            )
        self.phase("starting")
        env, dropped = procs.child_env(self.base, self.unit, self.proc)
        if dropped:
            self.echo(
                "из env-файлов не переданы (это настройки, а не секреты): " + ", ".join(dropped)
            )
        output = paths.resolve(self.base, self.unit.output_path(self.proc))
        procs.rotate_output(output)
        argv = procs.build_argv(self.spec, procs.interpreter(self.base))
        self.echo(f"{self.label}: запускаю {shlex.join(argv)}")
        started_at = time.time()
        child = procs.spawn(argv, env, self.base, output)
        self._save(
            child.pid, started_at, paths.relative(self.base, output), identity, adopted=False
        )
        self.phase("waiting_ready")
        return self._wait_ready(child, identity, started_at, output)

    def _wait_ready(
        self,
        child: subprocess.Popen[bytes],
        identity: dict[str, Any],
        started_at: float,
        output: Path,
    ) -> str:
        ready = self.spec.ready
        deadline = time.monotonic() + ready.timeout_s
        endpoint = self.unit.ready_endpoint(self.proc)
        while True:
            code = child.poll()
            if code is not None:
                self._mark_stopped()
                tail = "\n".join(tail_lines(output, 20))
                raise OpFailedError(
                    f"процесс завершился при запуске с кодом {code}. Последние строки вывода:\n{tail}",
                    "died_on_start",
                )
            if ready.kind == "alive" and time.time() - started_at >= ALIVE_GRACE_S:
                return f"запущен, процесс {child.pid}"
            if ready.kind == "http" and endpoint is not None and _http_ok(*endpoint):
                return f"запущен и отвечает, процесс {child.pid}"
            if ready.kind == "descriptor" and self._descriptor_pid(identity) == child.pid:
                return f"запущен и работает, процесс {child.pid}"
            if time.monotonic() >= deadline:
                return (
                    f"процесс {child.pid} жив, но не подтвердил готовность за {ready.timeout_s} с"
                )
            time.sleep(POLL_S)

    def _foreign_message(self, pid: int) -> str:
        return (
            f"работает процесс {pid}, которым консоль не управляет — примите его кнопкой "
            f"«Принять» или командой `uv run tbot adopt {self.label}`, затем повторите"
        )

    def _unknown_holder(self, identity: dict[str, Any]) -> str:
        lock = identity.get("lock") or "файл блокировки бота"
        return (
            f"{lock} занят процессом, номер которого прочитать не удалось: найдите его "
            f"(`lsof {lock}`) и примите командой `uv run tbot adopt {self.label} --pid N`"
        )

    def _mark_stopped(self) -> None:
        current = self._current()
        current.update(pid=None, create_time=None)
        write_json_atomic(self._state_file(), current)

    def _stop(self) -> str:
        current = self._current()
        pid = current.get("pid")
        if not pid or not self._alive(current):
            holder = foreign_pid(self.base, self.unit, self.proc, current)
            if holder is not None:
                raise OpFailedError(self._foreign_message(holder), "foreign")
            if pid:
                self._mark_stopped()
            return "уже остановлен"
        pid = int(pid)
        self.phase("stopping")
        self.echo(f"{self.label}: прошу процесс {pid} завершиться (SIGTERM)")
        procs.terminate(pid)
        deadline = time.monotonic() + self.spec.stop_timeout_s
        while self._alive(current) and time.monotonic() < deadline:
            time.sleep(POLL_S)
        if self._alive(current):
            if self.unit.trades(self.proc):
                raise OpFailedError(
                    f"процесс {pid} не остановился за {self.spec.stop_timeout_s} с. Разбираться "
                    "руками: kill -9 оборвёт процесс без сохранения состояния, а открытые позиции "
                    "останутся без присмотра",
                    "stop_timeout",
                )
            self.echo(f"{self.label}: не остановился вовремя — завершаю принудительно")
            procs.kill(pid)
            forced = time.monotonic() + KILL_WAIT_S
            while self._alive(current) and time.monotonic() < forced:
                time.sleep(POLL_S)
        self._mark_stopped()
        return f"остановлен (процесс {pid})"

    def _restart(self) -> str:
        current = self._current()
        procs.env_file_policy(self.base, self.unit)
        if self.spec.check:
            self._guard_lock(
                self._check(before="перезапуском").identity,
                current.get("pid") if self._alive(current) else None,
            )
        stopped = self._stop()
        self.echo(f"{self.label}: {stopped}")
        return self._start()

    def _adopt(self, pid: int | None) -> str:
        current = self._current()
        if self._alive(current):
            raise OpFailedError(
                f"уже под присмотром консоли (процесс {current.get('pid')})", "already_running"
            )
        identity = self._check(before="приёмом", strict=False).identity if self.spec.check else {}
        candidate = pid
        if candidate is None:
            holder = identity.get("lock_holder")
            if holder == 0:
                raise OpFailedError(self._unknown_holder(identity), "lock_held")
            candidate = int(holder) if holder else None
        if candidate is None:
            found = instances(self.base, self.unit, self.proc)
            if len(found) > 1:
                raise OpFailedError(
                    "подходят несколько процессов: "
                    + ", ".join(map(str, found))
                    + f" — укажите нужный: `uv run tbot adopt {self.label} --pid N`",
                    "ambiguous",
                )
            candidate = found[0] if found else None
        if candidate is None:
            raise OpFailedError("не нашёл работающий процесс, который можно принять", "not_found")
        argv = procs.cmdline(candidate)
        if not procs.runs_module(argv, self.spec.module, base=self.base):
            raise OpFailedError(
                f"процесс {candidate} запускает не {self.spec.module}: {' '.join(argv)}",
                "mismatch",
            )
        cwd = procs.cwd_of(candidate)
        if cwd is not None and cwd != self.base.resolve():
            raise OpFailedError(f"процесс {candidate} работает из другой папки: {cwd}", "mismatch")
        named = self._descriptor_pid(identity)
        if named is not None and named != candidate:
            raise OpFailedError(
                f"дескриптор бота называет процесс {named}, а не {candidate}", "mismatch"
            )
        stdout = procs.stdout_path(candidate)
        output = (
            paths.relative(self.base, stdout)
            if stdout is not None
            else self.unit.output_path(self.proc)
        )
        self._save(candidate, procs.create_time(candidate), output, identity, adopted=True)
        return f"принят под присмотр, процесс {candidate}"
