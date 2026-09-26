from __future__ import annotations

import json
from typing import Any

import pytest

from tbot_console.control import cli, ops, paths, state
from tbot_console.control.manifest import UnitManifest
from tests.unit.control.helpers import fixture_manifest, write_manifest


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def fake_run(base: Any, unit: UnitManifest, proc: str, action: str, **_: Any) -> int:
        calls.append(f"{unit.id}:{proc}")
        return ops.EXIT_OK

    monkeypatch.setattr(cli, "_run", fake_run)
    monkeypatch.setattr(
        state, "process_view", lambda base, unit, proc: {"can": ["start"], "label": "остановлен"}
    )
    return calls


def test_up_starts_the_console_first_and_ends_with_its_address(root, started, capsys):
    write_manifest(root, fixture_manifest("beta"))
    assert cli.main(["up"]) == 0
    assert started == ["console:web", "beta:api"]
    assert capsys.readouterr().out.splitlines()[-1] == "консоль: http://127.0.0.1:8420"


def test_up_uses_the_console_port_from_the_workspace(root, started, capsys):
    write_manifest(root, fixture_manifest("beta"))
    paths.workspace_file(root).write_text(json.dumps({"console_port": 18520}), encoding="utf-8")
    assert cli.main(["up"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "консоль: http://127.0.0.1:18520"


def test_up_withholds_the_address_of_a_console_that_failed_to_start(
    root, started, capsys, monkeypatch
):
    def fake_run(base: Any, unit: UnitManifest, proc: str, action: str, **_: Any) -> int:
        return ops.EXIT_FAILED if unit.id == "console" else ops.EXIT_OK

    monkeypatch.setattr(cli, "_run", fake_run)
    write_manifest(root, fixture_manifest("beta"))
    assert cli.main(["up"]) == ops.EXIT_FAILED
    assert "консоль:" not in capsys.readouterr().out


def test_up_outside_a_unit_repo_says_where_it_looked(root, started, capsys):
    assert cli.main(["up"]) == 2
    assert started == []
    expected = f"в {paths.units_dir(paths.root())} нет описаний — запустите из папки проекта"
    assert expected in capsys.readouterr().err


def test_a_check_names_an_unreadable_manifest(root, capsys, monkeypatch):
    monkeypatch.setattr(ops.subprocess, "run", lambda *a, **k: pytest.fail("the check ran"))
    manifest = fixture_manifest("beta")
    manifest["processes"]["api"]["check"] = True
    write_manifest(root, manifest)
    (paths.units_dir(root) / "x.json").write_text("{not json", encoding="utf-8")
    assert cli.main(["check", "beta:api"]) == 2
    assert "config/units/x.json" in capsys.readouterr().err


def test_ps_names_a_broken_file_relative_to_the_home(root, capsys, monkeypatch):
    monkeypatch.setattr(
        state,
        "process_view",
        lambda base, unit, proc: {"label": "", "pid": None, "since": None, "problem": None},
    )
    (paths.units_dir(root)).mkdir(parents=True)
    (paths.units_dir(root) / "x.json").write_text("{not json", encoding="utf-8")
    assert cli.main(["ps"]) == 0
    assert "ОШИБКА в config/units/x.json: не JSON" in capsys.readouterr().out
