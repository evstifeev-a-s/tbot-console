from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tbot_console.control import ops
from tbot_console.web import proxy

PROXY_VARIABLES = {
    "HTTP_PROXY": "http://127.0.0.1:9",
    "http_proxy": "http://127.0.0.1:9",
    "ALL_PROXY": "socks5://127.0.0.1:9",
    "all_proxy": "socks5://127.0.0.1:9",
}


class Answer(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def service_port() -> Iterator[int]:
    server = HTTPServer(("127.0.0.1", 0), Answer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield int(server.server_address[1])
    server.shutdown()
    server.server_close()


@pytest.fixture
def proxied_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in PROXY_VARIABLES.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setattr(proxy, "_client", None)


@pytest.mark.usefixtures("proxied_shell")
async def test_the_console_reaches_a_loopback_service_past_the_shell_proxy(service_port: int):
    client = proxy.shared_client()
    try:
        response = await client.get(f"http://127.0.0.1:{service_port}/api/demo/health")
    finally:
        await client.aclose()
    assert response.status_code == 200


@pytest.mark.usefixtures("proxied_shell")
def test_readiness_is_probed_past_the_shell_proxy(service_port: int):
    assert ops._http_ok(service_port, "/api/demo/health")
