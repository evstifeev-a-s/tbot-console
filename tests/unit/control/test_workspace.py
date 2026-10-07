from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest

from tbot_console.control import cli, ops, paths, procs, registry, state
from tbot_console.control.files import read_object
from tbot_console.control.manifest import UnitManifest
from tbot_console.control.registry import load_registry
from tbot_console.testing import loopback_client
from tbot_console.web.app import STATIC_DIR, create_app
from tests.unit.control.helpers import write_manifest
from tests.unit.control.test_ops import FAKE_UNIT, free_port, unit

POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="process control is POSIX-only")
PLATFORM_VERSION = version("tbot-console")


def serving(unit_id: str, port: int, **top: Any) -> dict[str, Any]:
    return unit(unit_id, port=port) | top


def workspace(home: Path, *roots: str, **extra: Any) -> None:
    paths.workspace_file(home).parent.mkdir(parents=True, exist_ok=True)
    body = {"roots": list(roots), **extra}
    paths.workspace_file(home).write_text(json.dumps(body), encoding="utf-8")
    registry.forget()


def installed(site: Path, platform_version: str = PLATFORM_VERSION) -> None:
    info = site / f"tbot_console-{platform_version}.dist-info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: tbot-console\nVersion: {platform_version}\n",
        encoding="utf-8",
    )


def venv(base: Path, platform_version: str = PLATFORM_VERSION) -> Path:
    python = base / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    python.chmod(0o755)
    installed(base / ".venv/lib/python3.14/site-packages", platform_version)
    return python


def refusal(found: registry.Registry) -> list[str]:
    return [broken.error for broken in found.broken]


@pytest.fixture
def home(root: Path) -> Path:
    write_manifest(root, unit("alpha"))
    return root


@pytest.fixture
def other(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("other")
    write_manifest(base, unit("beta"))
    venv(base)
    return base


class TestDiscovery:
    def test_without_a_workspace_file_only_the_home_and_the_console_exist(self, home):
        found = load_registry()
        assert [u.id for u in found.units] == ["console", "alpha"]
        assert found.bases == {"console": home, "alpha": home}
        assert found.broken == ()

    def test_listed_roots_join_with_their_own_base(self, home, other):
        workspace(home, str(other))
        found = load_registry()
        assert [u.id for u in found.units] == ["console", "alpha", "beta"]
        assert found.base(found.get("beta")) == other

    def test_a_relative_entry_is_read_from_the_home(self, home, other):
        workspace(home, os.path.relpath(other, home))
        found = load_registry()
        assert found.base(found.get("beta")) == other

    def test_a_new_root_appears_on_the_next_load_after_the_memo(self, home, other):
        assert load_registry().get("beta") is None
        workspace(home, str(other))
        assert load_registry().get("beta") is not None
        paths.workspace_file(home).unlink()
        registry.forget()
        assert load_registry().get("beta") is None

    def test_the_memo_holds_a_registry_for_a_few_seconds(self, home, other):
        assert load_registry().get("beta") is None
        paths.workspace_file(home).write_text(json.dumps({"roots": [str(other)]}), "utf-8")
        assert load_registry().get("beta") is None
        assert registry.MEMO_TTL_S <= 3.0

    @pytest.mark.parametrize(
        "raw", ['{"roots": "x"}', "[]", "{not json", '{"roots": [1]}', '{"console_port": 80}']
    )
    def test_a_malformed_workspace_file_keeps_the_home(self, home, raw):
        paths.workspace_file(home).write_text(raw, encoding="utf-8")
        found = load_registry()
        assert [u.id for u in found.units] == ["console", "alpha"]
        assert found.broken[0].file == "config/workspace.json"
        assert found.get("console").listen_port("web") == registry.CONSOLE_PORT

    def test_the_workspace_sets_the_console_port(self, home):
        workspace(home, console_port=18520)
        assert load_registry().get("console").listen_port("web") == 18520
        assert registry.console_manifest(18520).ready_endpoint("web") == (18520, "/")

    def test_missing_and_repeated_folders_are_reported(self, home, other):
        workspace(home, "../nope", str(other), str(other), ".")
        found = load_registry()
        assert [u.id for u in found.units] == ["console", "alpha", "beta"]
        assert refusal(found) == [
            "в папке нет config/units",
            "эта папка уже подключена",
            "эта папка уже подключена",
        ]
        assert found.broken[0].file == "config/workspace.json: ../nope"


class TestRootProblems:
    def test_a_root_without_a_venv_is_listed_broken(self, home, tmp_path_factory):
        bare = tmp_path_factory.mktemp("bare")
        write_manifest(bare, unit("beta"))
        workspace(home, str(bare))
        found = load_registry()
        assert found.get("beta") is None
        assert refusal(found) == ["нет .venv — выполните там `uv sync`"]

    def test_a_venv_without_the_platform_is_listed_broken(self, home, other):
        shutil.rmtree(other / ".venv/lib")
        workspace(home, str(other))
        assert refusal(load_registry()) == ["в .venv нет tbot-console — выполните там `uv sync`"]

    def test_another_major_version_of_the_platform_is_listed_broken(self, home, tmp_path_factory):
        newer = tmp_path_factory.mktemp("newer")
        write_manifest(newer, unit("beta"))
        venv(newer, "2.0.0")
        workspace(home, str(newer))
        found = load_registry()
        assert found.get("beta") is None
        assert "tbot-console 2.0.0" in found.broken[0].error
        assert "другая major-версия" in found.broken[0].error

    def test_another_minor_version_is_fine(self, home, tmp_path_factory):
        later = tmp_path_factory.mktemp("later")
        write_manifest(later, unit("beta"))
        major = PLATFORM_VERSION.split(".")[0]
        venv(later, f"{major}.99.0")
        workspace(home, str(later))
        assert load_registry().get("beta") is not None

    def test_a_home_venv_on_another_major_refuses_the_home_units(self, home):
        venv(home, "2.0.0")
        registry.forget()
        found = load_registry()
        assert [u.id for u in found.units] == ["console"]
        assert found.broken[0].file == "config/units"

    def test_a_windows_venv_is_recognised(self, home, tmp_path_factory):
        windows = tmp_path_factory.mktemp("windows")
        write_manifest(windows, unit("beta"))
        python = windows / ".venv/Scripts/python.exe"
        python.parent.mkdir(parents=True)
        python.write_text("", encoding="utf-8")
        installed(windows / ".venv/Lib/site-packages")
        workspace(home, str(windows))
        assert paths.python(windows) == python
        assert load_registry().get("beta") is not None

    def test_a_manifest_reading_env_files_outside_its_repo_is_refused(self, home, other):
        spec = unit("beta")
        spec["processes"]["main"]["env_files"] = ["../tbot/.env"]
        write_manifest(other, spec)
        workspace(home, str(other))
        found = load_registry()
        assert found.get("beta") is None
        assert "только из папки репо" in found.broken[0].error


class TestConflicts:
    def test_the_home_keeps_an_id_a_listed_root_repeats(self, home, other):
        write_manifest(other, unit("alpha"))
        workspace(home, str(other))
        found = load_registry()
        assert found.base(found.get("alpha")) == home
        assert [b.file for b in found.broken] == [f"{other}/config/units/alpha.json"]
        assert refusal(found) == ["имя «alpha» уже занято: config/units/alpha.json"]

    def test_a_root_cannot_replace_the_built_in_console(self, home):
        write_manifest(home, unit("console") | {"kind": "system"})
        found = load_registry()
        assert found.get("console").processes["web"].module == "tbot_console.web.app"
        assert "уже занято: консоль" in found.broken[0].error

    def test_a_later_root_never_takes_an_earlier_port(self, home, other):
        port = free_port()
        write_manifest(home, serving("alpha", port))
        write_manifest(other, serving("beta", port))
        workspace(home, str(other))
        found = load_registry()
        assert [u.id for u in found.units] == ["console", "alpha"]
        assert refusal(found) == [f"порт {port} уже занят"]

    def test_a_later_root_never_takes_the_console_port(self, home, other):
        write_manifest(other, serving("beta", 18420))
        workspace(home, str(other), console_port=18420)
        assert refusal(load_registry()) == ["порт 18420 уже занят"]

    def test_an_interface_name_belongs_to_one_folder(self, home, other):
        write_manifest(home, unit("alpha") | {"ui": "shared"})
        write_manifest(other, unit("beta") | {"ui": "shared"})
        write_manifest(home, unit("zeta") | {"ui": "shared"})
        workspace(home, str(other))
        found = load_registry()
        assert [u.id for u in found.units] == ["console", "alpha", "zeta"]
        assert found.ui_base("shared") == home
        assert "интерфейс «shared»" in found.broken[0].error

    def test_a_later_root_cannot_take_a_legacy_page(self, home, other):
        write_manifest(home, unit("alpha") | {"legacy_pages": ["old"]})
        write_manifest(other, unit("old"))
        workspace(home, str(other))
        assert "«old» уже занято" in load_registry().broken[0].error

    def test_a_later_root_cannot_claim_an_earlier_id_as_its_legacy_page(self, home, other):
        write_manifest(other, unit("beta") | {"legacy_pages": ["alpha"]})
        workspace(home, str(other))
        found = load_registry()
        assert found.get("beta") is None
        assert "«alpha» уже занято" in found.broken[0].error

    def test_earlier_roots_win_over_later_ones(self, home, other, tmp_path_factory):
        third = tmp_path_factory.mktemp("third")
        write_manifest(third, unit("beta"))
        venv(third)
        workspace(home, str(other), str(third))
        found = load_registry()
        assert found.base(found.get("beta")) == other
        assert [b.file for b in found.broken] == [f"{third}/config/units/beta.json"]


class TestEnvironment:
    def test_each_unit_reads_only_its_own_folder(self, home, other):
        write_manifest(home, unit("alpha") | {"env_files_ignore": {"prefixes": ["ALPHA_"]}})
        spec = unit("beta")
        spec["processes"]["main"]["env_files"] = [".env"]
        write_manifest(other, spec)
        (home / ".env").write_text("HOME_SECRET=h\nALPHA_KNOB=1\n", encoding="utf-8")
        (other / ".env").write_text("OTHER_SECRET=o\nALPHA_KNOB=2\n", encoding="utf-8")
        workspace(home, str(other))
        found = load_registry()
        beta = found.get("beta")
        env, dropped = procs.child_env(found.base(beta), beta, "main")
        assert env["OTHER_SECRET"] == "o"
        assert env["ALPHA_KNOB"] == "2"
        assert "HOME_SECRET" not in env
        assert env["TBOT_ROOT"] == str(other)
        assert dropped == []

    def test_a_broken_manifest_blocks_only_its_own_folder(self, home, other):
        (paths.units_dir(other) / "zz.json").write_text("{not json", encoding="utf-8")
        workspace(home, str(other))
        found = load_registry()
        alpha, beta = found.get("alpha"), found.get("beta")
        procs.child_env(home, alpha, "main")
        with pytest.raises(procs.EnvPolicyError, match="zz.json"):
            procs.child_env(other, beta, "main")


class TestInterpreter:
    def test_a_listed_root_runs_its_own_venv_unresolved(self, home, other):
        assert procs.interpreter(other) == other / ".venv" / "bin" / "python"

    def test_the_home_falls_back_to_the_console_interpreter(self, home):
        assert procs.interpreter(home) == Path(sys.executable)

    def test_a_home_with_a_venv_runs_it(self, home):
        python = venv(home)
        assert procs.interpreter(home) == python

    def test_a_listed_root_without_a_venv_refuses_to_start(self, home, tmp_path_factory):
        bare = tmp_path_factory.mktemp("bare")
        with pytest.raises(procs.EnvPolicyError, match="uv sync"):
            procs.interpreter(bare)

    def test_console_scripts_come_from_the_units_own_venv(self, other):
        site = other / ".venv/lib/python3.14/site-packages/demo-0.1.dist-info"
        site.mkdir(parents=True)
        (site / "METADATA").write_text("Metadata-Version: 2.1\nName: demo\nVersion: 0.1\n")
        (site / "entry_points.txt").write_text("[console_scripts]\ndemo-live = beta.live:main\n")
        assert procs.console_scripts("beta.live", other) == {"demo-live"}
        assert procs.console_scripts("beta.live") == set()


@POSIX_ONLY
class TestProcesses:
    @pytest.fixture
    def fake(self, home, other, monkeypatch):
        (other / "fakeunit.py").write_text(FAKE_UNIT, encoding="utf-8")
        original = procs.child_env

        def with_path(base: Path, spec: UnitManifest, proc: str):
            env, dropped = original(base, spec, proc)
            env["PYTHONPATH"] = str(base)
            return env, dropped

        monkeypatch.setattr(procs, "child_env", with_path)
        monkeypatch.setattr(ops, "ALIVE_GRACE_S", 0.3)
        workspace(home, str(other))
        yield other
        for path in paths.run_dir(other).glob("*.json"):
            pid = (read_object(path) or {}).get("pid")
            if pid and procs.is_alive(pid, None):
                procs.kill(pid)

    def test_a_listed_unit_runs_from_its_folder_with_its_python(self, home, fake, capsys):
        assert cli.main(["start", "beta"]) == ops.EXIT_OK
        assert f"запускаю {fake / '.venv/bin/python'} -m fakeunit" in capsys.readouterr().out
        record = read_object(paths.state_path(fake, "beta", "main"))
        pid = record["pid"]
        assert procs.cwd_of(pid) == fake.resolve()
        assert (fake / "logs/units/beta.main.out").is_file()
        assert not paths.run_dir(home).exists() or not list(paths.run_dir(home).glob("beta.*"))
        spec = load_registry().get("beta").processes["main"]
        assert procs.find_instances(fake, spec) == [pid]
        assert procs.find_instances(home, spec) == []
        assert cli.main(["stop", "beta"]) == ops.EXIT_OK

    def test_a_process_from_another_folder_is_never_adopted(self, home, fake):
        stray = subprocess.Popen(
            [sys.executable, "-m", "fakeunit"],
            cwd=home,
            env={**os.environ, "PYTHONPATH": str(fake)},
            stdout=subprocess.DEVNULL,
        )
        try:
            time.sleep(0.3)
            assert state.process_view(fake, load_registry().get("beta"), "main")["state"] == (
                "stopped"
            )
            assert cli.main(["adopt", "beta:main", "--pid", str(stray.pid)]) == ops.EXIT_FAILED
        finally:
            stray.kill()
            stray.wait()


class TestInterfaceFiles:
    @pytest.fixture
    def client(self, home, other):
        write_manifest(other, unit("beta") | {"ui": "beta"})
        folder = paths.ui_dir(other, "beta")
        folder.mkdir(parents=True)
        (folder / "index.js").write_text('import { h } from "../../dom.js";\n', encoding="utf-8")
        (folder / "style.css").write_text(".beta { color: red; }\n", encoding="utf-8")
        (folder / ".secret").write_text("x", encoding="utf-8")
        (other / ".env").write_text("KEY=1\n", encoding="utf-8")
        (folder / "escape.js").symlink_to(other / ".env")
        workspace(home, str(other))
        return loopback_client(create_app())

    def test_a_units_module_is_served_from_its_folder(self, client):
        response = client.get("/js/units/beta/index.js")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/javascript")
        assert response.headers["cache-control"] == "no-cache"
        assert client.get("/js/units/beta/style.css").headers["content-type"].startswith("text/css")
        assert client.get("/js/dom.js").status_code == 200
        assert client.get("/js/units/contract.d.ts").status_code == 200

    @pytest.mark.parametrize(
        "path",
        [
            "/js/units/beta/.secret",
            "/js/units/beta/escape.js",
            "/js/units/beta/%2e%2e/%2e%2e/.env",
            "/js/units/beta/..%2f..%2f.env",
            "/js/units/beta//index.js",
            "/js/units/beta/",
            "/js/units/zeta/index.js",
            "/js/units/beta/missing.js",
            "/js/units/beta/%00x",
            "/js/units/beta/a%00/b",
        ],
    )
    def test_nothing_outside_the_interface_folder_is_served(self, client, path):
        assert client.get(path).status_code == 404

    def test_a_symlinked_js_folder_is_followed(self, root, tmp_path_factory):
        shared = tmp_path_factory.mktemp("shared")
        (shared / "units/demo").mkdir(parents=True)
        (shared / "units/demo/index.js").write_bytes(b"export {};\n")
        write_manifest(root, unit("demo") | {"ui": "demo"})
        (root / "js").symlink_to(shared)
        response = loopback_client(create_app()).get("/js/units/demo/index.js")
        assert response.status_code == 200
        assert response.text == "export {};\n"


class TestUiLinks:
    def test_links_point_at_the_installed_platform(self, root, capsys):
        (root / "js").mkdir()
        (root / "js/util.js").write_text("old copy", encoding="utf-8")
        assert cli.main(["ui-links"]) == 0
        for name in cli.UI_LINKS:
            assert (root / name).is_symlink(), name
            assert (root / name).resolve() == (STATIC_DIR / name).resolve()
        assert (root / "js/util.js").read_text(encoding="utf-8") != "old copy"
        assert "js/require-tests.mjs" in capsys.readouterr().out

    def test_a_second_run_replaces_the_links(self, root):
        assert cli.main(["ui-links"]) == 0
        assert cli.main(["ui-links"]) == 0
        assert (root / "js/dom.js").is_symlink()

    def test_a_tracked_file_is_never_replaced(self, root, capsys):
        if shutil.which("git") is None:
            pytest.skip("git is not installed")
        (root / "js").mkdir()
        (root / "js/dom.js").write_text("own", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "add", "js/dom.js"], cwd=root, check=True)
        assert cli.main(["ui-links"]) == 2
        assert "js/dom.js" in capsys.readouterr().err
        assert (root / "js/dom.js").read_text(encoding="utf-8") == "own"
        assert not (root / "js/api.js").exists()

    def test_files_are_copied_where_links_are_refused(self, root, monkeypatch):
        def refuse(self: Path, target: Path) -> None:
            raise OSError("symlinks need developer mode")

        monkeypatch.setattr(Path, "symlink_to", refuse)
        assert cli.main(["ui-links"]) == 0
        for name in cli.UI_LINKS:
            assert not (root / name).is_symlink()
            assert (root / name).read_bytes() == (STATIC_DIR / name).read_bytes()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
class TestNodeGuard:
    def run_node(self, cwd: Path, pattern: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "node",
                "--test",
                "--test-reporter=spec",
                "--test-reporter-destination=stdout",
                f"--test-reporter={(STATIC_DIR / 'js/require-tests.mjs').as_uri()}",
                "--test-reporter-destination=stderr",
                pattern,
            ],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def test_zero_tests_fail_the_run(self, tmp_path):
        result = self.run_node(tmp_path, "js/units/**/*.test.js")
        assert result.returncode == 1
        assert "no tests ran" in result.stderr

    def test_a_passing_test_keeps_the_run_green(self, tmp_path):
        (tmp_path / "one.test.js").write_text(
            'import test from "node:test";\ntest("ok", () => {});\n', encoding="utf-8"
        )
        result = self.run_node(tmp_path, "*.test.js")
        assert result.returncode == 0, result.stdout + result.stderr


@POSIX_ONLY
def test_the_console_runs_an_operation_in_the_units_folder(home, other):
    (other / "fakeunit.py").write_text(FAKE_UNIT, encoding="utf-8")
    workspace(home, str(other))
    client = loopback_client(create_app())
    marked = {"X-Tbot-Console": "1"}
    op_id = client.post("/api/control/units/beta/processes/main/start", headers=marked).json()[
        "op_id"
    ]
    deadline = time.monotonic() + 20
    op: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        op = client.get(f"/api/control/ops/{op_id}").json()["op"]
        if op and op["phase"] in ("done", "failed"):
            break
        time.sleep(0.2)
    assert op is not None and op["phase"] == "done", client.get(f"/api/control/ops/{op_id}").json()
    log = paths.op_log_path(other, op_id).read_text(encoding="utf-8")
    assert f"запускаю {other / '.venv/bin/python'} -m fakeunit" in log
    pid = read_object(paths.state_path(other, "beta", "main"))["pid"]
    try:
        assert procs.cwd_of(pid) == other.resolve()
        assert procs.cmdline(pid)[1:] == ["-m", "fakeunit"]
    finally:
        assert cli.main(["stop", "beta"]) == ops.EXIT_OK
