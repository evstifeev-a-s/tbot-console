"""Unit tests for core types module."""

import pytest

from tbot_console.core import exceptions
from tbot_console.core.types import CallbackType, ConnectionType, ServerType

LEGACY = [
    ConnectionType,
    CallbackType,
    exceptions.ExchangeLockError,
    exceptions.ExchangeAuthError,
    exceptions.StreamingError,
    exceptions.MissingSequenceNumber,
    exceptions.SubscriptionError,
    exceptions.AuthenticationError,
    exceptions.CallbackError,
    exceptions.ConnectionTimeoutError,
]


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


def test_server_type_iteration():
    """Test that ServerType can be iterated."""
    server_types = list(ServerType)
    assert len(server_types) == 2
    assert ServerType.TEST in server_types
    assert ServerType.PRODUCTION in server_types


def test_server_type_comparison():
    """Test ServerType enum comparison."""
    assert ServerType.TEST == ServerType.TEST
    assert ServerType.TEST != ServerType.PRODUCTION


def test_server_type_string_comparison():
    """Test ServerType can be compared with string values."""
    assert ServerType.TEST == "test"
    assert ServerType.PRODUCTION == "production"


def test_server_type_in_dict():
    """Test ServerType can be used as dict keys."""
    endpoints = {
        ServerType.TEST: "wss://test.example.com",
        ServerType.PRODUCTION: "wss://prod.example.com",
    }
    assert endpoints[ServerType.TEST] == "wss://test.example.com"
    assert endpoints[ServerType.PRODUCTION] == "wss://prod.example.com"


def test_server_type_repr():
    """Test ServerType string representation."""
    assert "TEST" in repr(ServerType.TEST)
    assert "PRODUCTION" in repr(ServerType.PRODUCTION)


def test_server_type_from_value():
    """Test creating ServerType from string value."""
    assert ServerType("test") == ServerType.TEST
    assert ServerType("production") == ServerType.PRODUCTION


def test_invalid_server_type_value():
    """Test that invalid ServerType value raises ValueError."""
    with pytest.raises(ValueError):
        ServerType("invalid")


def test_server_type_is_not_deprecated():
    assert not hasattr(ServerType, "__deprecated__")


@pytest.mark.parametrize("legacy", LEGACY, ids=lambda legacy: legacy.__name__)
def test_names_the_platform_never_uses_are_deprecated(legacy: type):
    assert "goes away in tbot-console 2.0.0" in legacy.__deprecated__


def test_using_a_legacy_name_warns_but_still_works():
    with pytest.warns(DeprecationWarning, match="ConnectionType"):
        assert ConnectionType("trading") is ConnectionType.TRADING
    with pytest.warns(DeprecationWarning, match="ConnectionTimeoutError"):
        error = exceptions.ConnectionTimeoutError("late")
    assert isinstance(error, exceptions.StreamingError)


def test_the_hierarchy_the_connector_raises_is_not_deprecated():
    for kept in (
        exceptions.TradingSystemError,
        exceptions.ExchangeError,
        exceptions.ExchangeConnectionError,
        exceptions.ExchangeRequestError,
        exceptions.ConfigurationError,
    ):
        assert not hasattr(kept, "__deprecated__")
