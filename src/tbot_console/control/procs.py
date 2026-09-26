from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
from importlib.metadata import EntryPoint, distributions, entry_points
from pathlib import Path

import psutil
from dotenv import dotenv_values
from pydantic import ValidationError

from tbot_console.control import paths
from tbot_console.control.files import read_object
from tbot_console.control.manifest import EnvFilesIgnore, ProcessSpec, UnitManifest
from tbot_console.control.registry import Broken, parse_manifest

POSIX = sys.platform != "win32"

BASE_ENV = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TZ",
)
FILE_DROPPED_KEYS = frozenset(
    {"IBKR_ALLOW_LIVE_TRADING", "IBKR_WRITABLE", "TBOT_WEB_HOST", "TBOT_WEB_ALLOWED_HOSTS"}
)
SUPERVISED_ENV = "TBOT_UNIT"
UNREADABLE_MANIFEST = "файл не читается или в нём не JSON-объект"
PORT_ENV = "TBOT_PORT"

OUTPUT_ROTATE_BYTES = 20 * 1024 * 1024
OUTPUT_KEEP = 3
CREATE_TIME_TOLERANCE = 0.05


class ControlUnsupportedError(RuntimeError):
    pass


class EnvPolicyError(RuntimeError):
    pass


def require_posix() -> None:
    if not POSIX:
        raise ControlUnsupportedError("управление процессами работает только на macOS и Linux")


def _policy_error(path: Path, reason: str) -> EnvPolicyError:
    return EnvPolicyError(
        f"config/units/{path.name}: {reason}. Пока файл не исправлен, процессы не запускаются: "
        "без него неизвестно, какие настройки из env-файлов отбрасывать"
    )


def env_file_policy(base: Path, unit: UnitManifest) -> tuple[tuple[str, ...], frozenset[str]]:
    prefixes = set(unit.env_files_ignore.prefixes)
    keys = set(unit.env_files_ignore.keys) | FILE_DROPPED_KEYS
    for path in paths.manifest_files(base):
        raw = read_object(path)
        if raw is None:
            raise _policy_error(path, UNREADABLE_MANIFEST)
        try:
            ignore = EnvFilesIgnore.model_validate(raw.get("env_files_ignore", {}))
        except ValidationError as e:
            raise _policy_error(path, "поле env_files_ignore заполнено неверно") from e
        prefixes.update(ignore.prefixes)
        keys.update(ignore.keys)
    return tuple(sorted(prefixes)), frozenset(keys)


def file_env(base: Path, unit: UnitManifest, *files: Path) -> tuple[dict[str, str], list[str]]:
    prefixes, keys = env_file_policy(base, unit)
    only = unit.env_files_only
    env: dict[str, str] = {}
    dropped: set[str] = set()
    for path in files:
        if not path.is_file():
            continue
        for key, value in dotenv_values(path).items():
            if value is None:
                continue
            if key.startswith(prefixes) or key in keys:
                dropped.add(key)
            elif only is None or only.admits(key):
                env[key] = value
    return env, sorted(dropped)


def child_env(base: Path, unit: UnitManifest, proc: str) -> tuple[dict[str, str], list[str]]:
    spec = unit.processes[proc]
    env = {key: os.environ[key] for key in BASE_ENV if key in os.environ}
    from_files, dropped = file_env(
        base, unit, *(paths.resolve(base, raw) for raw in spec.env_files)
    )
    env.update(from_files)
    env.update(spec.env)
    if (port := unit.listen_port(proc)) is not None:
        env[PORT_ENV] = str(port)
    env[SUPERVISED_ENV] = unit.id
    env["TBOT_ROOT"] = str(base)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONFAULTHANDLER"] = "1"
    return env, dropped


def supervised() -> bool:
    return bool(os.getenv(SUPERVISED_ENV))


def _hand_start_unit(base: Path, unit_id: str) -> UnitManifest:
    parsed = parse_manifest(paths.units_dir(base) / f"{unit_id}.json")
    if isinstance(parsed, Broken):
        raise SystemExit(f"{parsed.file}: {parsed.error}; .env не загружен")
    return parsed


def load_env_file(unit: str | None = None) -> None:
    if supervised():
        return
    base = paths.root()
    if unit is None:
        from dotenv import load_dotenv

        load_dotenv(base / ".env", override=False)
        return
    try:
        values, _ = file_env(base, _hand_start_unit(base, unit), base / ".env")
    except EnvPolicyError as e:
        raise SystemExit(f"{e}; .env не загружен") from e
    for key, value in values.items():
        os.environ.setdefault(key, value)


def interpreter(base: Path) -> Path:
    found = paths.python(base)
    if found is None:
        raise EnvPolicyError(f"в {base} нет .venv — выполните там `uv sync`")
    return found


def build_argv(spec: ProcessSpec, python: Path) -> list[str]:
    command = [str(python), "-m", spec.module, *spec.args]
    if spec.keep_awake is not None and sys.platform == "darwin":
        flags = "-is" if spec.keep_awake == "system" else "-i"
        return ["/usr/bin/caffeinate", flags, *command]
    return command


def rotate_output(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < OUTPUT_ROTATE_BYTES:
        return
    for index in range(OUTPUT_KEEP - 1, 0, -1):
        older = path.with_name(f"{path.name}.{index}")
        if older.exists():
            older.replace(path.with_name(f"{path.name}.{index + 1}"))
    path.replace(path.with_name(f"{path.name}.1"))


def spawn(argv: list[str], env: dict[str, str], cwd: Path, output: Path) -> subprocess.Popen[bytes]:
    require_posix()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("ab") as sink:
        return subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=sink,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
        )


def create_time(pid: int) -> float | None:
    try:
        return psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return None


def is_alive(pid: int | None, started: float | None) -> bool:
    if not pid:
        return False
    try:
        process = psutil.Process(pid)
        if process.status() == psutil.STATUS_ZOMBIE:
            return False
        created = process.create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return False
    return started is None or abs(created - started) <= CREATE_TIME_TOLERANCE


def owned(base: Path, pid: int | None, started: float | None) -> bool:
    return pid is not None and is_alive(pid, started) and cwd_of(pid) in (None, base.resolve())


def terminate(pid: int) -> None:
    require_posix()
    os.kill(pid, signal.SIGTERM)


def kill(pid: int) -> None:
    require_posix()
    os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))


def cmdline(pid: int) -> list[str]:
    try:
        return psutil.Process(pid).cmdline()
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return []


def cwd_of(pid: int) -> Path | None:
    try:
        return Path(psutil.Process(pid).cwd()).resolve()
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError, OSError):
        return None


def stdout_path(pid: int) -> Path | None:
    try:
        files = psutil.Process(pid).open_files()
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return None
    for item in files:
        if item.fd == 1:
            return Path(item.path)
    return None


def _script_points(base: Path | None) -> list[EntryPoint]:
    sites = paths.site_dirs(base) if base else []
    if not sites:
        return list(entry_points(group="console_scripts"))
    return [
        point
        for dist in distributions(path=sites)
        for point in dist.entry_points
        if point.group == "console_scripts"
    ]


def console_scripts(module: str, base: Path | None = None) -> set[str]:
    return {
        point.name
        for point in _script_points(base)
        if point.value.split(":", 1)[0].removesuffix(".__main__") == module
    }


def runs_module(
    argv: list[str], module: str, scripts: set[str] | None = None, base: Path | None = None
) -> bool:
    for index, arg in enumerate(argv[:-1]):
        if arg == "-m" and argv[index + 1] == module:
            return True
    names = console_scripts(module, base) if scripts is None else scripts
    return any(Path(arg).name in names for arg in argv)


def _env_compatible(process: psutil.Process, wanted: dict[str, str]) -> bool:
    if not wanted:
        return True
    try:
        env = process.environ()
    except (psutil.Error, OSError):
        return True
    return all(env.get(key, value) == value for key, value in wanted.items())


def find_instances(base: Path, spec: ProcessSpec, exclude: set[int] | None = None) -> list[int]:
    root = base.resolve()
    skipped = {os.getpid(), *(exclude or set())}
    scripts = console_scripts(spec.module, root)
    found: list[int] = []
    for process in psutil.process_iter(["pid", "cmdline", "cwd"]):
        info = process.info
        argv = info.get("cmdline") or []
        if info["pid"] in skipped or not argv or "--check" in argv:
            continue
        if not Path(argv[0]).name.startswith("python"):
            continue
        if not runs_module(argv, spec.module, scripts):
            continue
        cwd = info.get("cwd")
        if not cwd or Path(cwd).resolve() != root:
            continue
        if _env_compatible(process, spec.env):
            found.append(int(info["pid"]))
    return sorted(found)


def port_holder(port: int) -> int | None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        if probe.connect_ex(("127.0.0.1", port)) != 0:
            return None
    try:
        found = subprocess.run(
            ["lsof", "-nP", f"-tiTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        found = None
    if found is not None:
        for line in found.stdout.split():
            if line.isdigit():
                return int(line)
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port:
                return conn.pid
    except (psutil.AccessDenied, OSError):
        return None
    return None


def lock_holder(lock: Path, module: str, base: Path | None = None) -> int | None:
    try:
        raw = lock.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw.isdigit():
        return None
    pid = int(raw)
    if not is_alive(pid, None):
        return None
    return pid if runs_module(cmdline(pid), module, base=base) else None
