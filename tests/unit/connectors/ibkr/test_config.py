from __future__ import annotations

import pytest

from tbot_console.connectors.ibkr.config import (
    LIVE_TRADING_ENV,
    Gateway,
    IBKRConfig,
    MarketDataType,
    default_port,
)
from tbot_console.core.exceptions import ConfigurationError
from tbot_console.core.types import ServerType

IBKR_ENV = (
    "IBKR_HOST",
    "IBKR_PORT",
    "IBKR_CLIENT_ID",
    "IBKR_ACCOUNT",
    "IBKR_TIMEOUT",
    "IBKR_WRITABLE",
    "IBKR_MARKET_DATA_TYPE",
    LIVE_TRADING_ENV,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch):
    for name in IBKR_ENV:
        monkeypatch.delenv(name, raising=False)


class TestPorts:
    @pytest.mark.parametrize(
        ("gateway", "server_type", "expected"),
        [
            (Gateway.TWS, ServerType.TEST, 7497),
            (Gateway.TWS, ServerType.PRODUCTION, 7496),
            (Gateway.GATEWAY, ServerType.TEST, 4002),
            (Gateway.GATEWAY, ServerType.PRODUCTION, 4001),
        ],
    )
    def test_table(self, gateway: Gateway, server_type: ServerType, expected: int):
        assert default_port(gateway, server_type) == expected

    def test_paper_ports_are_recognised(self):
        assert IBKRConfig(port=7497).is_paper_port
        assert IBKRConfig(port=4002).is_paper_port
        assert not IBKRConfig(port=7496).is_paper_port

    def test_non_positive_port_rejected(self):
        with pytest.raises(ConfigurationError, match="port"):
            IBKRConfig(port=0)


class TestDefaults:
    def test_defaults_are_paper_and_readonly(self):
        config = IBKRConfig()
        assert config.server_type is ServerType.TEST
        assert config.readonly
        assert config.is_paper_port
        assert config.host == "127.0.0.1"

    def test_default_feed_is_delayed_frozen(self):
        assert IBKRConfig().market_data_type is MarketDataType.DELAYED_FROZEN


class TestLiveTradingGuard:
    def test_writable_live_session_refused(self):
        with pytest.raises(ConfigurationError, match=LIVE_TRADING_ENV):
            IBKRConfig(port=7496, server_type=ServerType.PRODUCTION, readonly=False)

    def test_writable_live_session_allowed_with_flag(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(LIVE_TRADING_ENV, "true")
        config = IBKRConfig(port=7496, server_type=ServerType.PRODUCTION, readonly=False)
        assert not config.readonly

    @pytest.mark.parametrize("port", [7497, 4002])
    def test_writable_paper_session_needs_no_flag(self, port: int):
        config = IBKRConfig(port=port, server_type=ServerType.TEST, readonly=False)
        assert not config.readonly

    @pytest.mark.parametrize("port", [7496, 4001, 7500])
    def test_writable_non_paper_port_refused_whatever_the_server_type(self, port: int):
        with pytest.raises(ConfigurationError, match=LIVE_TRADING_ENV):
            IBKRConfig(port=port, server_type=ServerType.TEST, readonly=False)

    def test_writable_non_paper_port_allowed_with_flag(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(LIVE_TRADING_ENV, "true")
        assert not IBKRConfig(port=7496, server_type=ServerType.TEST, readonly=False).readonly

    def test_writable_paper_port_against_production_is_refused(self):
        with pytest.raises(ConfigurationError, match=LIVE_TRADING_ENV):
            IBKRConfig(port=7497, server_type=ServerType.PRODUCTION, readonly=False)

    def test_readonly_live_session_allowed(self):
        config = IBKRConfig(port=7496, server_type=ServerType.PRODUCTION)
        assert config.readonly


class TestFromEnv:
    def test_defaults_to_paper_tws(self):
        config = IBKRConfig.from_env()
        assert config.port == 7497
        assert config.readonly
        assert config.server_type is ServerType.TEST

    def test_gateway_and_server_type_pick_the_port(self):
        config = IBKRConfig.from_env(ServerType.PRODUCTION, Gateway.GATEWAY)
        assert config.port == 4001

    def test_env_overrides(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("IBKR_HOST", "10.0.0.5")
        monkeypatch.setenv("IBKR_PORT", "4002")
        monkeypatch.setenv("IBKR_CLIENT_ID", "42")
        monkeypatch.setenv("IBKR_ACCOUNT", "DUTEST")
        monkeypatch.setenv("IBKR_TIMEOUT", "3.5")
        monkeypatch.setenv("IBKR_MARKET_DATA_TYPE", "1")

        config = IBKRConfig.from_env()
        assert config.host == "10.0.0.5"
        assert config.port == 4002
        assert config.client_id == 42
        assert config.account == "DUTEST"
        assert config.timeout == 3.5
        assert config.market_data_type is MarketDataType.LIVE

    def test_writable_flag(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("IBKR_WRITABLE", "true")
        assert not IBKRConfig.from_env().readonly

    def test_writable_flag_against_live_still_needs_the_override(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("IBKR_WRITABLE", "true")
        with pytest.raises(ConfigurationError, match=LIVE_TRADING_ENV):
            IBKRConfig.from_env(ServerType.PRODUCTION)

    def test_force_readonly_ignores_the_writable_flag(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("IBKR_WRITABLE", "true")
        config = IBKRConfig.from_env(ServerType.PRODUCTION, force_readonly=True)
        assert config.readonly and config.port == 7496

    def test_non_integer_port_is_a_configuration_error(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("IBKR_PORT", "seven")
        with pytest.raises(ConfigurationError, match="IBKR_PORT"):
            IBKRConfig.from_env()

    def test_non_numeric_timeout_is_a_configuration_error(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("IBKR_TIMEOUT", "abc")
        with pytest.raises(ConfigurationError, match="IBKR_TIMEOUT"):
            IBKRConfig.from_env()

    def test_unknown_market_data_type_is_a_configuration_error(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("IBKR_MARKET_DATA_TYPE", "9")
        with pytest.raises(ConfigurationError, match="IBKR_MARKET_DATA_TYPE"):
            IBKRConfig.from_env()

    def test_writable_flag_against_a_live_port_still_needs_the_override(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("IBKR_WRITABLE", "true")
        monkeypatch.setenv("IBKR_PORT", "7496")
        with pytest.raises(ConfigurationError, match=LIVE_TRADING_ENV):
            IBKRConfig.from_env()
