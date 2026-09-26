"""Unit tests for core types module."""

import pytest

from tbot_console.core.types import CallbackType, ConnectionType, ServerType


def test_connection_type_enum_defined():
    """Test that ConnectionType enum is defined with all required values."""
    assert hasattr(ConnectionType, "MARKET_DATA")
    assert hasattr(ConnectionType, "TRADING")
    assert hasattr(ConnectionType, "USER_DATA")


def test_connection_type_string_values():
    """Test ConnectionType enum string values."""
    assert ConnectionType.MARKET_DATA.value == "market_data"
    assert ConnectionType.TRADING.value == "trading"
    assert ConnectionType.USER_DATA.value == "user_data"


def test_connection_type_is_string_enum():
    """Test that ConnectionType values are strings."""
    assert isinstance(ConnectionType.MARKET_DATA.value, str)
    assert isinstance(ConnectionType.TRADING.value, str)
    assert isinstance(ConnectionType.USER_DATA.value, str)


def test_connection_type_all_members():
    """Test that ConnectionType has exactly the expected members."""
    expected_members = {"MARKET_DATA", "TRADING", "USER_DATA"}
    actual_members = {member.name for member in ConnectionType}
    assert actual_members == expected_members


def test_server_type_enum_defined():
    """Test that ServerType enum is defined with all required values."""
    assert hasattr(ServerType, "TEST")
    assert hasattr(ServerType, "PRODUCTION")


def test_server_type_string_values():
    """Test ServerType enum string values."""
    assert ServerType.TEST.value == "test"
    assert ServerType.PRODUCTION.value == "production"


def test_server_type_is_string_enum():
    """Test that ServerType values are strings."""
    assert isinstance(ServerType.TEST.value, str)
    assert isinstance(ServerType.PRODUCTION.value, str)


def test_server_type_all_members():
    """Test that ServerType has exactly the expected members."""
    expected_members = {"TEST", "PRODUCTION"}
    actual_members = {member.name for member in ServerType}
    assert actual_members == expected_members


def test_callback_type_enum_defined():
    """Test that CallbackType enum is defined with all required values."""
    assert hasattr(CallbackType, "TRADE")
    assert hasattr(CallbackType, "ORDERBOOK")
    assert hasattr(CallbackType, "TICKER")
    assert hasattr(CallbackType, "USER_ORDER")
    assert hasattr(CallbackType, "USER_TRADE")
    assert hasattr(CallbackType, "USER_BALANCE")


def test_callback_type_string_values():
    """Test CallbackType enum string values."""
    assert CallbackType.TRADE.value == "trade"
    assert CallbackType.ORDERBOOK.value == "orderbook"
    assert CallbackType.TICKER.value == "ticker"
    assert CallbackType.USER_ORDER.value == "user_order"
    assert CallbackType.USER_TRADE.value == "user_trade"
    assert CallbackType.USER_BALANCE.value == "user_balance"


def test_callback_type_is_string_enum():
    """Test that CallbackType values are strings."""
    assert isinstance(CallbackType.TRADE.value, str)
    assert isinstance(CallbackType.ORDERBOOK.value, str)
    assert isinstance(CallbackType.TICKER.value, str)
    assert isinstance(CallbackType.USER_ORDER.value, str)
    assert isinstance(CallbackType.USER_TRADE.value, str)
    assert isinstance(CallbackType.USER_BALANCE.value, str)


def test_callback_type_all_members():
    """Test that CallbackType has exactly the expected members."""
    expected_members = {
        "TRADE",
        "ORDERBOOK",
        "TICKER",
        "TICKER_RAW",
        "USER_ORDER",
        "USER_ORDER_RAW",
        "USER_TRADE",
        "USER_TRADE_RAW",
        "USER_BALANCE",
        "USER_POSITION",
    }
    actual_members = {member.name for member in CallbackType}
    assert actual_members == expected_members


def test_connection_type_iteration():
    """Test that ConnectionType can be iterated."""
    connection_types = list(ConnectionType)
    assert len(connection_types) == 3
    assert ConnectionType.MARKET_DATA in connection_types
    assert ConnectionType.TRADING in connection_types
    assert ConnectionType.USER_DATA in connection_types


def test_server_type_iteration():
    """Test that ServerType can be iterated."""
    server_types = list(ServerType)
    assert len(server_types) == 2
    assert ServerType.TEST in server_types
    assert ServerType.PRODUCTION in server_types


def test_callback_type_iteration():
    """Test that CallbackType can be iterated."""
    callback_types = list(CallbackType)
    assert len(callback_types) == 10
    assert CallbackType.TRADE in callback_types
    assert CallbackType.ORDERBOOK in callback_types
    assert CallbackType.TICKER in callback_types
    assert CallbackType.TICKER_RAW in callback_types
    assert CallbackType.USER_ORDER in callback_types
    assert CallbackType.USER_ORDER_RAW in callback_types
    assert CallbackType.USER_TRADE in callback_types
    assert CallbackType.USER_TRADE_RAW in callback_types
    assert CallbackType.USER_BALANCE in callback_types
    assert CallbackType.USER_POSITION in callback_types


def test_connection_type_comparison():
    """Test ConnectionType enum comparison."""
    assert ConnectionType.MARKET_DATA == ConnectionType.MARKET_DATA
    assert ConnectionType.MARKET_DATA != ConnectionType.TRADING
    assert ConnectionType.TRADING != ConnectionType.USER_DATA


def test_server_type_comparison():
    """Test ServerType enum comparison."""
    assert ServerType.TEST == ServerType.TEST
    assert ServerType.TEST != ServerType.PRODUCTION


def test_callback_type_comparison():
    """Test CallbackType enum comparison."""
    assert CallbackType.TRADE == CallbackType.TRADE
    assert CallbackType.TRADE != CallbackType.ORDERBOOK
    assert CallbackType.TICKER != CallbackType.USER_ORDER


def test_connection_type_string_comparison():
    """Test ConnectionType can be compared with string values."""
    assert ConnectionType.MARKET_DATA == "market_data"
    assert ConnectionType.TRADING == "trading"
    assert ConnectionType.USER_DATA == "user_data"


def test_server_type_string_comparison():
    """Test ServerType can be compared with string values."""
    assert ServerType.TEST == "test"
    assert ServerType.PRODUCTION == "production"


def test_callback_type_string_comparison():
    """Test CallbackType can be compared with string values."""
    assert CallbackType.TRADE == "trade"
    assert CallbackType.ORDERBOOK == "orderbook"
    assert CallbackType.TICKER == "ticker"
    assert CallbackType.USER_ORDER == "user_order"
    assert CallbackType.USER_TRADE == "user_trade"
    assert CallbackType.USER_BALANCE == "user_balance"


def test_connection_type_in_dict():
    """Test ConnectionType can be used as dict keys."""
    config = {
        ConnectionType.MARKET_DATA: {"timeout": 120},
        ConnectionType.TRADING: {"timeout": 30},
        ConnectionType.USER_DATA: {"timeout": 60},
    }
    assert config[ConnectionType.MARKET_DATA]["timeout"] == 120
    assert config[ConnectionType.TRADING]["timeout"] == 30
    assert config[ConnectionType.USER_DATA]["timeout"] == 60


def test_server_type_in_dict():
    """Test ServerType can be used as dict keys."""
    endpoints = {
        ServerType.TEST: "wss://test.example.com",
        ServerType.PRODUCTION: "wss://prod.example.com",
    }
    assert endpoints[ServerType.TEST] == "wss://test.example.com"
    assert endpoints[ServerType.PRODUCTION] == "wss://prod.example.com"


def test_callback_type_in_dict():
    """Test CallbackType can be used as dict keys."""
    handlers = {
        CallbackType.TRADE: lambda x: x,
        CallbackType.ORDERBOOK: lambda x: x,
        CallbackType.TICKER: lambda x: x,
    }
    assert CallbackType.TRADE in handlers
    assert CallbackType.ORDERBOOK in handlers
    assert CallbackType.TICKER in handlers


def test_connection_type_repr():
    """Test ConnectionType string representation."""
    assert "MARKET_DATA" in repr(ConnectionType.MARKET_DATA)
    assert "TRADING" in repr(ConnectionType.TRADING)
    assert "USER_DATA" in repr(ConnectionType.USER_DATA)


def test_server_type_repr():
    """Test ServerType string representation."""
    assert "TEST" in repr(ServerType.TEST)
    assert "PRODUCTION" in repr(ServerType.PRODUCTION)


def test_callback_type_repr():
    """Test CallbackType string representation."""
    assert "TRADE" in repr(CallbackType.TRADE)
    assert "ORDERBOOK" in repr(CallbackType.ORDERBOOK)
    assert "TICKER" in repr(CallbackType.TICKER)


def test_enums_are_immutable():
    """Test that enum values cannot be modified."""
    with pytest.raises(AttributeError):
        ConnectionType.MARKET_DATA = "modified"

    with pytest.raises(AttributeError):
        ServerType.TEST = "modified"

    with pytest.raises(AttributeError):
        CallbackType.TRADE = "modified"


def test_connection_type_from_value():
    """Test creating ConnectionType from string value."""
    assert ConnectionType("market_data") == ConnectionType.MARKET_DATA
    assert ConnectionType("trading") == ConnectionType.TRADING
    assert ConnectionType("user_data") == ConnectionType.USER_DATA


def test_server_type_from_value():
    """Test creating ServerType from string value."""
    assert ServerType("test") == ServerType.TEST
    assert ServerType("production") == ServerType.PRODUCTION


def test_callback_type_from_value():
    """Test creating CallbackType from string value."""
    assert CallbackType("trade") == CallbackType.TRADE
    assert CallbackType("orderbook") == CallbackType.ORDERBOOK
    assert CallbackType("ticker") == CallbackType.TICKER
    assert CallbackType("user_order") == CallbackType.USER_ORDER
    assert CallbackType("user_trade") == CallbackType.USER_TRADE
    assert CallbackType("user_balance") == CallbackType.USER_BALANCE


def test_invalid_connection_type_value():
    """Test that invalid ConnectionType value raises ValueError."""
    with pytest.raises(ValueError):
        ConnectionType("invalid")


def test_invalid_server_type_value():
    """Test that invalid ServerType value raises ValueError."""
    with pytest.raises(ValueError):
        ServerType("invalid")


def test_invalid_callback_type_value():
    """Test that invalid CallbackType value raises ValueError."""
    with pytest.raises(ValueError):
        CallbackType("invalid")
