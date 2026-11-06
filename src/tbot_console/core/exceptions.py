from warnings import deprecated


class TradingSystemError(Exception):
    pass


class ExchangeError(TradingSystemError):
    pass


class ExchangeConnectionError(ExchangeError):
    pass


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
    pass


class ConfigurationError(TradingSystemError):
    pass


class StreamingError(TradingSystemError):
    pass


@deprecated(
    "MissingSequenceNumber is never raised by the platform and goes away in tbot-console 2.0.0"
)
class MissingSequenceNumber(StreamingError):
    """Exception raised when sequence gap is detected in orderbook updates."""


@deprecated("SubscriptionError is never raised by the platform and goes away in tbot-console 2.0.0")
class SubscriptionError(StreamingError):
    pass


@deprecated(
    "AuthenticationError is never raised by the platform and goes away in tbot-console 2.0.0"
)
class AuthenticationError(StreamingError):
    pass


@deprecated("CallbackError is never raised by the platform and goes away in tbot-console 2.0.0")
class CallbackError(StreamingError):
    pass


@deprecated(
    "ConnectionTimeoutError is never raised by the platform and goes away in tbot-console 2.0.0"
)
class ConnectionTimeoutError(StreamingError):
    pass


deprecated("StreamingError is never raised by the platform and goes away in tbot-console 2.0.0")(
    StreamingError
)
