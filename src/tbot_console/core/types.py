from enum import StrEnum
from warnings import deprecated


@deprecated("ConnectionType is not used by the platform and goes away in tbot-console 2.0.0")
class ConnectionType(StrEnum):
    """Types of connections in a pool.

    Universal across all exchanges. Each connection type serves a specific purpose:
    - MARKET_DATA: Public data streams (orderbook, trades, ticker)
    - TRADING: Order management operations (place, cancel, edit orders)
    - USER_DATA: Private user data streams (positions, balance, order fills)
    """

    MARKET_DATA = "market_data"
    TRADING = "trading"
    USER_DATA = "user_data"


class ServerType(StrEnum):
    TEST = "test"
    PRODUCTION = "production"


@deprecated("CallbackType is not used by the platform and goes away in tbot-console 2.0.0")
class CallbackType(StrEnum):
    TRADE = "trade"
    ORDERBOOK = "orderbook"
    TICKER = "ticker"
    TICKER_RAW = "ticker_raw"
    USER_ORDER = "user_order"
    USER_ORDER_RAW = "user_order_raw"
    USER_TRADE = "user_trade"
    USER_TRADE_RAW = "user_trade_raw"
    USER_BALANCE = "user_balance"
    USER_POSITION = "user_position"
