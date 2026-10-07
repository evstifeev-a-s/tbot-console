from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ib_async import Contract
from ib_async.objects import CommissionReport, Execution, Fill
from ib_async.wrapper import RequestError

from tbot_console.connectors.ibkr import IBKRClient, IBKRConfig, IBKRExecution, SecType
from tbot_console.core.exceptions import ExchangeRequestError

from .conftest import FakeIB


def fill(exec_id: str, side: str, hour: int, symbol: str = "DEMO", shares: float = 100.0) -> Fill:
    moment = datetime(2026, 10, 7, hour, 0, tzinfo=UTC)
    contract = Contract(secType="STK", symbol=symbol, currency="USD", exchange="SMART")
    execution = Execution(
        execId=exec_id,
        time=moment,
        acctNumber="DU0000001",
        side=side,
        shares=shares,
        price=2.5,
        orderId=7,
        permId=11,
    )
    return Fill(contract, execution, CommissionReport(), moment)


@pytest.fixture
def ib() -> FakeIB:
    fake = FakeIB()
    fake.connected = True
    return fake


async def test_executions_are_read_as_buys_and_sells_in_time_order(ib: FakeIB):
    ib.fills = [fill("2", "SLD", 15), fill("1", "BOT", 14), fill("3", "XXX", 16)]
    client = IBKRClient(IBKRConfig(account="DU0000001"), ib=ib)
    found = await client.fetch_executions("demo")
    assert [(e.exec_id, e.side) for e in found] == [("1", "buy"), ("2", "sell")]
    first = found[0]
    assert isinstance(first, IBKRExecution)
    assert (first.symbol, first.shares, first.price, first.account) == (
        "DEMO",
        100.0,
        2.5,
        "DU0000001",
    )
    assert first.instrument is not None and first.instrument.sec_type is SecType.STK
    assert first.timestamp == datetime(2026, 10, 7, 14, 0, tzinfo=UTC).timestamp()
    sent = ib.execution_calls[-1]
    assert (sent.acctCode, sent.symbol) == ("DU0000001", "DEMO")


async def test_since_keeps_only_executions_from_that_moment(ib: FakeIB):
    ib.fills = [fill("1", "BOT", 13), fill("2", "BOT", 15)]
    client = IBKRClient(IBKRConfig(), ib=ib)
    found = await client.fetch_executions(since=datetime(2026, 10, 7, 14, 0, tzinfo=UTC))
    assert [e.exec_id for e in found] == ["2"]


async def test_a_refused_executions_request_is_an_exchange_error(ib: FakeIB):
    ib.raise_on["executions"] = RequestError(-1, 321, "Error validating request")
    with pytest.raises(ExchangeRequestError) as caught:
        await IBKRClient(IBKRConfig(), ib=ib).fetch_executions()
    assert caught.value.code == 321
