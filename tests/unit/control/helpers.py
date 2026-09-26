from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tbot_console.control import paths
from tbot_console.control.registry import console_manifest

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_UNITS = Path(__file__).parent / "fixtures" / "units"


def write_manifest(root: Path, manifest: dict[str, Any]) -> Path:
    directory = paths.units_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{manifest['id']}.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path


def fixture_manifest(name: str, **top: Any) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURE_UNITS / f"{name}.json").read_text("utf-8"))
    return data | top


def console_data(**top: Any) -> dict[str, Any]:
    data: dict[str, Any] = console_manifest().model_dump(mode="json")
    return data | top
