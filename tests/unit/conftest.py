from __future__ import annotations

import os
from pathlib import Path

import pytest

from tbot_console.control import paths, registry

ISOLATED_PREFIXES = ("TBOT_WEB_", "IBKR_")


@pytest.fixture(autouse=True)
def isolate_unit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith(ISOLATED_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    registry.forget()


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(paths.ROOT_ENV, str(tmp_path))
    registry.forget()
    return tmp_path
