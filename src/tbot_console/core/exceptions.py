"""Custom exceptions for the trading system."""

from warnings import deprecated


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


@deprecated("ExchangeLockError is never raised by the platform and goes away in tbot-console 2.0.0")
class ExchangeLockError(ExchangeConnectionError):
    """Exchange is temporarily locked (e.g. settlement, maintenance)."""


@deprecated("ExchangeAuthError is never raised by the platform and goes away in tbot-console 2.0.0")
class ExchangeAuthError(ExchangeError):
    """Exchange authentication error."""


class ConfigurationError(TradingSystemError):
    """Configuration error."""


class StreamingError(TradingSystemError):
    """Base exception for streaming-related errors."""


@deprecated(
    "MissingSequenceNumber is never raised by the platform and goes away in tbot-console 2.0.0"
)
class MissingSequenceNumber(StreamingError):
    """Exception raised when sequence gap is detected in orderbook updates."""


@deprecated("SubscriptionError is never raised by the platform and goes away in tbot-console 2.0.0")
class SubscriptionError(StreamingError):
    """Exception raised when subscription to a channel fails."""


@deprecated(
    "AuthenticationError is never raised by the platform and goes away in tbot-console 2.0.0"
)
class AuthenticationError(StreamingError):
    """Exception raised when authentication fails."""


@deprecated("CallbackError is never raised by the platform and goes away in tbot-console 2.0.0")
class CallbackError(StreamingError):
    """Exception raised when a callback function encounters an error."""


@deprecated(
    "ConnectionTimeoutError is never raised by the platform and goes away in tbot-console 2.0.0"
)
class ConnectionTimeoutError(StreamingError):
    """Exception raised when connection timeout is detected."""


deprecated("StreamingError is never raised by the platform and goes away in tbot-console 2.0.0")(
    StreamingError
)
