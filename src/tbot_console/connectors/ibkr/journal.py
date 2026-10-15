from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


class OrderJournal:
    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._mended = False

    @property
    def path(self) -> Path:
        return self._path

    def write(self, event: str, **fields: Any) -> None:
        record = {"at": time.time(), "event": event, **fields}
        line = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str) + "\n"
        with self._path.open("ab") as handle:
            if not self._mended:
                handle.write(self._separator())
                self._mended = True
            handle.write(line.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())

    def _separator(self) -> bytes:
        try:
            with self._path.open("rb") as existing:
                existing.seek(-1, os.SEEK_END)
                last = existing.read(1)
        except OSError:
            return b""
        return b"" if last in (b"", b"\n") else b"\n"


def read_journal(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_bytes().splitlines()
    except FileNotFoundError:
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        try:
            record = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(record, dict):
            records.append(record)
    return records
