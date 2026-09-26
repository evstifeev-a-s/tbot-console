from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum, IntEnum

from tbot_console.core.exceptions import ConfigurationError
from tbot_console.core.types import ServerType

DEFAULT_HOST = "127.0.0.1"
DEFAULT_CLIENT_ID = 17
DEFAULT_TIMEOUT = 8.0

LIVE_TRADING_ENV = "IBKR_ALLOW_LIVE_TRADING"


class Gateway(str, Enum):
    TWS = "tws"
    GATEWAY = "gateway"


class MarketDataType(IntEnum):
    LIVE = 1
    FROZEN = 2
    DELAYED = 3
    DELAYED_FROZEN = 4


PORTS: dict[tuple[Gateway, ServerType], int] = {
    (Gateway.TWS, ServerType.TEST): 7497,
    (Gateway.TWS, ServerType.PRODUCTION): 7496,
    (Gateway.GATEWAY, ServerType.TEST): 4002,
    (Gateway.GATEWAY, ServerType.PRODUCTION): 4001,
}

PAPER_PORTS = frozenset({7497, 4002})


def default_port(gateway: Gateway, server_type: ServerType) -> int:
    return PORTS[(gateway, server_type)]


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() == "true"


def _env_int(name: str, fallback: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return fallback
    try:
        return int(raw)
    except ValueError:
        raise ConfigurationError(f"{name} must be an integer, got {raw!r}") from None


def _env_float(name: str, fallback: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return fallback
    try:
        return float(raw)
    except ValueError:
        raise ConfigurationError(f"{name} must be a number, got {raw!r}") from None


def _env_market_data_type(name: str) -> MarketDataType:
    value = _env_int(name, int(MarketDataType.DELAYED_FROZEN))
    try:
        return MarketDataType(value)
    except ValueError:
        known = ", ".join(f"{t.value}={t.name.lower()}" for t in MarketDataType)
        raise ConfigurationError(f"{name} must be one of {known}, got {value}") from None


@dataclass(frozen=True, slots=True)
class IBKRConfig:
    host: str = DEFAULT_HOST
    port: int = PORTS[(Gateway.TWS, ServerType.TEST)]
    client_id: int = DEFAULT_CLIENT_ID
    account: str = ""
    server_type: ServerType = ServerType.TEST
    readonly: bool = True
    timeout: float = DEFAULT_TIMEOUT
    market_data_type: MarketDataType = MarketDataType.DELAYED_FROZEN

    def __post_init__(self) -> None:
        if self.port <= 0:
            raise ConfigurationError(f"IBKR port must be positive, got {self.port}")
        if self.readonly:
            return
        if self.is_paper_port and self.server_type is not ServerType.PRODUCTION:
            return
        if not _env_flag(LIVE_TRADING_ENV):
            raise ConfigurationError(
                "Refusing a writable IBKR session that is not provably against a paper "
                f"account: port {self.port} ({self.server_type.value}), paper ports are "
                f"{sorted(PAPER_PORTS)}. Set {LIVE_TRADING_ENV}=true to override, or keep "
                "readonly=True."
            )

    @property
    def is_paper_port(self) -> bool:
        return self.port in PAPER_PORTS

    @classmethod
    def from_env(
        cls,
        server_type: ServerType = ServerType.TEST,
        gateway: Gateway = Gateway.TWS,
        *,
        force_readonly: bool = False,
    ) -> IBKRConfig:
        return cls(
            host=os.getenv("IBKR_HOST", DEFAULT_HOST),
            port=_env_int("IBKR_PORT", default_port(gateway, server_type)),
            client_id=_env_int("IBKR_CLIENT_ID", DEFAULT_CLIENT_ID),
            account=os.getenv("IBKR_ACCOUNT", ""),
            server_type=server_type,
            readonly=force_readonly or not _env_flag("IBKR_WRITABLE"),
            timeout=_env_float("IBKR_TIMEOUT", DEFAULT_TIMEOUT),
            market_data_type=_env_market_data_type("IBKR_MARKET_DATA_TYPE"),
        )
