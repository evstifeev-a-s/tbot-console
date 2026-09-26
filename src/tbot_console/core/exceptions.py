"""Custom exceptions for the trading system."""


class TradingSystemError(Exception):
    """Base exception for trading system."""


class ExchangeError(TradingSystemError):
    """Base exception for exchange-related errors."""


class ExchangeConnectionError(ExchangeError):
    """Exchange connection error."""


class ExchangeRequestError(ExchangeConnectionError):
    """Exchange rejected a request and named a venue error code."""

    def __init__(self, message: str, code: int | None = None, reason: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.reason = reason


class ExchangeLockError(ExchangeConnectionError):
    """Exchange is temporarily locked (e.g. settlement, maintenance)."""


class ExchangeAuthError(ExchangeError):
    """Exchange authentication error."""


class ConfigurationError(TradingSystemError):
    """Configuration error."""


class StreamingError(TradingSystemError):
    """Base exception for streaming-related errors."""


class MissingSequenceNumber(StreamingError):
    """Exception raised when sequence gap is detected in orderbook updates."""


class SubscriptionError(StreamingError):
    """Exception raised when subscription to a channel fails."""


class AuthenticationError(StreamingError):
    """Exception raised when authentication fails."""


class CallbackError(StreamingError):
    """Exception raised when a callback function encounters an error."""


class ConnectionTimeoutError(StreamingError):
    """Exception raised when connection timeout is detected."""
