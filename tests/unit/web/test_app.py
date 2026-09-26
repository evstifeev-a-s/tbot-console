from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from tbot_console.control import procs, registry
from tbot_console.web import proxy
from tbot_console.web.app import create_app
from tests.unit.control.helpers import fixture_manifest, write_manifest
from tests.unit.web.conftest import CONSOLE

SHELL_MODULES = {
    "tbot_console",
    "tbot_console.control",
    "tbot_console.control.files",
    "tbot_console.control.logs",
    "tbot_console.control.manifest",
    "tbot_console.control.ops",
    "tbot_console.control.paths",
    "tbot_console.control.procs",
    "tbot_console.control.registry",
    "tbot_console.control.service",
    "tbot_console.control.state",
    "tbot_console.web",
    "tbot_console.web.app",
    "tbot_console.web.control_api",
    "tbot_console.web.proxy",
}


@pytest.fixture(autouse=True)
def units_root(root: Path, services) -> Path:
    write_manifest(root, fixture_manifest("alpha"))
    write_manifest(root, fixture_manifest("beta"))
    services.default = httpx.Response(200, json={"ok": True})
    return root


class TestProxy:
    def test_path_and_query_reach_the_service(self, console, services):
        services.default = httpx.Response(200, json={"summary": {"fills": 3}})
        response = console.get("/api/alpha/history/runs/c613", params={"scope": "run"})
        assert response.status_code == 200
        assert response.json() == {"summary": {"fills": 3}}
        assert str(services.requests[0].url) == (
            "http://127.0.0.1:18601/api/alpha/history/runs/c613?scope=run"
        )

    def test_a_config_save_carries_its_body_and_method(self, console, services):
        services.default = httpx.Response(200, json={"applied_live": ["spot"]})
        response = console.put(
            "/api/alpha/config", json={"values": {"spot": "ETH"}}, headers=CONSOLE
        )
        assert response.json() == {"applied_live": ["spot"]}
        sent = services.requests[0]
        assert sent.method == "PUT"
        assert json.loads(sent.content) == {"values": {"spot": "ETH"}}
        assert sent.headers["content-type"] == "application/json"

    def test_the_service_status_reaches_the_browser_unchanged(self, console, services):
        services.default = httpx.Response(422, json={"detail": ["step_pct must be <= 1"]})
        response = console.put("/api/alpha/config", json={"values": {}}, headers=CONSOLE)
        assert response.status_code == 422
        assert response.json()["detail"] == ["step_pct must be <= 1"]

    def test_the_body_is_passed_through_without_being_reparsed(self, console, services):
        services.default = httpx.Response(
            200,
            content=b"not json at all",
            headers={"content-type": "text/plain", "etag": '"v1"', "cache-control": "no-store"},
        )
        response = console.get("/api/alpha/logs")
        assert response.content == b"not json at all"
        assert response.headers["content-type"].startswith("text/plain")
        assert response.headers["etag"] == '"v1"'
        assert response.headers["cache-control"] == "no-store"

    def test_a_stopped_service_says_how_to_start_it(self, console, services):
        services.default = httpx.ConnectError("connection refused")
        response = console.get("/api/alpha/history/runs")
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert "http://127.0.0.1:18601" in detail
        assert "uv run tbot start alpha:api" in detail

    def test_a_service_killed_mid_request_reads_as_stopped(self, console, services):
        services.default = httpx.RemoteProtocolError("server disconnected")
        assert console.get("/api/alpha/history/runs").status_code == 503

    def test_a_hung_service_is_a_timeout_not_an_outage(self, console, services):
        services.default = httpx.ReadTimeout("slow")
        response = console.get("/api/alpha/history/runs/c613")
        assert response.status_code == 504
        assert "не ответила" in response.json()["detail"]

    def test_an_unknown_service_is_not_forwarded(self, console, services):
        response = console.get("/api/nope/anything")
        assert response.status_code == 404
        assert services.requests == []

    def test_a_unit_added_while_running_is_proxied_on_the_next_load(
        self, console, services, units_root
    ):
        assert console.get("/api/zeta/history/runs").status_code == 404
        zeta = fixture_manifest("beta", id="zeta", api={"process": "api", "port": 18603})
        write_manifest(units_root, zeta)
        registry.forget()
        assert console.get("/api/zeta/history/runs").status_code == 200
        assert str(services.requests[-1].url) == "http://127.0.0.1:18603/api/zeta/history/runs"

    def test_a_removed_unit_stops_being_proxied(self, console, services, units_root):
        (units_root / "config/units/beta.json").unlink()
        registry.forget()
        assert console.get("/api/beta/watchlist").status_code == 404
        assert services.requests == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("service", ["alpha", "beta"])
    @pytest.mark.parametrize("path", ["../config", "history/../../x", "history//runs", ""])
    async def test_a_path_cannot_step_outside_the_service(self, services, service, path):
        forwarded = await proxy.forward(service, "GET", path, "", b"", None)
        assert forwarded.status == 404
        assert services.requests == []

    def test_a_segment_is_quoted_on_the_way_out(self):
        assert proxy.upstream_path("alpha", "history/a b") == "/api/alpha/history/a%20b"

    def test_proxied_answers_carry_the_security_headers(self, console, services):
        response = console.get("/api/alpha/schema")
        assert "default-src 'self'" in response.headers["Content-Security-Policy"]
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"

    def test_each_unit_is_reached_on_its_own_port(self, console, services):
        services.default = httpx.Response(200, json={"day": "2026-09-18", "tickers": []})
        response = console.get(
            "/api/beta/watchlist", params={"day": "2026-09-18", "as_of": "10:00"}
        )
        assert response.json()["day"] == "2026-09-18"
        assert str(services.requests[0].url) == (
            "http://127.0.0.1:18602/api/beta/watchlist?day=2026-09-18&as_of=10%3A00"
        )


class TestShell:
    def test_index_served_without_caching(self, console):
        response = console.get("/")
        assert response.status_code == 200
        assert 'id="unit-nav"' in response.text
        assert 'src="js/app.js"' in response.text
        assert response.headers["cache-control"] == "no-cache"

    def test_the_shell_imports_only_console_modules(self):
        probe = (
            "import sys, tbot_console.web.app; "
            "print('\\n'.join(sorted(m for m in sys.modules if m.startswith('tbot_console'))))"
        )
        loaded = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, check=True
        ).stdout.split()
        assert set(loaded) == SHELL_MODULES


class TestGuards:
    def test_a_foreign_host_name_is_refused(self, services):
        rebound = TestClient(create_app(), base_url="http://evil.example:8420")
        assert rebound.get("/").status_code == 400
        assert rebound.get("/api/alpha/config").status_code == 400
        assert services.requests == []

    @pytest.mark.parametrize("host", ["localhost:8420", "[::1]:8420", "127.0.0.1", "127.0.0.2"])
    def test_loopback_names_are_accepted(self, console, host):
        assert console.get("/", headers={"host": host}).status_code == 200

    @pytest.mark.parametrize(
        ("env", "value"),
        [
            ("TBOT_WEB_ALLOWED_HOSTS", "nas.local, Mac-Mini.local"),
            ("TBOT_WEB_HOST", "mac-mini.local"),
        ],
    )
    def test_extra_host_names_can_be_allowed(self, services, monkeypatch, env, value):
        monkeypatch.setenv(env, value)
        client = TestClient(create_app(), base_url="http://mac-mini.local:8420")
        assert client.get("/").status_code == 200

    def test_a_write_without_the_console_header_is_refused(self, console, services):
        response = console.put("/api/alpha/config", json={"values": {}})
        assert response.status_code == 403
        assert services.requests == []

    def test_a_cross_site_form_post_cannot_touch_processes(self, console):
        response = console.post(
            "/api/control/units/alpha/processes/bot/stop",
            content=b"x=1",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 403


class TestAuth:
    @pytest.fixture
    def secured_client(self, console, monkeypatch):
        monkeypatch.setenv("TBOT_WEB_TOKEN", "s3cret")
        return console

    def test_api_requires_token(self, secured_client, services):
        response = secured_client.get("/api/alpha/config")
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert services.requests == []

    def test_api_rejects_wrong_token(self, secured_client):
        response = secured_client.get(
            "/api/alpha/config", headers={"Authorization": "Bearer wrong"}
        )
        assert response.status_code == 401

    def test_api_rejects_non_bearer_scheme(self, secured_client):
        response = secured_client.get(
            "/api/alpha/config", headers={"Authorization": "Basic s3cret"}
        )
        assert response.status_code == 401

    def test_api_accepts_valid_token(self, secured_client, services):
        response = secured_client.get(
            "/api/alpha/config", headers={"Authorization": "Bearer s3cret"}
        )
        assert response.status_code == 200
        assert "authorization" not in services.requests[0].headers

    def test_control_requires_token(self, secured_client):
        assert secured_client.get("/api/control/units").status_code == 401

    def test_static_served_without_token(self, secured_client):
        assert secured_client.get("/").status_code == 200

    def test_open_when_token_not_configured(self, console):
        assert console.get("/api/alpha/config").status_code == 200


class TestMain:
    def test_main_refuses_non_loopback_without_token(self, monkeypatch):
        from tbot_console.web.app import main

        monkeypatch.setenv("TBOT_WEB_HOST", "0.0.0.0")
        monkeypatch.delenv("TBOT_WEB_TOKEN", raising=False)
        with pytest.raises(SystemExit):
            main()

    @pytest.mark.parametrize(
        ("env", "port"),
        [
            ({}, 8420),
            ({"TBOT_WEB_PORT": "8799"}, 8799),
            ({"TBOT_WEB_PORT": "8799", "TBOT_PORT": "8421"}, 8421),
        ],
    )
    def test_main_listens_where_the_supervisor_says(self, monkeypatch, env, port):
        from tbot_console.web.app import main

        monkeypatch.setattr(procs, "load_env_file", lambda: None)
        monkeypatch.delenv("TBOT_PORT", raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        bound: dict[str, object] = {}
        monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: bound.update(kwargs))
        main()
        assert bound["port"] == port
