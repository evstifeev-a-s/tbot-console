from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from tbot_console.control import procs
from tbot_console.testing import loopback_client
from tbot_console.web import control_api, proxy
from tbot_console.web.app import create_app

CONSOLE = {"X-Tbot-Console": "1"}


class Services:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.answers: dict[str, httpx.Response | Exception] = {}
        self.default: httpx.Response | Exception = httpx.ConnectError("refused")

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.get(request.url.path, self.default)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def services(monkeypatch: pytest.MonkeyPatch, root: Path) -> Services:
    stub = Services()
    control_api._cache.clear()
    monkeypatch.setattr(
        proxy,
        "shared_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(stub.handler)),
    )
    monkeypatch.setattr(procs, "port_holder", lambda port: None)
    return stub


@pytest.fixture
def console(services: Services) -> TestClient:
    return loopback_client(create_app())
