from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from tbot_console.control import paths, procs
from tbot_console.control.manifest import UnitManifest
from tbot_console.control.registry import console_manifest, load_registry
from tests.unit.control.helpers import FIXTURE_UNITS

GOLDEN: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures/env_golden.json").read_text(encoding="utf-8")
)
TRADING_PAIRS = ("alpha:bot", "alpha:api")


@pytest.fixture(autouse=True)
def fixed_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in procs.BASE_ENV:
        monkeypatch.delenv(key, raising=False)
    for key, value in GOLDEN["environ"].items():
        monkeypatch.setenv(key, value)


def prepare(root: Path, scenario: str) -> dict[str, UnitManifest]:
    shutil.copytree(FIXTURE_UNITS, paths.units_dir(root))
    for name, values in GOLDEN["scenarios"][scenario]["files"].items():
        text = "".join(f"{key}={value}\n" for key, value in values.items())
        (root / name).write_text(text, encoding="utf-8")
    units = {
        path.stem: UnitManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
        for path in paths.manifest_files(root)
    }
    return {"console": console_manifest(), **units}


def results(root: Path, units: dict[str, UnitManifest]) -> dict[str, dict[str, Any]]:
    out = {}
    for pair in GOLDEN["pairs"]:
        unit_id, proc = pair.split(":")
        env, dropped = procs.child_env(root, units[unit_id], proc)
        assert env["TBOT_ROOT"] == str(root)
        out[pair] = {"env": {**env, "TBOT_ROOT": "<root>"}, "dropped": dropped}
    return out


def golden(scenario: str) -> dict[str, dict[str, Any]]:
    recorded: dict[str, dict[str, Any]] = GOLDEN["scenarios"][scenario]["results"]
    return recorded


def rewrite(root: Path, unit_id: str, change: dict[str, Any]) -> None:
    path = paths.units_dir(root) / f"{unit_id}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    for dotted, value in change.items():
        *parents, leaf = dotted.split(".")
        target = raw
        for key in parents:
            target = target[key]
        target[leaf] = value
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("scenario", ["A", "B"])
def test_every_process_environment_matches_the_recording(tmp_path, scenario):
    assert results(tmp_path, prepare(tmp_path, scenario)) == golden(scenario)


def test_broken_manifests_still_contribute_their_dropped_prefixes(tmp_path):
    units = prepare(tmp_path, "A")
    rewrite(tmp_path, "beta", {"api.port": 5432})
    rewrite(tmp_path, "alpha", {"restart": "always"})
    assert [unit.id for unit in load_registry(tmp_path).units] == ["console"]
    assert results(tmp_path, units) == golden("A")


def test_an_unreadable_manifest_refuses_every_environment(tmp_path):
    units = prepare(tmp_path, "A")
    (paths.units_dir(tmp_path) / "x.json").write_text("{not json", encoding="utf-8")
    for pair in GOLDEN["pairs"]:
        unit_id, proc = pair.split(":")
        with pytest.raises(procs.EnvPolicyError, match="x.json"):
            procs.child_env(tmp_path, units[unit_id], proc)


@pytest.mark.parametrize("name", ["._beta.json", ".#beta.json"])
def test_hidden_files_beside_the_manifests_are_not_manifests(tmp_path, name):
    units = prepare(tmp_path, "A")
    hidden = paths.units_dir(tmp_path) / name
    if name.startswith(".#"):
        hidden.symlink_to("user@host.1:2")
    else:
        hidden.write_bytes(b"\x00\x05\x16\x07Mac OS X")
    assert load_registry(tmp_path).broken == ()
    assert results(tmp_path, units) == golden("A")


@pytest.mark.parametrize("scenario", ["A", "B"])
def test_trading_processes_get_credentials_but_never_settings_from_env_files(tmp_path, scenario):
    units = prepare(tmp_path, scenario)
    got = {pair: item["env"] for pair, item in results(tmp_path, units).items()}
    bot = got["alpha:bot"]
    assert bot["ALPHA_CONFIG_PATH"] == "config/alpha.json"
    assert bot["SERVICE_CLIENT_SECRET"] == "b"
    assert bot["SERVICE_CLIENT_ID"] == "alpha"
    assert bot["TBOT_UNIT"] == "alpha"
    assert bot["TBOT_ROOT"] == "<root>"
    assert bot["TBOT_FOO"] == "1"
    for pair in TRADING_PAIRS:
        unit_id, proc = pair.split(":")
        declared = units[unit_id].processes[proc].env
        assert {k: v for k, v in got[pair].items() if k.startswith("ALPHA_")} == declared
    for env in got.values():
        assert "IBKR_ALLOW_LIVE_TRADING" not in env
        assert "IBKR_WRITABLE" not in env
        assert "TBOT_WEB_HOST" not in env
        assert "TBOT_WEB_ALLOWED_HOSTS" not in env


def test_the_console_takes_only_its_token_from_env_files(tmp_path):
    got = results(tmp_path, prepare(tmp_path, "B"))["console:web"]["env"]
    passed = {key for key in got if key not in GOLDEN["environ"]}
    assert passed == {
        "PYTHONFAULTHANDLER",
        "PYTHONUNBUFFERED",
        "TBOT_PORT",
        "TBOT_ROOT",
        "TBOT_UNIT",
        "TBOT_WEB_TOKEN",
    }
