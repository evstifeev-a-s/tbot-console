from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from tbot_console.control import paths, procs
from tbot_console.control.live import LIVE_TRADING_ENV
from tests.unit.control.helpers import FIXTURE_UNITS

ENV_FILE = {
    "BETA_USER_AGENT": "demo-agent/1.0",
    "BETA_DATA_DIR": "data/beta",
    "IBKR_PORT": "7497",
    "IBKR_ACCOUNT": "DU0",
    "IBKR_WRITABLE": "true",
    LIVE_TRADING_ENV: "true",
    "SERVICE_CLIENT_SECRET": "s",
    "ALPHA_DRY_RUN": "false",
    "MESSENGER_BOT_TOKEN": "t",
}
LOADED = {"BETA_USER_AGENT", "BETA_DATA_DIR", "IBKR_PORT"}
SWITCHES = {"IBKR_WRITABLE", LIVE_TRADING_ENV}


@pytest.fixture
def hand_start(tmp_path: Path) -> Iterator[Path]:
    shutil.copytree(FIXTURE_UNITS, paths.units_dir(tmp_path))
    text = "".join(f"{key}={value}\n" for key, value in ENV_FILE.items())
    (tmp_path / ".env").write_text(text, encoding="utf-8")
    with patch.dict(os.environ):
        for key in (*ENV_FILE, procs.SUPERVISED_ENV):
            os.environ.pop(key, None)
        os.environ[paths.ROOT_ENV] = str(tmp_path)
        yield tmp_path


def test_a_hand_started_process_loads_only_its_own_keys(hand_start):
    procs.load_env_file("beta")
    assert {key: os.environ[key] for key in LOADED} == {key: ENV_FILE[key] for key in LOADED}
    assert not {key for key in ENV_FILE if key not in LOADED and key in os.environ}


def test_the_shell_wins_over_the_env_file(hand_start):
    os.environ["BETA_DATA_DIR"] = "data/beta/shell"
    procs.load_env_file("beta")
    assert os.environ["BETA_DATA_DIR"] == "data/beta/shell"
    assert os.environ["IBKR_PORT"] == "7497"


def test_a_supervised_process_does_not_read_the_env_file(hand_start):
    os.environ[procs.SUPERVISED_ENV] = "beta"
    procs.load_env_file("beta")
    assert not {key for key in ENV_FILE if key in os.environ}


def test_without_a_unit_the_env_file_is_loaded_except_the_trading_switches(hand_start):
    procs.load_env_file()
    rest = {key: value for key, value in ENV_FILE.items() if key not in SWITCHES}
    assert {key: os.environ[key] for key in rest} == rest
    assert not SWITCHES & os.environ.keys()


@pytest.mark.parametrize("unit", [None, "alpha"])
def test_a_trading_switch_spelled_in_lowercase_is_dropped_too(hand_start, unit):
    (hand_start / ".env").write_text(
        f"{LIVE_TRADING_ENV.lower()}=true\nibkr_writable=true\nALPHA_DRY_RUN=false\n",
        encoding="utf-8",
    )
    procs.load_env_file(unit)
    assert not SWITCHES & {key.upper() for key in os.environ}


def test_a_missing_manifest_stops_the_start_before_reading_the_env_file(hand_start):
    (paths.units_dir(hand_start) / "beta.json").unlink()
    with pytest.raises(SystemExit, match=r"config/units/beta\.json: .*\.env не загружен"):
        procs.load_env_file("beta")
    assert not {key for key in ENV_FILE if key in os.environ}


def test_a_manifest_named_after_another_id_stops_the_start(hand_start):
    path = paths.units_dir(hand_start) / "beta.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"beta"', '"zeta"', 1), "utf-8")
    with pytest.raises(SystemExit, match=r"beta\.json: имя файла .*\.env не загружен"):
        procs.load_env_file("beta")
    assert not {key for key in ENV_FILE if key in os.environ}


def test_an_invalid_manifest_names_the_field(hand_start):
    path = paths.units_dir(hand_start) / "beta.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"BETA_"', '"beta"'), "utf-8")
    with pytest.raises(SystemExit, match=r"beta\.json: env_files_only\.prefixes\.0: .*не загружен"):
        procs.load_env_file("beta")


def test_an_unreadable_neighbour_manifest_stops_the_start(hand_start):
    (paths.units_dir(hand_start) / "x.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit, match=r"x\.json.*\.env не загружен"):
        procs.load_env_file("beta")
    assert not {key for key in ENV_FILE if key in os.environ}
