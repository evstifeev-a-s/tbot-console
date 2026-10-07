from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT_ENV = "TBOT_ROOT"
OP_SUFFIX = ".op.json"
VENV_PYTHONS = (Path(".venv/bin/python"), Path(".venv/Scripts/python.exe"))


def root() -> Path:
    return Path(os.getenv(ROOT_ENV) or Path.cwd()).resolve()


def units_dir(base: Path) -> Path:
    return base / "config" / "units"


def workspace_file(base: Path) -> Path:
    return base / "config" / "workspace.json"


def ui_dir(base: Path, ui: str) -> Path:
    return base / "js" / "units" / ui


def python(base: Path) -> Path | None:
    for candidate in VENV_PYTHONS:
        if (base / candidate).is_file():
            return base / candidate
    return Path(sys.executable) if base == root() else None


def site_dirs(base: Path) -> list[str]:
    venv = base / ".venv"
    found = [*sorted(venv.glob("lib/python*/site-packages")), venv / "Lib" / "site-packages"]
    return [str(site) for site in found if site.is_dir()]


def manifest_files(base: Path) -> list[Path]:
    directory = units_dir(base)
    return sorted(directory.glob("[!.]*.json")) if directory.is_dir() else []


def run_dir(base: Path) -> Path:
    return base / "run"


def ops_dir(base: Path) -> Path:
    return run_dir(base) / "ops"


def state_path(base: Path, unit: str, proc: str) -> Path:
    return run_dir(base) / f"{unit}.{proc}.json"


def op_path(base: Path, unit: str, proc: str) -> Path:
    return run_dir(base) / f"{unit}.{proc}{OP_SUFFIX}"


def lock_path(base: Path, unit: str, proc: str) -> Path:
    return run_dir(base) / f"{unit}.{proc}.lock"


def op_log_path(base: Path, op_id: str) -> Path:
    return ops_dir(base) / f"{op_id}.log"


def resolve(base: Path, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else base / path


def relative(base: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(base).as_posix()
    except ValueError:
        return str(path)
