from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from tbot_console.control import ops, paths, procs, state
from tbot_console.control.files import read_object
from tbot_console.control.manifest import UnitManifest
from tbot_console.control.registry import Registry, load_registry
from tbot_console.web import proxy

STATUS_TIMEOUT = httpx.Timeout(3.5, connect=0.5)
UNITS_TTL_S = 3.0
OP_ID_RE = re.compile(r"^[0-9a-f]{32}$")
UNIT_FIELDS = {"id", "kind", "title", "sub", "order", "ui", "places_orders", "legacy_pages"}

router = APIRouter(prefix="/api/control")

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_generation = [0]
_executors: list[subprocess.Popen[bytes]] = []


def _forget_finished_executors() -> None:
    _executors[:] = [child for child in _executors if child.poll() is None]


def _views(registry: Registry) -> dict[str, list[dict[str, Any]]]:
    return {
        unit.id: [state.process_view(registry.base(unit), unit, proc) for proc in unit.processes]
        for unit in registry.units
    }


def _valid_status(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("headline"), str)
        and isinstance(payload.get("metrics", []), list)
        and isinstance(payload.get("problems", []), list)
    )


async def _status(unit: UnitManifest) -> tuple[dict[str, Any] | None, Any, str | None]:
    if unit.api is None:
        return None, None, None
    url = unit.api_url() or ""
    api: dict[str, Any] = {"url": url, "reachable": None, "hint": None}
    try:
        response = await proxy.shared_client().get(
            f"{url}/api/{unit.id}/status", timeout=STATUS_TIMEOUT
        )
    except proxy.OFFLINE_ERRORS:
        api.update(reachable=False, hint=proxy.offline_hint(unit))
        return api, None, None
    except httpx.TimeoutException:
        seconds = str(STATUS_TIMEOUT.read).replace(".", ",")
        return api, None, f"служба не ответила за {seconds} с"
    except httpx.HTTPError as e:
        return api, None, f"ошибка связи со службой: {e}"
    api["reachable"] = True
    if response.status_code == 404:
        return api, None, "служба не отдаёт сводку — вероятно, работает старая версия"
    if response.status_code != 200:
        try:
            detail = response.json().get("detail")
        except (ValueError, AttributeError):
            detail = None
        suffix = f": {detail}" if detail else ""
        return api, None, f"служба ответила кодом {response.status_code}{suffix}"
    try:
        payload = response.json()
    except ValueError:
        return api, None, "служба прислала сводку не в JSON"
    if not _valid_status(payload):
        return api, None, "служба прислала сводку непонятного вида"
    return api, payload, None


async def units_payload() -> dict[str, Any]:
    cached = _cache.get("units")
    now = time.monotonic()
    if cached is not None and now - cached[0] < UNITS_TTL_S:
        return cached[1]
    registry = load_registry()
    generation = _generation[0]
    views, statuses = await asyncio.gather(
        run_in_threadpool(_views, registry),
        asyncio.gather(*(_status(unit) for unit in registry.units)),
    )
    payload = {
        "v": 1,
        "control": procs.POSIX,
        "units": [
            {
                **unit.model_dump(include=UNIT_FIELDS),
                "glyph": unit.glyph or unit.id[:2].upper(),
                "api": api,
                "processes": views[unit.id],
                "status": status,
                "status_error": error,
            }
            for unit, (api, status, error) in zip(registry.units, statuses, strict=True)
        ],
        "broken": [{"file": b.file, "error": b.error} for b in registry.broken],
    }
    if generation == _generation[0]:
        _cache["units"] = (time.monotonic(), payload)
    return payload


def _target(unit_id: str, proc: str) -> tuple[Path, UnitManifest]:
    registry = load_registry()
    unit = registry.get(unit_id)
    if unit is None:
        raise HTTPException(status_code=404, detail=f"консоль не знает «{unit_id}»")
    if proc not in unit.processes:
        raise HTTPException(status_code=404, detail=f"у «{unit.title}» нет процесса «{proc}»")
    return registry.base(unit), unit


@router.get("/units")
async def get_units() -> dict[str, Any]:
    _forget_finished_executors()
    return await units_payload()


@router.post("/units/{unit_id}/processes/{proc}/{action}", status_code=202)
async def act(unit_id: str, proc: str, action: str) -> dict[str, Any]:
    if action not in ops.ACTIONS:
        raise HTTPException(status_code=404, detail=f"неизвестное действие «{action}»")
    base, unit = _target(unit_id, proc)
    if not procs.POSIX:
        raise HTTPException(
            status_code=409, detail="управление процессами работает только на macOS и Linux"
        )
    view = await run_in_threadpool(state.process_view, base, unit, proc)
    if action not in view["can"]:
        raise HTTPException(
            status_code=409, detail=f"сейчас это действие недоступно: процесс {view['label']}"
        )
    op_id = ops.new_op_id()
    log = paths.op_log_path(base, op_id)
    argv = [
        sys.executable,
        "-m",
        "tbot_console.control.cli",
        "_op",
        action,
        f"{unit.id}:{proc}",
        "--op-id",
        op_id,
    ]
    home = paths.root()
    env = {**os.environ, paths.ROOT_ENV: str(home)}
    _executors.append(procs.spawn(argv, env, home, log))
    _generation[0] += 1
    _cache.clear()
    return {"op_id": op_id}


@router.get("/units/{unit_id}/processes/{proc}/output")
def get_output(
    unit_id: str, proc: str, lines: int = Query(default=200, ge=1, le=2000)
) -> dict[str, Any]:
    base, unit = _target(unit_id, proc)
    path = state.output_file(base, unit, proc)
    return {
        "path": paths.relative(base, path),
        "lines": ops.tail_lines(path, lines),
        "size": path.stat().st_size if path.is_file() else None,
    }


@router.get("/ops/{op_id}")
def get_op(op_id: str, lines: int = Query(default=200, ge=1, le=2000)) -> dict[str, Any]:
    if not OP_ID_RE.fullmatch(op_id):
        raise HTTPException(status_code=404, detail="нет такой операции")
    for base in dict.fromkeys(load_registry().bases.values()):
        log = paths.op_log_path(base, op_id)
        records = (read_object(p) for p in paths.run_dir(base).glob(f"*{paths.OP_SUFFIX}"))
        record = next((r for r in records if r and r.get("op_id") == op_id), None)
        if record is not None or log.is_file():
            return {"op": record, "lines": ops.tail_lines(log, lines)}
    raise HTTPException(status_code=404, detail="нет такой операции")
