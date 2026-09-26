from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from importlib.metadata import EntryPoint
from pathlib import Path
from typing import Any

import pytest

from tbot_console.control import cli, ops, paths, procs, registry, state
from tbot_console.control.files import read_object, write_json_atomic
from tbot_console.control.manifest import ProcessSpec, UnitManifest
from tbot_console.control.registry import load_registry
from tests.unit.control.helpers import write_manifest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="process control is POSIX-only")

FAKE_UNIT = """
import json, os, signal, sys, time, http.server, threading
from pathlib import Path

mode = os.environ.get("TBOT_FAKE_MODE", "sleep")
lock = Path("state/fake.lock")
descriptor = Path("state/fake.runtime.json")


def holder():
    try:
        pid = int(lock.read_text())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


if "--check" in sys.argv:
    ok = os.environ.get("TBOT_FAKE_CHECK", "ok") == "ok"
    print("log line that is not json")
    print(json.dumps({
        "ok": ok,
        "summary": "фейковый бот",
        "checks": [{"id": "budget", "ok": ok, "detail": "бюджет $1500"}],
        "identity": {"lock": str(lock), "descriptor": str(descriptor), "lock_holder": holder()},
    }))
    sys.exit(0 if ok else 1)

if mode == "die":
    print("boom", flush=True)
    sys.exit(3)

stop = threading.Event()
if mode == "ignore":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
else:
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

Path("state").mkdir(exist_ok=True)
lock.write_text(str(os.getpid()))
if mode == "descriptor":
    descriptor.write_text(json.dumps({"pid": os.getpid()}))
if mode == "serve":
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", int(os.environ["TBOT_PORT"])), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
print("started", flush=True)
while not stop.is_set():
    time.sleep(0.05)
print("stopped", flush=True)
"""


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def unit(
    unit_id: str = "demo",
    *,
    mode: str = "sleep",
    places_orders: bool = False,
    check: bool = False,
    ready: dict[str, Any] | None = None,
    port: int | None = None,
    stop_timeout_s: int = 5,
    fake_check: str = "ok",
) -> dict[str, Any]:
    env = {"TBOT_FAKE_MODE": mode, "TBOT_FAKE_CHECK": fake_check}
    manifest: dict[str, Any] = {
        "id": unit_id,
        "kind": "strategy" if places_orders else "monitor",
        "title": "Демо",
        "places_orders": places_orders,
        "processes": {
            "main": {
                "title": "Процесс",
                "module": "fakeunit",
                "env": env,
                "check": check,
                "stop_timeout_s": stop_timeout_s,
                "ready": ready or {"kind": "alive"},
            }
        },
    }
    if port is not None:
        manifest["api"] = {"process": "main", "port": port}
    return manifest


@pytest.fixture
def fake_root(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (root / "fakeunit.py").write_text(FAKE_UNIT, encoding="utf-8")
    original = procs.child_env

    def with_path(base: Path, unit: UnitManifest, proc: str) -> tuple[dict[str, str], list[str]]:
        env, dropped = original(base, unit, proc)
        env["PYTHONPATH"] = str(root)
        return env, dropped

    monkeypatch.setattr(procs, "child_env", with_path)
    monkeypatch.setattr(ops, "ALIVE_GRACE_S", 0.3)
    yield root
    for path in root.glob("**/run/*.json"):
        record = read_object(path) or {}
        pid = record.get("pid")
        if pid and procs.is_alive(pid, record.get("create_time")):
            os.kill(pid, signal.SIGKILL)


def _lock_written(lock: Path) -> bool:
    try:
        return lock.read_text(encoding="utf-8").strip().isdigit()
    except OSError:
        return False


@pytest.fixture
def stranger(fake_root: Path) -> Iterator[subprocess.Popen[bytes]]:
    child = subprocess.Popen(
        [sys.executable, "-m", "fakeunit"],
        cwd=fake_root,
        env={**os.environ, "PYTHONPATH": str(fake_root), "TBOT_FAKE_MODE": "sleep"},
        stdout=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 5
    while not _lock_written(fake_root / "state/fake.lock") and time.monotonic() < deadline:
        time.sleep(0.05)
    yield child
    if child.poll() is None:
        child.kill()
        child.wait(5)


def run(
    root: Path, action: str, unit_id: str = "demo", pid: int | None = None
) -> tuple[int, list[str]]:
    registry.forget()
    found = load_registry(root)
    manifest = found.get(unit_id)
    assert manifest is not None, found.broken
    lines: list[str] = []
    operation = ops.Operation(root, manifest, "main", action, ops.new_op_id(), echo=lines.append)
    return operation.execute(pid), lines


def view(root: Path, unit_id: str = "demo", proc: str = "main") -> dict[str, Any]:
    registry.forget()
    manifest = load_registry(root).get(unit_id)
    assert manifest is not None
    return state.process_view(root, manifest, proc)


class TestLifecycle:
    def test_start_then_stop(self, fake_root):
        write_manifest(fake_root, unit())
        code, lines = run(fake_root, "start")
        assert code == ops.EXIT_OK, lines
        running = view(fake_root)
        assert running["state"] == "running"
        assert running["can"] == ["stop", "restart"]
        assert running["output"] is True
        pid = running["pid"]
        assert procs.runs_module(procs.cmdline(pid), "fakeunit")
        code, lines = run(fake_root, "stop")
        assert code == ops.EXIT_OK, lines
        stopped = view(fake_root)
        assert stopped["state"] == "stopped"
        assert stopped["can"] == ["start"]
        assert not procs.is_alive(pid, None)
        output = (fake_root / "logs/units/demo.main.out").read_text()
        assert "started" in output and "stopped" in output

    def test_a_second_start_is_a_no_op(self, fake_root):
        write_manifest(fake_root, unit())
        assert run(fake_root, "start")[0] == ops.EXIT_OK
        op = read_object(paths.op_path(fake_root, "demo", "main"))
        code, lines = run(fake_root, "start")
        assert code == ops.EXIT_OK
        assert "уже работает" in lines[-1]
        assert read_object(paths.op_path(fake_root, "demo", "main")) == op
        assert view(fake_root)["problem"] is None

    def test_starting_a_unit_starts_what_is_not_running_yet(self, fake_root):
        (fake_root / "fakeunit2.py").write_text(FAKE_UNIT, encoding="utf-8")
        manifest = unit()
        manifest["processes"]["two"] = {**manifest["processes"]["main"], "module": "fakeunit2"}
        write_manifest(fake_root, manifest)
        assert cli.main(["start", "demo:main"]) == ops.EXIT_OK
        assert cli.main(["start", "demo"]) == ops.EXIT_OK
        assert view(fake_root, proc="two")["state"] == "running"

    @pytest.mark.parametrize(("action", "check"), [("stop", False), ("restart", True)])
    def test_a_copied_folder_never_signals_the_original(
        self, fake_root, monkeypatch, action, check
    ):
        write_manifest(fake_root, unit(check=check))
        assert run(fake_root, "start")[0] == ops.EXIT_OK
        pid = view(fake_root)["pid"]
        copy = fake_root / "copy"
        shutil.copytree(fake_root, copy, ignore=shutil.ignore_patterns("copy"))
        monkeypatch.setenv(paths.ROOT_ENV, str(copy))
        assert view(copy)["state"] != "running"
        run(copy, action)
        assert procs.is_alive(pid, None)
        assert view(fake_root)["state"] == "running"

    def test_http_readiness_waits_for_the_port(self, fake_root):
        port = free_port()
        write_manifest(fake_root, unit(mode="serve", port=port, ready={"kind": "http"}))
        code, lines = run(fake_root, "start")
        assert code == ops.EXIT_OK, lines
        assert "отвечает" in lines[-1]
        assert procs.port_holder(port) == view(fake_root)["pid"]

    def test_descriptor_readiness_matches_the_pid(self, fake_root):
        write_manifest(fake_root, unit(mode="descriptor", check=True, ready={"kind": "descriptor"}))
        code, lines = run(fake_root, "start")
        assert code == ops.EXIT_OK, lines
        assert "работает" in lines[-1]

    def test_a_process_that_dies_on_start_is_reported_with_its_output(self, fake_root):
        write_manifest(fake_root, unit(mode="die"))
        code, _ = run(fake_root, "start")
        assert code == ops.EXIT_FAILED
        current = view(fake_root)
        assert current["state"] == "stopped"
        assert current["can"] == ["start"]
        assert "кодом 3" in current["problem"]
        assert "boom" in current["problem"]

    def test_restart_replaces_the_process(self, fake_root):
        write_manifest(fake_root, unit())
        run(fake_root, "start")
        first = view(fake_root)["pid"]
        code, lines = run(fake_root, "restart")
        assert code == ops.EXIT_OK, lines
        second = view(fake_root)["pid"]
        assert second != first
        assert not procs.is_alive(first, None)

    def test_an_unreadable_manifest_leaves_the_process_running_on_restart(self, fake_root):
        write_manifest(fake_root, unit())
        assert run(fake_root, "start")[0] == ops.EXIT_OK
        pid = view(fake_root)["pid"]
        (paths.units_dir(fake_root) / "x.json").write_text("{not json", encoding="utf-8")
        code, lines = run(fake_root, "restart")
        assert code == ops.EXIT_FAILED
        assert "config/units/x.json" in lines[-1]
        assert view(fake_root)["pid"] == pid
        assert procs.is_alive(pid, None)


class TestSafety:
    def test_a_failed_check_never_starts_anything(self, fake_root):
        write_manifest(fake_root, unit(check=True, places_orders=True, fake_check="fail"))
        code, lines = run(fake_root, "start")
        assert code == ops.EXIT_FAILED
        assert "бюджет $1500" in lines[-1]
        assert not (fake_root / "logs/units/demo.main.out").exists()

    def test_a_failed_check_leaves_the_running_bot_alone_on_restart(self, fake_root):
        write_manifest(fake_root, unit(check=True, places_orders=True))
        assert run(fake_root, "start")[0] == ops.EXIT_OK
        pid = view(fake_root)["pid"]
        write_manifest(fake_root, unit(check=True, places_orders=True, fake_check="fail"))
        code, _ = run(fake_root, "restart")
        assert code == ops.EXIT_FAILED
        current = view(fake_root)
        assert current["state"] == "running"
        assert current["pid"] == pid
        assert "проверка перед перезапуском" in current["problem"]

    def test_a_trading_bot_that_ignores_stop_is_never_killed(self, fake_root):
        write_manifest(
            fake_root, unit(mode="ignore", check=True, places_orders=True, stop_timeout_s=1)
        )
        run(fake_root, "start")
        pid = view(fake_root)["pid"]
        code, _ = run(fake_root, "stop")
        assert code == ops.EXIT_FAILED
        current = view(fake_root)
        assert current["state"] == "stuck"
        assert current["can"] == ["stop"]
        assert procs.is_alive(pid, None)
        assert "kill -9" in current["problem"]

    def test_a_start_leaves_a_stuck_bot_stuck(self, fake_root):
        write_manifest(
            fake_root, unit(mode="ignore", check=True, places_orders=True, stop_timeout_s=1)
        )
        run(fake_root, "start")
        run(fake_root, "stop")
        assert run(fake_root, "start")[0] == ops.EXIT_OK
        assert view(fake_root)["state"] == "stuck"

    def test_a_service_that_ignores_stop_is_killed(self, fake_root):
        write_manifest(fake_root, unit(mode="ignore", stop_timeout_s=1))
        run(fake_root, "start")
        pid = view(fake_root)["pid"]
        code, lines = run(fake_root, "stop")
        assert code == ops.EXIT_OK, lines
        assert not procs.is_alive(pid, None)

    def test_a_bot_started_elsewhere_blocks_a_start(self, fake_root, stranger):
        write_manifest(fake_root, unit(check=True, places_orders=True))
        code, lines = run(fake_root, "start")
        assert code == ops.EXIT_FAILED
        assert f"процесс {stranger.pid}" in lines[-1]
        assert "Принять" in lines[-1]

    def test_a_busy_process_refuses_a_second_operation(self, fake_root):
        write_manifest(fake_root, unit())
        with state.transition_lock(paths.lock_path(fake_root, "demo", "main")):
            code, lines = run(fake_root, "start")
        assert code == ops.EXIT_BUSY
        assert "другая операция" in lines[-1]

    def test_an_occupied_port_is_named(self, fake_root):
        port = free_port()
        write_manifest(fake_root, unit(mode="serve", port=port, ready={"kind": "http"}))
        with socket.socket() as squatter:
            squatter.bind(("127.0.0.1", port))
            squatter.listen()
            code, lines = run(fake_root, "start")
        assert code == ops.EXIT_FAILED
        assert f"порт {port}" in lines[-1]


class TestAdoption:
    def test_a_bot_started_elsewhere_is_shown_and_never_reported_stopped(self, fake_root, stranger):
        write_manifest(fake_root, unit(check=True, places_orders=True))
        before = view(fake_root)
        assert before["state"] == "foreign"
        assert before["pid"] == stranger.pid
        assert before["can"] == ["adopt"]
        code, lines = run(fake_root, "stop")
        assert code == ops.EXIT_FAILED
        assert f"процесс {stranger.pid}" in lines[-1]
        assert stranger.poll() is None
        code, lines = run(fake_root, "start")
        assert code == ops.EXIT_FAILED
        assert "Принять" in lines[-1]

    def test_the_same_module_for_another_unit_is_not_foreign(self, fake_root, stranger):
        other = unit("other", check=True, places_orders=True)
        other["processes"]["main"]["env"]["TBOT_FAKE_MODE"] = "descriptor"
        write_manifest(fake_root, other)
        assert view(fake_root, "other")["state"] == "stopped"

    def test_a_bot_started_elsewhere_is_taken_over(self, fake_root, stranger):
        write_manifest(fake_root, unit(check=True, places_orders=True))
        assert view(fake_root)["state"] == "foreign"
        code, lines = run(fake_root, "adopt")
        assert code == ops.EXIT_OK, lines
        current = view(fake_root)
        assert current["state"] == "running"
        assert current["adopted"] is True
        assert current["pid"] == stranger.pid
        code, _ = run(fake_root, "stop")
        assert code == ops.EXIT_OK
        assert stranger.wait(5) == 0

    def test_an_unknown_action_is_refused_not_adopted(self, fake_root, stranger):
        write_manifest(fake_root, unit(check=True, places_orders=True))
        code, _ = run(fake_root, "explode")
        assert code == ops.EXIT_FAILED
        record = read_object(paths.op_path(fake_root, "demo", "main"))
        assert record is not None and record["error_code"] == "bad_action"
        assert view(fake_root)["state"] == "foreign"

    def test_adopting_the_process_the_console_runs_is_a_no_op(self, fake_root):
        write_manifest(fake_root, unit())
        assert run(fake_root, "start")[0] == ops.EXIT_OK
        code, lines = run(fake_root, "adopt")
        assert code == ops.EXIT_OK
        assert "уже работает" in lines[-1]
        assert view(fake_root)["problem"] is None
        code, lines = run(fake_root, "adopt", pid=os.getpid())
        assert code == ops.EXIT_FAILED
        assert "уже под присмотром" in lines[-1]

    def test_after_takeover_a_lost_lock_holder_shows_as_foreign(self, fake_root, stranger):
        write_manifest(fake_root, unit(check=True, places_orders=True))
        run(fake_root, "adopt")
        record_path = paths.state_path(fake_root, "demo", "main")
        record = read_object(record_path) or {}
        record["pid"] = None
        write_json_atomic(record_path, record)
        current = view(fake_root)
        assert current["state"] == "foreign"
        assert current["can"] == ["adopt"]
        assert current["pid"] == stranger.pid

    def test_a_process_running_other_code_is_not_taken(self, fake_root):
        write_manifest(fake_root, unit())
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            code, lines = run(fake_root, "adopt", pid=other.pid)
            assert code == ops.EXIT_FAILED
            assert "запускает не fakeunit" in lines[-1]
        finally:
            other.kill()
            other.wait(5)


class TestUnitStop:
    def test_a_stuck_service_does_not_keep_the_bot_trading(self, fake_root):
        manifest = unit(places_orders=True, stop_timeout_s=1)
        manifest["processes"] = {
            "bot": {**manifest["processes"]["main"], "check": True},
            "api": {
                **manifest["processes"]["main"],
                "env": {"TBOT_FAKE_MODE": "ignore", "TBOT_FAKE_CHECK": "ok"},
            },
        }
        manifest["api"] = {"process": "api", "port": free_port()}
        write_manifest(fake_root, manifest)
        found = load_registry(fake_root)
        demo = found.get("demo")
        assert demo is not None
        for proc in ("bot", "api"):
            operation = ops.Operation(fake_root, demo, proc, "start", ops.new_op_id(), echo=print)
            assert operation.execute() == ops.EXIT_OK
        bot_pid = state.process_view(fake_root, demo, "bot")["pid"]
        api_pid = state.process_view(fake_root, demo, "api")["pid"]
        code = cli.cmd_action(found, "stop", "demo")
        assert code == ops.EXIT_OK
        assert not procs.is_alive(bot_pid, None)
        assert not procs.is_alive(api_pid, None)
        assert state.process_view(fake_root, demo, "api")["state"] == "stopped"


class TestViews:
    def test_an_interrupted_operation_is_visible(self, root):
        write_manifest(root, unit())
        write_json_atomic(
            paths.op_path(root, "demo", "main"),
            {"op_id": "x", "action": "restart", "phase": "stopping"},
        )
        current = view(root)
        assert current["state"] == "interrupted"
        assert current["can"] == ["start"]
        assert "прервалась" in current["problem"]

    def test_a_process_that_died_by_itself_is_marked(self, root):
        write_manifest(root, unit())
        write_json_atomic(
            paths.state_path(root, "demo", "main"),
            {"pid": 999_999, "create_time": 1.0, "started_at": 1.0},
        )
        current = view(root)
        assert current["state"] == "exited"
        assert current["can"] == ["start"]

    def test_a_trading_unit_asks_for_confirmation(self, root):
        write_manifest(root, unit(check=True, places_orders=True))
        assert view(root)["confirm"] == state.TRADING_CONFIRM

    def test_a_process_confirmation_from_the_manifest_wins(self, root):
        manifest = unit(check=True, places_orders=True)
        manifest["processes"]["main"]["confirm"] = "Своё предупреждение."
        write_manifest(root, manifest)
        assert view(root)["confirm"] == "Своё предупреждение."


class TestEnvironment:
    def test_env_files_carry_secrets_only(self, root, monkeypatch):
        (root / ".env").write_text(
            "SERVICE_CLIENT_ID=abc\nALPHA_DRY_RUN=false\nBETA_POLL=1\nDEMO_MODE=x\n"
            "IBKR_ALLOW_LIVE_TRADING=1\nALPHA_WEB_TOKEN=old\nTBOT_WEB_TOKEN=t0ken\n"
            "TBOT_WEB_HOST=0.0.0.0\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("ALPHA_SPOT", "X")
        monkeypatch.setenv("UNRELATED_KEY", "x")
        write_manifest(root, {**unit("other"), "env_files_ignore": {"prefixes": ["ALPHA_"]}})
        manifest = unit()
        manifest["env_files_ignore"] = {"prefixes": ["BETA_"], "keys": ["DEMO_MODE"]}
        manifest["processes"]["main"]["env_files"] = [".env", ".env.missing"]
        env, dropped = procs.child_env(root, UnitManifest.model_validate(manifest), "main")
        assert env["SERVICE_CLIENT_ID"] == "abc"
        assert env["TBOT_WEB_TOKEN"] == "t0ken"
        assert dropped == [
            "ALPHA_DRY_RUN",
            "ALPHA_WEB_TOKEN",
            "BETA_POLL",
            "DEMO_MODE",
            "IBKR_ALLOW_LIVE_TRADING",
            "TBOT_WEB_HOST",
        ]
        assert "ALPHA_SPOT" not in env
        assert "UNRELATED_KEY" not in env
        assert env["TBOT_UNIT"] == "demo"
        assert env["TBOT_ROOT"] == str(root)
        assert env["TBOT_FAKE_MODE"] == "sleep"
        assert "TBOT_PORT" not in env

    def test_an_unreadable_manifest_fails_the_start_in_plain_words(self, root):
        write_manifest(root, unit())
        (paths.units_dir(root) / "x.json").write_text("{not json", encoding="utf-8")
        code, lines = run(root, "start")
        assert code == ops.EXIT_FAILED
        assert "config/units/x.json" in lines[-1]
        op = read_object(paths.op_path(root, "demo", "main"))
        assert op is not None and op["phase"] == "failed" and op["error_code"] == "env_policy"
        assert view(root)["state"] == "stopped"

    def test_a_listening_process_is_told_its_port(self, root):
        env, _ = procs.child_env(root, UnitManifest.model_validate(unit(port=8555)), "main")
        assert env["TBOT_PORT"] == "8555"

    def test_a_supervised_service_does_not_reread_the_env_file(self, root, monkeypatch):
        (root / ".env").write_text("ALPHA_DRY_RUN=false\n", encoding="utf-8")
        monkeypatch.chdir(root)
        monkeypatch.delenv("ALPHA_DRY_RUN", raising=False)
        monkeypatch.setenv("TBOT_UNIT", "demo")
        procs.load_env_file()
        assert "ALPHA_DRY_RUN" not in os.environ
        monkeypatch.delenv("TBOT_UNIT")
        procs.load_env_file()
        assert os.environ.pop("ALPHA_DRY_RUN") == "false"

    def test_a_hand_start_never_reads_a_parent_folders_env_file(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("TBOT_PARENT_KEY=leaked\n", encoding="utf-8")
        checkout = tmp_path / "worktree"
        checkout.mkdir()
        monkeypatch.chdir(checkout)
        monkeypatch.delenv("TBOT_UNIT", raising=False)
        monkeypatch.delenv("TBOT_ROOT", raising=False)
        monkeypatch.delenv("TBOT_PARENT_KEY", raising=False)
        procs.load_env_file()
        assert "TBOT_PARENT_KEY" not in os.environ
        monkeypatch.setenv("TBOT_ROOT", str(tmp_path))
        procs.load_env_file()
        assert os.environ.pop("TBOT_PARENT_KEY") == "leaked"

    def test_keep_awake_wraps_the_command_on_macos(self, monkeypatch):
        spec = ProcessSpec.model_validate({**unit()["processes"]["main"], "keep_awake": "system"})
        monkeypatch.setattr(sys, "platform", "darwin")
        python = Path(sys.executable)
        assert procs.build_argv(spec, python)[:2] == ["/usr/bin/caffeinate", "-is"]
        monkeypatch.setattr(sys, "platform", "linux")
        assert procs.build_argv(spec, python) == [sys.executable, "-m", "fakeunit"]

    def test_a_hand_started_console_script_is_recognised(self, monkeypatch):
        module = "tbot_console.web.app"
        assert "tbot-web" in procs.console_scripts(module)
        assert procs.runs_module(["/v/bin/python3", "/v/bin/tbot-web"], module)
        point = EntryPoint("demo-unit", "fakeunit.__main__:main", "console_scripts")
        monkeypatch.setattr(procs, "entry_points", lambda group: [point])
        assert procs.runs_module(["/v/bin/python3", "/v/bin/demo-unit"], "fakeunit")

    def test_output_rotates_before_a_start(self, tmp_path, monkeypatch):
        monkeypatch.setattr(procs, "OUTPUT_ROTATE_BYTES", 10)
        target = tmp_path / "x.out"
        target.write_text("0123456789abc")
        procs.rotate_output(target)
        assert not target.exists()
        assert (tmp_path / "x.out.1").read_text() == "0123456789abc"

    def test_tail_reads_the_last_lines(self, tmp_path):
        target = tmp_path / "x.out"
        target.write_text("\n".join(str(i) for i in range(5000)) + "\n")
        assert ops.tail_lines(target, 3) == ["4997", "4998", "4999"]
        assert ops.tail_lines(tmp_path / "missing", 3) == []
