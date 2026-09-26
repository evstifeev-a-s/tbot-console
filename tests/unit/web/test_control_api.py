from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from tbot_console.control import paths, procs, state
from tbot_console.control.files import write_json_atomic
from tbot_console.web import control_api
from tests.unit.control.helpers import fixture_manifest, write_manifest
from tests.unit.web.conftest import CONSOLE

STATUS = {
    "v": 1,
    "tone": "ok",
    "headline": "Бот торгует",
    "metrics": [{"label": "Сердцебиение", "value": "8 с назад"}],
    "problems": [],
    "updated_at": 1.0,
}


@pytest.fixture(autouse=True)
def units_root(root: Path, services) -> Path:
    write_manifest(root, fixture_manifest("alpha"))
    write_manifest(root, fixture_manifest("beta"))
    return root


class TestUnits:
    def test_every_unit_comes_with_processes_and_status(self, console, services):
        services.answers["/api/alpha/status"] = httpx.Response(200, json=STATUS)
        body = console.get("/api/control/units").json()
        assert body["v"] == 1
        assert body["broken"] == []
        console_unit, alpha, beta = body["units"]
        assert console_unit["id"] == "console" and console_unit["kind"] == "system"
        assert alpha["id"] == "alpha" and alpha["places_orders"] is True
        assert alpha["legacy_pages"] == ["old-page"]
        assert beta["legacy_pages"] == []
        assert [p["name"] for p in alpha["processes"]] == ["bot", "api"]
        assert alpha["processes"][0]["state"] == "stopped"
        assert alpha["processes"][0]["label"] == "остановлен"
        assert alpha["processes"][0]["can"] == (["start"] if procs.POSIX else [])
        assert "без присмотра" in alpha["processes"][0]["confirm"]
        assert alpha["status"]["headline"] == "Бот торгует"
        assert alpha["api"] == {"url": "http://127.0.0.1:18601", "reachable": True, "hint": None}
        assert beta["api"]["reachable"] is False
        assert "uv run tbot start beta:api" in beta["api"]["hint"]
        assert beta["status"] is None

    def test_a_slow_or_old_service_is_described(self, console, services):
        services.answers["/api/alpha/status"] = httpx.ReadTimeout("slow")
        services.answers["/api/beta/status"] = httpx.Response(404, json={"detail": "Not Found"})
        _, alpha, beta = console.get("/api/control/units").json()["units"]
        assert "не ответила за 3,5 с" in alpha["status_error"]
        assert "старая версия" in beta["status_error"]

    @pytest.mark.parametrize(
        ("answer", "error"),
        [
            (
                httpx.Response(
                    500, json={"detail": "внутренняя ошибка службы: OSError: disk gone"}
                ),
                "служба ответила кодом 500: внутренняя ошибка службы: OSError: disk gone",
            ),
            (httpx.Response(500, text="Internal Server Error"), "служба ответила кодом 500"),
            (httpx.Response(502, json=["not", "an", "object"]), "служба ответила кодом 502"),
        ],
    )
    def test_a_failed_status_shows_the_service_reason(self, console, services, answer, error):
        services.answers["/api/alpha/status"] = answer
        alpha = console.get("/api/control/units").json()["units"][1]
        assert alpha["status"] is None
        assert alpha["status_error"] == error

    def test_a_malformed_status_is_not_passed_on(self, console, services):
        services.answers["/api/alpha/status"] = httpx.Response(200, json={"tone": "ok"})
        alpha = console.get("/api/control/units").json()["units"][1]
        assert alpha["status"] is None
        assert "непонятного вида" in alpha["status_error"]

    def test_a_unit_without_a_glyph_gets_its_initials(self, console, root):
        write_manifest(root, fixture_manifest("beta", glyph=""))
        beta = console.get("/api/control/units").json()["units"][2]
        assert beta["glyph"] == "BE"

    def test_a_broken_manifest_is_listed(self, console, root):
        (root / "config/units/zz.json").write_text("[]", encoding="utf-8")
        body = console.get("/api/control/units").json()
        assert body["broken"][0]["file"] == "config/units/zz.json"

    def test_the_answer_is_briefly_cached(self, console, services):
        console.get("/api/control/units")
        seen = len(services.requests)
        console.get("/api/control/units")
        assert len(services.requests) == seen


@pytest.mark.skipif(not procs.POSIX, reason="process control is POSIX-only")
class TestActions:
    @pytest.fixture
    def spawned(self, monkeypatch) -> list[list[str]]:
        calls: list[list[str]] = []

        class Done:
            def poll(self) -> int:
                return 0

        def fake_spawn(argv, env, cwd, output):
            calls.append(argv)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("executor started\n", encoding="utf-8")
            return Done()

        monkeypatch.setattr(procs, "spawn", fake_spawn)
        return calls

    def test_an_action_runs_as_a_detached_executor(self, console, spawned, root):
        response = console.post("/api/control/units/alpha/processes/api/start", headers=CONSOLE)
        assert response.status_code == 202
        op_id = response.json()["op_id"]
        argv = spawned[0]
        assert argv[1:] == [
            "-m",
            "tbot_console.control.cli",
            "_op",
            "start",
            "alpha:api",
            "--op-id",
            op_id,
        ]
        op = console.get(f"/api/control/ops/{op_id}").json()
        assert op["lines"] == ["executor started"]

    def test_an_action_the_state_does_not_allow_is_refused(self, console, spawned):
        response = console.post("/api/control/units/alpha/processes/bot/stop", headers=CONSOLE)
        assert response.status_code == 409
        assert "остановлен" in response.json()["detail"]
        assert spawned == []

    @pytest.mark.parametrize(
        "url",
        [
            "/api/control/units/nope/processes/api/start",
            "/api/control/units/alpha/processes/nope/start",
            "/api/control/units/alpha/processes/api/explode",
        ],
    )
    def test_unknown_targets_are_404(self, console, spawned, url):
        assert console.post(url, headers=CONSOLE).status_code == 404
        assert spawned == []

    def test_a_running_operation_blocks_another(self, console, spawned, root):
        with state.transition_lock(paths.lock_path(root, "alpha", "api")):
            write_json_atomic(
                paths.op_path(root, "alpha", "api"),
                {"op_id": "a" * 32, "action": "start", "phase": "waiting_ready"},
            )
            control_api._cache.clear()
            response = console.post("/api/control/units/alpha/processes/api/start", headers=CONSOLE)
            assert response.status_code == 409
            view = console.get("/api/control/units").json()["units"][1]["processes"][1]
            assert view["state"] == "starting"


class TestOutput:
    def test_the_tail_of_a_process_output(self, console, root):
        target = root / "logs/units/alpha.api.out"
        target.parent.mkdir(parents=True)
        target.write_text("one\ntwo\nthree\n", encoding="utf-8")
        body = console.get(
            "/api/control/units/alpha/processes/api/output", params={"lines": 2}
        ).json()
        assert body["lines"] == ["two", "three"]
        assert body["path"] == "logs/units/alpha.api.out"

    def test_an_adopted_process_shows_the_file_it_writes(self, console, root):
        target = root / "logs/alpha_bot.out"
        target.parent.mkdir(parents=True)
        target.write_text("bot line\n", encoding="utf-8")
        write_json_atomic(
            paths.state_path(root, "alpha", "bot"),
            {"pid": None, "output": "logs/alpha_bot.out"},
        )
        body = console.get("/api/control/units/alpha/processes/bot/output").json()
        assert body["lines"] == ["bot line"]

    def test_no_output_yet(self, console):
        body = console.get("/api/control/units/alpha/processes/bot/output").json()
        assert body["lines"] == []
        assert body["size"] is None

    @pytest.mark.parametrize("op_id", ["../../etc", "0" * 31, "Z" * 32])
    def test_operation_ids_are_validated(self, console, op_id):
        assert console.get(f"/api/control/ops/{op_id}").status_code == 404
