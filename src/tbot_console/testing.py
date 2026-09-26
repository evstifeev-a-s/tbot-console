from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tbot_console.control.registry import claim, console_manifest, load_root


def loopback_client(app: FastAPI, **kwargs: Any) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1", **kwargs)


def manifest_problems(root: Path) -> list[str]:
    found = claim(root, [("", root)], console_manifest())
    return [f"{broken.file}: {broken.error}" for broken in found.broken]


def manifest_modules(root: Path) -> list[str]:
    units = load_root(root).units
    return sorted({spec.module for unit in units for spec in unit.processes.values()})


def startup_imports(module: str, src: Path) -> str:
    lines = [f"import {module}"]
    main = src.joinpath(*module.split(".")) / "__main__.py"
    if not main.is_file():
        return "\n".join(lines)
    for node in ast.walk(ast.parse(main.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            lines.append(ast.unparse(node))
        elif isinstance(node, ast.ImportFrom):
            source = importlib.util.resolve_name("." * node.level + (node.module or ""), module)
            lines.append(ast.unparse(ast.ImportFrom(module=source, names=node.names, level=0)))
    return "\n".join(lines)


def imported_modules(code: str, cwd: Path) -> set[str]:
    probe = f"{code}\nimport sys\nprint('\\n'.join(sys.modules))"
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return set(result.stdout.split())
