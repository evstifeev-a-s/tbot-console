from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from tbot_console.control.manifest import UnitManifest
from tbot_console.control.registry import load_registry

TIMEOUT = httpx.Timeout(60.0, connect=2.0)
FORWARDED_RESPONSE_HEADERS = ("content-type", "cache-control", "etag", "last-modified")
OFFLINE_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError)


def offline_hint(unit: UnitManifest) -> str:
    url = unit.api_url() or ""
    process = unit.api.process if unit.api is not None else ""
    return (
        f"Служба «{unit.title}» не отвечает на {url}. Запустите её на главной странице консоли "
        f"(кнопка «Запустить») или командой `uv run tbot start {unit.id}:{process}`."
    )


@dataclass(frozen=True, slots=True)
class Forwarded:
    status: int
    content: bytes
    headers: dict[str, str]


_client: httpx.AsyncClient | None = None


def shared_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=TIMEOUT)
    return _client


def _answer(status: int, detail: str) -> Forwarded:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode()
    return Forwarded(status, body, {"content-type": "application/json"})


def upstream_path(unit_id: str, path: str) -> str | None:
    segments = path.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        return None
    return f"/api/{unit_id}/" + "/".join(quote(segment, safe="") for segment in segments)


async def forward(
    service_name: str,
    method: str,
    path: str,
    query: str,
    body: bytes,
    content_type: str | None,
) -> Forwarded:
    unit = load_registry().get(service_name)
    if unit is None or unit.api is None:
        return _answer(404, f"консоль не знает службы «{service_name}»")
    target = upstream_path(unit.id, path)
    if target is None:
        return _answer(404, f"у службы «{unit.title}» нет такого запроса: {path}")
    url = f"{unit.api_url()}{target}" + (f"?{query}" if query else "")
    headers = {"content-type": content_type} if content_type else {}
    try:
        response = await shared_client().request(method, url, content=body or None, headers=headers)
    except OFFLINE_ERRORS:
        return _answer(503, offline_hint(unit))
    except httpx.TimeoutException:
        return _answer(504, f"служба «{unit.title}» не ответила за {TIMEOUT.read:.0f} с")
    except httpx.HTTPError as e:
        return _answer(502, f"служба «{unit.title}» ответила с ошибкой транспорта: {e}")
    forwarded = {
        name: value
        for name in FORWARDED_RESPONSE_HEADERS
        if (value := response.headers.get(name)) is not None
    }
    return Forwarded(response.status_code, response.content, forwarded)
