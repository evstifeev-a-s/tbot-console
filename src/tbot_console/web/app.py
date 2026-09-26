from __future__ import annotations

import os
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from tbot_console.control import paths
from tbot_console.control.registry import load_registry
from tbot_console.control.service import SECURITY_HEADERS as SERVICE_HEADERS
from tbot_console.control.service import host_name, is_loopback_host
from tbot_console.web import control_api, proxy

STATIC_DIR = Path(__file__).parent / "static"

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
    **SERVICE_HEADERS,
}

PROXIED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CONSOLE_HEADER = "X-Tbot-Console"


def configured_token() -> str | None:
    token = os.getenv("TBOT_WEB_TOKEN", "").strip()
    return token or None


def host_allowed(name: str) -> bool:
    if is_loopback_host(name):
        return True
    configured = (
        os.getenv("TBOT_WEB_HOST", ""),
        *os.getenv("TBOT_WEB_ALLOWED_HOSTS", "").split(","),
    )
    return name in {entry.strip().lower() for entry in configured if entry.strip()}


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


def _refuse(status: int, detail: str, extra: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"detail": detail},
        headers={**SECURITY_HEADERS, **(extra or {})},
    )


def unit_asset(ui: str, path: str) -> Path | None:
    base = load_registry().ui_base(ui)
    parts = path.split("/")
    if base is None or any(not part or part.startswith(".") or "\x00" in part for part in parts):
        return None
    directory = paths.ui_dir(base, ui).resolve()
    target = directory.joinpath(*parts).resolve()
    return target if target.is_file() and target.is_relative_to(directory) else None


def create_app() -> FastAPI:
    app = FastAPI(title="Trading console", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not host_allowed(host_name(request.headers.get("host", ""))):
            return _refuse(400, "консоль отвечает только на своём адресе")
        is_api = request.url.path.startswith("/api")
        unmarked = request.headers.get(CONSOLE_HEADER) != "1"
        if is_api and request.method not in SAFE_METHODS and unmarked:
            return _refuse(403, "запрос без заголовка консоли отклонён (защита от чужих сайтов)")
        token = configured_token()
        if token is not None and is_api:
            provided = _bearer_token(request)
            if provided is None or not secrets.compare_digest(provided.encode(), token.encode()):
                return _refuse(401, "missing or invalid token", {"WWW-Authenticate": "Bearer"})
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if not is_api:
            response.headers["Cache-Control"] = "no-cache"
        return response

    app.include_router(control_api.router)

    @app.api_route("/api/{service}/{path:path}", methods=PROXIED_METHODS)
    async def forward_to_service(service: str, path: str, request: Request) -> Response:
        forwarded = await proxy.forward(
            service,
            request.method,
            path,
            request.url.query,
            await request.body(),
            request.headers.get("content-type"),
        )
        return Response(
            content=forwarded.content, status_code=forwarded.status, headers=forwarded.headers
        )

    @app.get("/js/units/{ui}/{path:path}")
    def serve_unit_asset(ui: str, path: str) -> Response:
        found = unit_asset(ui, path)
        return FileResponse(found) if found else _refuse(404, "нет такого файла интерфейса")

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def main() -> None:
    import uvicorn

    from tbot_console.control.procs import PORT_ENV, load_env_file

    load_env_file()

    host = os.getenv("TBOT_WEB_HOST", "127.0.0.1")
    port = int(os.getenv(PORT_ENV) or os.getenv("TBOT_WEB_PORT") or 8420)
    if not is_loopback_host(host) and configured_token() is None:
        raise SystemExit(
            f"Refusing to bind {host}: set TBOT_WEB_TOKEN before exposing the console "
            "beyond localhost"
        )
    uvicorn.run(create_app(), host=host, port=port, access_log=False)


if __name__ == "__main__":
    main()
