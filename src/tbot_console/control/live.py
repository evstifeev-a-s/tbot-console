from __future__ import annotations

import contextlib
import logging
import os
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from tbot_console.control import paths
from tbot_console.control.files import read_object, write_json_atomic

LOG = logging.getLogger(__name__)

LIVE_TRADING_ENV = "IBKR_ALLOW_LIVE_TRADING"
Via = Literal["console", "cli"]
VIA_NAMES: dict[str, str] = {"console": "в консоли", "cli": "командой tbot live"}
RESTART_HINT = "переключатель изменён — перезапустите процесс, чтобы применить"


def entries(home: Path) -> dict[str, dict[str, Any]]:
    raw = read_object(paths.live_trading_file(home))
    units = (raw or {}).get("units")
    if not isinstance(units, dict):
        return {}
    return {unit: entry for unit, entry in units.items() if isinstance(entry, dict)}


def describe(entry: dict[str, Any] | None) -> dict[str, Any]:
    entry = entry or {}
    changed = entry.get("changed_at")
    via = entry.get("via")
    return {
        "enabled": entry.get("enabled") is True,
        "changed_at": changed if isinstance(changed, str) else None,
        "via": via if via in VIA_NAMES else None,
    }


def state(home: Path, unit_id: str) -> dict[str, Any]:
    return describe(entries(home).get(unit_id))


def enabled(home: Path, unit_id: str) -> bool:
    return bool(state(home, unit_id)["enabled"])


@contextlib.contextmanager
def _locked(home: Path) -> Iterator[None]:
    lock = paths.run_dir(home) / "live_trading.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a", encoding="utf-8") as handle:
        if sys.platform != "win32":
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def change(home: Path, unit_id: str, on: bool, via: Via) -> dict[str, Any]:
    with _locked(home):
        units = entries(home)
        units[unit_id] = {
            "enabled": on,
            "changed_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "via": via,
        }
        write_json_atomic(paths.live_trading_file(home), {"units": units})
    _record(home, unit_id, on, via)
    return state(home, unit_id)


def _record(home: Path, unit_id: str, on: bool, via: Via) -> None:
    what = "заявки разрешены на ЖИВОМ счёте" if on else "заявки только на бумажный счёт"
    line = (
        f"{datetime.now().astimezone():%Y-%m-%d %H:%M:%S %z} {unit_id}: {what} ({VIA_NAMES[via]})"
    )
    log = paths.live_trading_log(home)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    LOG.warning("живая торговля: %s", line)
