from pathlib import Path

import pytest

from tbot_console.testing import imported_modules

REPO_ROOT = Path(__file__).resolve().parents[2]
CONSOLE_ENTRIES = ("tbot_console.control.cli", "tbot_console.web.app")
HEAVY = ("ib_async", "numpy")


@pytest.mark.parametrize("entry", CONSOLE_ENTRIES)
def test_a_console_entry_loads_no_market_libraries(entry: str) -> None:
    loaded = imported_modules(f"import {entry}", REPO_ROOT)
    assert entry in loaded
    assert sorted(name for name in loaded if name.split(".")[0] in HEAVY) == []
