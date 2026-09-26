from __future__ import annotations

import ipaddress
import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from tbot_console.control.logs import configure_logging

LOG = logging.getLogger(__name__)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}

STARTED_AT = time.time()


def is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def host_name(header: str) -> str:
    value = header.strip().lower()
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def unit_app(unit: str, title: str) -> FastAPI:
    prefix = f"/api/{unit}"
    app = FastAPI(
        title=title,
        docs_url=f"{prefix}/docs",
        openapi_url=f"{prefix}/openapi.json",
        swagger_ui_oauth2_redirect_url=f"{prefix}/docs/oauth2-redirect",
        redoc_url=None,
    )

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not is_loopback_host(host_name(request.headers.get("host", ""))):
            return JSONResponse(
                status_code=400,
                content={"detail": "служба отвечает только на локальном адресе"},
                headers=SECURITY_HEADERS,
            )
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        LOG.error("Unhandled error on %s", request.url.path, exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={"detail": f"внутренняя ошибка службы: {type(exc).__name__}: {exc}"},
            headers=SECURITY_HEADERS,
        )

    @app.get(f"{prefix}/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "unit": unit, "pid": os.getpid(), "started_at": STARTED_AT}

    return app


def setup_logging() -> None:
    configure_logging()
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _bind_host(host_env: str, name: str) -> str:
    host = os.getenv(host_env, "127.0.0.1")
    if not is_loopback_host(host):
        raise SystemExit(
            f"Refusing to bind {host}: {name} has no auth of its own and is reached only "
            "through the console"
        )
    return host


def serve(
    factory: Callable[[], FastAPI],
    *,
    name: str,
    host_env: str,
    port_env: str,
    default_port: int,
    unit: str | None = None,
) -> None:
    import uvicorn

    from tbot_console.control.procs import PORT_ENV, load_env_file

    _bind_host(host_env, name)
    load_env_file(unit)
    host = _bind_host(host_env, name)
    setup_logging()
    port = int(os.getenv(PORT_ENV) or os.getenv(port_env) or default_port)
    LOG.info("%s on http://%s:%s", name, host, port)
    uvicorn.run(factory(), host=host, port=port, access_log=False)
