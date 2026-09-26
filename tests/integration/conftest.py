from __future__ import annotations

import pytest

from tbot_console.core.exceptions import ConnectionTimeoutError, ExchangeConnectionError

_TRANSIENT_EXCEPTIONS = (ExchangeConnectionError, ConnectionTimeoutError)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when in ("setup", "call") and report.failed and call.excinfo is not None:
        exc = call.excinfo.value
        if isinstance(exc, _TRANSIENT_EXCEPTIONS):
            report.outcome = "skipped"
            reason = f"Skipped: gateway unreachable ({type(exc).__name__}: {exc})"
            report.longrepr = (str(item.fspath), item.location[1] or 0, reason)
