from __future__ import annotations

import asyncio
import dataclasses
import os
from collections.abc import AsyncIterator

import pytest

from tbot_console.connectors.ibkr.client import IBKRClient
from tbot_console.connectors.ibkr.config import IBKRConfig

PROBE_TIMEOUT = 1.0
TEST_CLIENT_ID_ENV = "IBKR_TEST_CLIENT_ID"
DEFAULT_TEST_CLIENT_ID = 9917


async def port_is_open(host: str, port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=PROBE_TIMEOUT
        )
    except (TimeoutError, OSError):
        return False
    writer.close()
    await writer.wait_closed()
    return True


def paper_client_id() -> int:
    return int(os.getenv(TEST_CLIENT_ID_ENV, str(DEFAULT_TEST_CLIENT_ID)))


async def paper_config() -> IBKRConfig:
    config = dataclasses.replace(IBKRConfig.from_env(), client_id=paper_client_id())
    if not config.is_paper_port:
        pytest.skip(f"IBKR integration runs against a paper gateway only, got port {config.port}")
    if not await port_is_open(config.host, config.port):
        pytest.skip(
            f"No IBKR gateway listening on {config.host}:{config.port} — "
            "start TWS or IB Gateway in paper mode with the API enabled"
        )
    return config


@pytest.fixture
async def client() -> AsyncIterator[IBKRClient]:
    connected = IBKRClient(await paper_config())
    await connected.connect()
    try:
        yield connected
    finally:
        await connected.disconnect()
