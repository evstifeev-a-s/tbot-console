from __future__ import annotations

import logging
import os

import pytest
import uvicorn
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tbot_console.control import procs, service
from tbot_console.testing import loopback_client

SECURITY = {"x-content-type-options": "nosniff", "x-frame-options": "DENY"}


@pytest.fixture
def app():
    demo = service.unit_app("demo", "demo")

    @demo.get("/api/demo/boom")
    def boom() -> None:
        raise RuntimeError("boom")

    @demo.get("/api/demo/busy")
    def busy() -> None:
        raise HTTPException(status_code=409, detail="занято", headers={"Retry-After": "5"})

    return demo


@pytest.fixture
def client(app) -> TestClient:
    return loopback_client(app, raise_server_exceptions=False)


def _secured(headers) -> bool:
    return all(headers.get(name) == value for name, value in SECURITY.items()) and (
        headers.get("referrer-policy") == "no-referrer"
    )


def test_health_names_the_unit(client):
    body = client.get("/api/demo/health").json()
    assert body == {
        "status": "ok",
        "unit": "demo",
        "pid": os.getpid(),
        "started_at": service.STARTED_AT,
    }


def test_every_route_lives_under_the_unit_prefix(app):
    paths = {getattr(route, "path", "") for route in app.routes}
    assert {"/api/demo/health", "/api/demo/docs", "/api/demo/openapi.json"} <= paths
    assert all(path.startswith("/api/demo/") for path in paths)


def test_an_unexpected_error_is_a_json_500_with_the_headers(client):
    response = client.get("/api/demo/boom")
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert "RuntimeError: boom" in response.json()["detail"]
    assert _secured(response.headers)


def test_an_http_error_keeps_its_own_headers(client):
    response = client.get("/api/demo/busy")
    assert response.status_code == 409
    assert response.json() == {"detail": "занято"}
    assert response.headers["retry-after"] == "5"
    assert _secured(response.headers)


@pytest.mark.parametrize("path", ["/api/demo/health", "/api/demo/nope"])
def test_ordinary_answers_carry_the_headers(client, path):
    assert _secured(client.get(path).headers)


@pytest.mark.parametrize(
    ("host", "status"),
    [("localhost:1", 200), ("[::1]:1", 200), ("127.0.0.1", 200), ("evil.example", 400)],
)
def test_only_loopback_host_names_are_answered(client, host, status):
    response = client.get("/api/demo/health", headers={"host": host})
    assert response.status_code == status
    assert _secured(response.headers)


@pytest.mark.parametrize(
    ("host", "loopback"),
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("::1", True),
        ("[::1]", True),
        ("0.0.0.0", False),
        ("192.168.1.10", False),
        ("evil.example", False),
    ],
)
def test_loopback_hosts(host, loopback):
    assert service.is_loopback_host(host) is loopback


@pytest.mark.parametrize(
    ("header", "name"),
    [("127.0.0.1:8420", "127.0.0.1"), ("[::1]:8420", "::1"), ("LOCALHOST", "localhost")],
)
def test_host_names_are_parsed(header, name):
    assert service.host_name(header) == name


def test_serve_refuses_a_non_loopback_bind_before_reading_the_env_file(monkeypatch):
    loaded: list[str | None] = []
    monkeypatch.setattr(procs, "load_env_file", lambda unit=None: loaded.append(unit))
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: pytest.fail("bound"))
    monkeypatch.setenv("DEMO_HOST", "0.0.0.0")
    with pytest.raises(SystemExit, match="Refusing to bind 0.0.0.0"):
        service.serve(
            lambda: service.unit_app("demo", "demo"),
            name="demo",
            host_env="DEMO_HOST",
            port_env="DEMO_PORT",
            default_port=1,
        )
    assert loaded == []


def test_serve_refuses_a_non_loopback_bind_named_in_the_env_file(monkeypatch):
    monkeypatch.delenv("DEMO_HOST", raising=False)
    monkeypatch.setattr(
        procs, "load_env_file", lambda unit=None: monkeypatch.setenv("DEMO_HOST", "0.0.0.0")
    )
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: pytest.fail("bound"))
    with pytest.raises(SystemExit, match="Refusing to bind 0.0.0.0"):
        service.serve(
            lambda: service.unit_app("demo", "demo"),
            name="demo",
            host_env="DEMO_HOST",
            port_env="DEMO_PORT",
            default_port=1,
        )


@pytest.mark.parametrize(
    ("env", "port"),
    [
        ({}, 8500),
        ({"DEMO_PORT": "8501"}, 8501),
        ({"DEMO_PORT": "8501", "TBOT_PORT": "8502"}, 8502),
    ],
)
def test_serve_listens_where_the_supervisor_says(monkeypatch, env, port):
    httpx_logger = logging.getLogger("httpx")
    monkeypatch.setattr(httpx_logger, "level", httpx_logger.level)
    monkeypatch.setattr(procs, "load_env_file", lambda unit=None: None)
    for key in ("DEMO_PORT", "TBOT_PORT"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    bound: dict[str, object] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: bound.update(kwargs))
    service.serve(
        lambda: service.unit_app("demo", "demo"),
        name="demo",
        host_env="DEMO_HOST",
        port_env="DEMO_PORT",
        default_port=8500,
    )
    assert bound["port"] == port


@pytest.mark.parametrize("unit", [None, "demo"])
def test_serve_reads_the_env_file_for_its_own_unit(monkeypatch, unit):
    httpx_logger = logging.getLogger("httpx")
    monkeypatch.setattr(httpx_logger, "level", httpx_logger.level)
    loaded: list[str | None] = []
    monkeypatch.setattr(procs, "load_env_file", lambda unit=None: loaded.append(unit))
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: None)
    service.serve(
        lambda: service.unit_app("demo", "demo"),
        name="demo",
        host_env="DEMO_HOST",
        port_env="DEMO_PORT",
        default_port=8500,
        unit=unit,
    )
    assert loaded == [unit]
