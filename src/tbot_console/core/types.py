"""Universal types for the trading system.

This module defines core types used across all exchanges and components.
"""

from enum import Enum


class ConnectionType(str, Enum):
    """Types of connections in a pool.

    Universal across all exchanges. Each connection type serves a specific purpose:
    - MARKET_DATA: Public data streams (orderbook, trades, ticker)
    - TRADING: Order management operations (place, cancel, edit orders)
    - USER_DATA: Private user data streams (positions, balance, order fills)
    """

    MARKET_DATA = "market_data"
    TRADING = "trading"
    USER_DATA = "user_data"


class ServerType(str, Enum):
    """Server environment types.

    Used to distinguish between test/sandbox and production environments.
    """

    TEST = "test"
    PRODUCTION = "production"


class CallbackType(str, Enum):
    """Types of data callbacks.

    Defines the different types of data that can be received from exchanges
    and trigger registered callbacks.
    """

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
