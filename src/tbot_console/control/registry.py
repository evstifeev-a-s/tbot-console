from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass, field
from importlib.metadata import distributions, version
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tbot_console.control import paths
from tbot_console.control.manifest import UnitManifest

DISTRIBUTION = "tbot-console"
CONSOLE_PORT = 8420
MEMO_TTL_S = 3.0
WORKSPACE_FILE = "config/workspace.json"
WORKSPACE_SHAPE = '{"roots": ["../папка"], "console_port": 8420}'


@dataclass(frozen=True, slots=True)
class Broken:
    file: str
    error: str


@dataclass(frozen=True, slots=True)
class Registry:
    units: tuple[UnitManifest, ...]
    broken: tuple[Broken, ...]
    bases: dict[str, Path] = field(default_factory=dict)

    def get(self, unit_id: str) -> UnitManifest | None:
        for unit in self.units:
            if unit.id == unit_id:
                return unit
        return None

    def base(self, unit: UnitManifest) -> Path:
        return self.bases[unit.id]

    def ui_base(self, ui: str) -> Path | None:
        return next((self.bases[unit.id] for unit in self.units if unit.ui == ui), None)


class Workspace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    roots: tuple[str, ...] = ()
    console_port: int = Field(default=CONSOLE_PORT, ge=1024, le=65535)


def console_manifest(port: int = CONSOLE_PORT) -> UnitManifest:
    return UnitManifest.model_validate(
        {
            "id": "console",
            "kind": "system",
            "title": "Консоль",
            "glyph": "◎",
            "sub": "эта страница: управление и наблюдение",
            "order": 0,
            "env_files_only": {"keys": ["TBOT_WEB_TOKEN"]},
            "processes": {
                "web": {
                    "title": "Веб-консоль",
                    "module": "tbot_console.web.app",
                    "env_files": [".env"],
                    "autostart": True,
                    "stop_timeout_s": 15,
                    "ready": {"kind": "http", "port": port, "timeout_s": 30},
                }
            },
        }
    )


def explain(error: ValidationError, where: str = "описание") -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in item['loc']) or where}: {item['msg']}"
        for item in error.errors()
    )


def _read(path: Path, where: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        return Broken(where, f"файл не читается: {e}")
    except json.JSONDecodeError as e:
        return Broken(where, f"не JSON: строка {e.lineno}, {e.msg}")


def parse_manifest(path: Path) -> UnitManifest | Broken:
    where = f"config/units/{path.name}"
    raw = _read(path, where)
    if isinstance(raw, Broken):
        return raw
    try:
        unit = UnitManifest.model_validate(raw)
    except ValidationError as e:
        return Broken(where, explain(e))
    if unit.id != path.stem:
        return Broken(where, f"имя файла должно совпадать с id: {unit.id}.json")
    return unit


def _ports(unit: UnitManifest) -> set[int]:
    return {port for proc in unit.processes if (port := unit.listen_port(proc)) is not None}


def _names(unit: UnitManifest) -> set[str]:
    return {unit.id, *unit.legacy_pages}


def load_root(base: Path) -> Registry:
    units: list[UnitManifest] = []
    broken: list[Broken] = []
    for path in paths.manifest_files(base):
        parsed = parse_manifest(path)
        if isinstance(parsed, Broken):
            broken.append(parsed)
        else:
            units.append(parsed)
    ports = Counter(port for unit in units for port in _ports(unit))
    clashing = {port for port, count in ports.items() if count > 1}
    accepted: list[UnitManifest] = []
    for unit in units:
        taken = sorted(_ports(unit) & clashing)
        if taken:
            broken.append(
                Broken(
                    f"config/units/{unit.id}.json",
                    f"порт {', '.join(map(str, taken))} занят другим описанием",
                )
            )
        else:
            accepted.append(unit)
    return Registry(tuple(accepted), tuple(broken), dict.fromkeys((u.id for u in accepted), base))


def workspace(home: Path) -> tuple[Workspace, list[Broken]]:
    source = paths.workspace_file(home)
    if not source.exists():
        return Workspace(), []
    raw = _read(source, WORKSPACE_FILE)
    if isinstance(raw, Broken):
        return Workspace(), [raw]
    try:
        return Workspace.model_validate(raw), []
    except ValidationError as e:
        return Workspace(), [
            Broken(
                WORKSPACE_FILE, f"нужен JSON-объект вида {WORKSPACE_SHAPE}: {explain(e, 'файл')}"
            )
        ]


def _major(raw: str) -> str:
    return raw.split(".", 1)[0]


def root_problem(base: Path) -> str | None:
    if paths.python(base) is None:
        return "нет .venv — выполните там `uv sync`"
    if not (base / ".venv").is_dir():
        return None
    installed = sorted(
        {dist.version for dist in distributions(name=DISTRIBUTION, path=paths.site_dirs(base))}
    )
    if not installed:
        return f"в .venv нет {DISTRIBUTION} — выполните там `uv sync`"
    own = version(DISTRIBUTION)
    if all(_major(found) != _major(own) for found in installed):
        return (
            f"в .venv стоит {DISTRIBUTION} {', '.join(installed)}, а консоль — {own}: другая "
            "major-версия платформы. Обновите метку платформы в pyproject.toml этого репо и "
            "выполните там `uv sync`"
        )
    return None


def claim(home: Path, roots: list[tuple[str, Path]], console: UnitManifest) -> Registry:
    units = [console]
    bases = {console.id: home}
    owner = {console.id: "консоль"}
    ports = _ports(console)
    uis: dict[str, Path] = {}
    broken: list[Broken] = []
    for label, base in roots:
        prefix = f"{label}/" if label else ""
        found = load_root(base)
        broken += [Broken(prefix + b.file, b.error) for b in found.broken]
        for unit in found.units:
            file = f"{prefix}config/units/{unit.id}.json"
            taken = sorted(_names(unit) & owner.keys())
            busy = sorted(_ports(unit) & ports)
            if taken:
                broken.append(Broken(file, f"имя «{taken[0]}» уже занято: {owner[taken[0]]}"))
            elif busy:
                broken.append(Broken(file, f"порт {', '.join(map(str, busy))} уже занят"))
            elif unit.ui is not None and uis.setdefault(unit.ui, base) != base:
                broken.append(Broken(file, f"интерфейс «{unit.ui}» уже отдаёт другая папка"))
            else:
                units.append(unit)
                bases[unit.id] = base
                owner.update(dict.fromkeys(_names(unit), file))
                ports |= _ports(unit)
    units.sort(key=lambda u: (u.order, u.id))
    return Registry(tuple(units), tuple(broken), bases)


def _load(home: Path) -> Registry:
    space, broken = workspace(home)
    roots: list[tuple[str, Path]] = []
    if (home / ".venv").is_dir() and (problem := root_problem(home)) is not None:
        broken.append(Broken("config/units", problem))
    else:
        roots.append(("", home))
    for entry in space.roots:
        base = paths.resolve(home, entry).resolve()
        where = f"{WORKSPACE_FILE}: {entry}"
        if base == home or any(base == known for _, known in roots):
            broken.append(Broken(where, "эта папка уже подключена"))
        elif not paths.units_dir(base).is_dir():
            broken.append(Broken(where, "в папке нет config/units"))
        elif (problem := root_problem(base)) is not None:
            broken.append(Broken(where, problem))
        else:
            roots.append((entry, base))
    found = claim(home, roots, console_manifest(space.console_port))
    return Registry(found.units, (*broken, *found.broken), found.bases)


_memo: dict[Path, tuple[float, Registry]] = {}


def forget() -> None:
    _memo.clear()


def load_registry(home: Path | None = None) -> Registry:
    home = (home or paths.root()).resolve()
    now = time.monotonic()
    cached = _memo.get(home)
    if cached is not None and now - cached[0] < MEMO_TTL_S:
        return cached[1]
    registry = _load(home)
    _memo[home] = (now, registry)
    return registry
