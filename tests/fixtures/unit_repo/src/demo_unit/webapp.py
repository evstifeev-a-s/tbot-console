from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI

from tbot_console.control import service
from tbot_console.control.status import metric, status_payload

UNIT = "demo"
PREFIX = f"/api/{UNIT}"


def greeting() -> str:
    return os.getenv("DEMO_GREETING", "Привет из демо-репо")


def create_app() -> FastAPI:
    app = service.unit_app(UNIT, "Demo unit")

    @app.get(f"{PREFIX}/status")
    async def status() -> dict[str, Any]:
        return status_payload("ok", "Служба отвечает", [metric("Приветствие", greeting())], [])

    @app.get(f"{PREFIX}/hello")
    async def hello() -> dict[str, str]:
        return {"text": greeting()}

    return app


def main() -> None:
    service.serve(
        create_app,
        name="Demo unit",
        host_env="DEMO_WEB_HOST",
        port_env="DEMO_WEB_PORT",
        default_port=18603,
        unit=UNIT,
    )


if __name__ == "__main__":
    main()
