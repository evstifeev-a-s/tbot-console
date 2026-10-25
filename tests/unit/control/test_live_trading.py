from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from tbot_console.connectors.ibkr.config import LIVE_TRADING_ENV
from tbot_console.control import cli, live, ops, paths, procs, state
from tbot_console.control.manifest import UnitManifest
from tbot_console.control.registry import console_manifest, load_registry
from tests.unit.control.helpers import fixture_manifest, write_manifest


@pytest.fixture
def units(root: Path) -> dict[str, UnitManifest]:
    write_manifest(root, fixture_manifest("alpha"))
    write_manifest(root, fixture_manifest("beta"))
    write_manifest(root, fixture_manifest("gamma"))
    found = load_registry(root)
    return {unit.id: unit for unit in found.units}


def switch_file(root: Path) -> dict[str, Any]:
    return json.loads(paths.live_trading_file(root).read_text(encoding="utf-8"))


class TestSwitchFile:
    def test_no_file_or_no_entry_means_paper(self, root: Path):
        assert live.state(root, "alpha") == {"enabled": False, "changed_at": None, "via": None}
        assert not live.enabled(root, "alpha")

    def test_a_change_is_stored_per_unit_with_its_time_and_source(
        self, root: Path, caplog: pytest.LogCaptureFixture
    ):
        with caplog.at_level(logging.WARNING):
            on = live.change(root, "alpha", True, "console")
        live.change(root, "gamma", True, "cli")
        live.change(root, "gamma", False, "cli")
        assert on["enabled"] is True and on["via"] == "console"
        stored = switch_file(root)["units"]
        assert set(stored) == {"alpha", "gamma"}
        assert stored["alpha"]["enabled"] is True
        assert stored["alpha"]["changed_at"] == on["changed_at"]
        assert on["changed_at"].endswith("+00:00")
        assert stored["gamma"] == {**stored["gamma"], "enabled": False, "via": "cli"}
        trail = paths.live_trading_log(root).read_text(encoding="utf-8").splitlines()
        assert len(trail) == 3
        assert trail[0].endswith("alpha: заявки разрешены на ЖИВОМ счёте (в консоли)")
        assert trail[2].endswith("gamma: заявки только на бумажный счёт (командой tbot live)")
        assert "живая торговля" in caplog.text

    @pytest.mark.parametrize(
        "content",
        [
            "{not json",
            '{"units": []}',
            '{"units": {"alpha": "on"}}',
            '{"units": {"alpha": {"enabled": "true"}}}',
            '{"units": {"alpha": {"enabled": 1}}}',
        ],
    )
    def test_anything_but_a_literal_true_reads_as_off(self, root: Path, content: str):
        target = paths.live_trading_file(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        assert not live.enabled(root, "alpha")

    def test_a_broken_file_is_replaced_by_the_next_change(self, root: Path):
        target = paths.live_trading_file(root)
        target.parent.mkdir(parents=True)
        target.write_text("{not json", encoding="utf-8")
        live.change(root, "alpha", False, "cli")
        assert switch_file(root)["units"]["alpha"]["enabled"] is False

    @pytest.mark.parametrize("error", [SystemError("NULL result"), PermissionError("hidden")])
    def test_an_environment_the_os_hides_reads_as_unknown(
        self, monkeypatch: pytest.MonkeyPatch, error: Exception
    ):
        class Hidden:
            def __init__(self, pid: int) -> None:
                self.pid = pid

            def environ(self) -> dict[str, str]:
                raise error

        monkeypatch.setattr(procs.psutil, "Process", Hidden)
        assert procs.env_flag(4242, LIVE_TRADING_ENV) is None

    def test_the_variable_is_the_one_the_connector_reads(self):
        assert live.LIVE_TRADING_ENV == LIVE_TRADING_ENV
        assert LIVE_TRADING_ENV in procs.FILE_DROPPED_KEYS


class TestChildEnvironment:
    def test_only_the_trading_process_of_a_switched_unit_gets_the_flag(
        self, root: Path, units: dict[str, UnitManifest]
    ):
        def flags() -> dict[str, str | None]:
            pairs = {
                "alpha:bot": (units["alpha"], "bot"),
                "alpha:api": (units["alpha"], "api"),
                "beta:api": (units["beta"], "api"),
                "gamma:api": (units["gamma"], "api"),
                "gamma:feed": (units["gamma"], "feed"),
                "gamma:trader": (units["gamma"], "trader"),
                "console:web": (console_manifest(), "web"),
            }
            return {
                name: procs.child_env(root, manifest, proc)[0].get(LIVE_TRADING_ENV)
                for name, (manifest, proc) in pairs.items()
            }

        assert set(flags().values()) == {None}
        live.change(root, "alpha", True, "console")
        live.change(root, "beta", True, "console")
        live.change(root, "gamma", True, "console")
        assert flags() == {
            "alpha:bot": "true",
            "alpha:api": None,
            "beta:api": None,
            "gamma:api": None,
            "gamma:feed": None,
            "gamma:trader": "true",
            "console:web": None,
        }
        live.change(root, "alpha", False, "console")
        live.change(root, "gamma", False, "console")
        assert set(flags().values()) == {None}

    def test_an_env_file_never_supplies_the_flag(self, root: Path, units: dict[str, UnitManifest]):
        (root / ".env").write_text(f"{LIVE_TRADING_ENV}=true\nIBKR_WRITABLE=true\n", "utf-8")
        env, dropped = procs.child_env(root, units["alpha"], "bot")
        assert LIVE_TRADING_ENV not in env
        assert LIVE_TRADING_ENV in dropped
        live.change(root, "alpha", True, "console")
        (root / ".env").write_text(f"{LIVE_TRADING_ENV}=false\n", "utf-8")
        assert procs.child_env(root, units["alpha"], "bot")[0][LIVE_TRADING_ENV] == "true"

    @pytest.mark.parametrize("key", [LIVE_TRADING_ENV, "IBKR_WRITABLE"])
    def test_a_manifest_cannot_set_the_flag(self, key: str):
        manifest = fixture_manifest("alpha")
        manifest["processes"]["bot"]["env"] = {key: "true"}
        with pytest.raises(ValidationError, match=key):
            UnitManifest.model_validate(manifest)

    def test_the_check_runs_with_the_flag_the_process_will_get(
        self, root: Path, units: dict[str, UnitManifest], monkeypatch: pytest.MonkeyPatch
    ):
        seen: list[dict[str, str]] = []

        class Done:
            returncode = 0
            stdout = '{"ok": true}'
            stderr = ""

        def fake_run(argv: list[str], **kwargs: Any) -> Done:
            seen.append(kwargs["env"])
            return Done()

        monkeypatch.setattr(ops.subprocess, "run", fake_run)
        monkeypatch.setattr(procs, "interpreter", lambda base: Path(sys.executable))
        live.change(root, "alpha", True, "cli")
        assert ops.run_check(root, units["alpha"], "bot").ok
        assert seen[-1][LIVE_TRADING_ENV] == "true"


class TestCommand:
    def test_on_off_and_status(
        self, root: Path, units: dict[str, UnitManifest], capsys, monkeypatch
    ):
        monkeypatch.setattr(
            state,
            "process_view",
            lambda base, unit, proc: {"state": "stopped", "live_trading": None},
        )
        assert cli.main(["live", "alpha", "status"]) == 0
        assert "только на бумажный счёт" in capsys.readouterr().out
        assert cli.main(["live", "alpha", "on"]) == 0
        out = capsys.readouterr().out
        assert "ЖИВОМ счёте — на реальные деньги" in out
        assert "командой tbot live" in out
        assert "alpha:bot: действует со следующего запуска" in out
        assert switch_file(root)["units"]["alpha"]["via"] == "cli"
        assert cli.main(["live", "alpha", "off"]) == 0
        assert not live.enabled(root, "alpha")
        assert cli.main(["live", "gamma", "on"]) == 0
        assert "gamma:trader: действует со следующего запуска" in capsys.readouterr().out
        assert live.enabled(root, "gamma")

    def test_a_running_process_is_told_to_restart(
        self, root: Path, units: dict[str, UnitManifest], capsys, monkeypatch
    ):
        monkeypatch.setattr(
            state,
            "process_view",
            lambda base, unit, proc: {
                "state": "running",
                "live_trading": {"started": False, "restart": True},
            },
        )
        assert cli.main(["live", "alpha", "on"]) == 0
        assert "перезапустите процесс, чтобы применить: uv run tbot restart alpha:bot" in (
            capsys.readouterr().out
        )

    @pytest.mark.parametrize(
        ("target", "message"),
        [
            ("nope", "не знаю «nope»"),
            ("beta", "не ставит заявки"),
            ("alpha:bot", "действует на всю стратегию"),
        ],
    )
    def test_a_wrong_target_is_refused(
        self, root: Path, units: dict[str, UnitManifest], capsys, target: str, message: str
    ):
        assert cli.main(["live", target, "on"]) == 2
        assert message in capsys.readouterr().err
        assert not paths.live_trading_file(root).exists()
