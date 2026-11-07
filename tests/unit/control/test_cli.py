from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tbot_console.control import cli, ops, paths, procs, state
from tbot_console.control.manifest import UnitManifest
from tbot_console.control.registry import Registry
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
    paths.workspace_file(root).write_text(json.dumps({"console_port": 18520}))
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
    (paths.units_dir(root) / "x.json").write_text("{not json")
    assert cli.main(["check", "beta:api"]) == 2
    assert "config/units/x.json" in capsys.readouterr().err


def test_ps_names_a_broken_file_relative_to_the_home(root, capsys, monkeypatch):
    monkeypatch.setattr(
        state,
        "process_view",
        lambda base, unit, proc: {"label": "", "pid": None, "since": None, "problem": None},
    )
    (paths.units_dir(root)).mkdir(parents=True)
    (paths.units_dir(root) / "x.json").write_text("{not json")
    assert cli.main(["ps"]) == 0
    assert "ОШИБКА в config/units/x.json: не JSON" in capsys.readouterr().out


@pytest.mark.parametrize("action", ["start", "restart", "stop"])
def test_a_whole_unit_command_leaves_the_process_that_places_orders_alone(
    root, started, capsys, action
):
    write_manifest(root, fixture_manifest("gamma"))
    assert cli.main([action, "gamma"]) == 0
    expected = ["gamma:api", "gamma:feed"]
    assert started == (expected[::-1] if action == "stop" else expected)
    assert f"uv run tbot {action} gamma:trader" in capsys.readouterr().out
    assert cli.main([action, "gamma:trader"]) == 0
    assert started[-1] == "gamma:trader"


def test_a_unit_that_places_orders_as_a_whole_still_starts_as_a_whole(root, started, capsys):
    write_manifest(root, fixture_manifest("alpha"))
    assert cli.main(["start", "alpha"]) == 0
    assert started == ["alpha:bot", "alpha:api"]
    assert "ставит заявки" not in capsys.readouterr().out


@pytest.fixture
def handlers(root, monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    write_manifest(root, fixture_manifest("alpha"))
    write_manifest(root, fixture_manifest("beta"))
    calls: list[tuple[Any, ...]] = []

    def handler(name: str, code: int) -> Any:
        def record(*args: Any, **kwargs: Any) -> int:
            plain = [getattr(a, "id", a) for a in args if not isinstance(a, Registry | Path)]
            calls.append((name, *plain, kwargs))
            return code

        return record

    for name, code in [
        ("cmd_ps", 11),
        ("cmd_up", 12),
        ("cmd_action", 13),
        ("cmd_check", 14),
        ("cmd_output", 15),
        ("cmd_ui_links", 16),
        ("cmd_json", 17),
        ("cmd_live", 18),
        ("_run", 19),
    ]:
        monkeypatch.setattr(cli, name, handler(name, code))
    return calls


@pytest.mark.parametrize(
    ("argv", "code", "call"),
    [
        (["ps"], 11, ("cmd_ps", {})),
        (["up"], 12, ("cmd_up", {})),
        (["start", "beta"], 13, ("cmd_action", "start", "beta", {})),
        (["stop", "beta:api"], 13, ("cmd_action", "stop", "beta:api", {})),
        (["restart", "beta"], 13, ("cmd_action", "restart", "beta", {})),
        (["check", "beta"], 14, ("cmd_check", "beta", {})),
        (["output", "beta", "-n", "7"], 15, ("cmd_output", "beta", 7, {})),
        (["ui-links"], 16, ("cmd_ui_links", {})),
        (["json"], 17, ("cmd_json", None, {})),
        (["live", "beta", "status"], 18, ("cmd_live", "beta", "status", {})),
        (["adopt", "beta:api", "--pid", "42"], 19, ("_run", "beta", "api", "adopt", {"pid": 42})),
        (
            ["_op", "stop", "beta:api", "--op-id", "x1"],
            19,
            ("_run", "beta", "api", "stop", {"op_id": "x1", "pid": None}),
        ),
    ],
)
def test_each_command_reaches_its_handler(
    handlers: list[tuple[Any, ...]], argv: list[str], code: int, call: tuple[Any, ...]
):
    assert cli.main(argv) == code
    assert handlers == [call]


@pytest.mark.parametrize(
    ("argv", "refusal"),
    [
        (["adopt", "alpha"], "tbot: укажите процесс: tbot adopt <стратегия>:<процесс>"),
        (["_op", "stop", "alpha", "--op-id", "x1"], "tbot: _op нужен конкретный процесс"),
    ],
)
def test_adopt_and_op_need_one_process(
    handlers: list[tuple[Any, ...]],
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    refusal: str,
):
    assert cli.main(argv) == 2
    assert handlers == []
    assert capsys.readouterr().err.strip() == refusal


@pytest.mark.parametrize("error", [cli.CliError("нет такого"), procs.EnvPolicyError("нет такого")])
def test_a_refused_command_prints_why_and_exits_2(
    handlers: list[tuple[Any, ...]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
):
    def refuse(*_: Any) -> int:
        raise error

    monkeypatch.setattr(cli, "cmd_ps", refuse)
    assert cli.main(["ps"]) == 2
    assert capsys.readouterr().err == "tbot: нет такого\n"


def test_check_reports_each_process_and_fails_on_a_failed_check(
    root, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    write_manifest(root, fixture_manifest("alpha"))
    payload = {"summary": "бот", "checks": [{"id": "budget", "ok": False, "detail": "мало"}]}
    results = iter([ops.CheckResult(False, payload, "")])
    monkeypatch.setattr(ops, "run_check", lambda base, unit, proc: next(results))
    assert cli.main(["check", "alpha"]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "alpha:bot: проверка НЕ пройдена",
        "проверка: бот",
        "  НЕ ПРОЙДЕНО: budget — мало",
        "  причина: budget: мало",
        "alpha:api: проверка перед запуском не предусмотрена",
    ]


def test_check_passes_when_every_check_passes(
    root, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    write_manifest(root, fixture_manifest("alpha"))
    monkeypatch.setattr(ops, "run_check", lambda *_: ops.CheckResult(True, {}, ""))
    assert cli.main(["check", "alpha:bot"]) == 0
    assert capsys.readouterr().out.splitlines() == ["alpha:bot: проверка пройдена"]


def test_output_prints_the_last_lines_of_each_process(root, capsys: pytest.CaptureFixture[str]):
    write_manifest(root, fixture_manifest("alpha"))
    log = root / "logs/units/alpha.bot.out"
    log.parent.mkdir(parents=True)
    log.write_text("one\ntwo\nthree\n")
    assert cli.main(["output", "alpha", "-n", "2"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "== alpha:bot — logs/units/alpha.bot.out",
        "two",
        "three",
        "== alpha:api — logs/units/alpha.api.out",
    ]


def test_json_prints_the_views_of_the_target_or_of_everything(
    root, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    write_manifest(root, fixture_manifest("alpha"))
    monkeypatch.setattr(state, "process_view", lambda base, unit, proc: {"name": proc})
    assert cli.main(["json", "alpha:bot"]) == 0
    assert json.loads(capsys.readouterr().out) == [{"unit": "alpha", "name": "bot"}]
    assert cli.main(["json"]) == 0
    listed = [(view["unit"], view["name"]) for view in json.loads(capsys.readouterr().out)]
    assert sorted(listed) == [("alpha", "api"), ("alpha", "bot"), ("console", "web")]
