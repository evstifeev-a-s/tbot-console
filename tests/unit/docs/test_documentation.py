"""Documentation contract gate (AGENTS.md "Documentation").

Runs the structural checks from scripts/check_docs.py inside the unit suite so
the Definition of done ("unit tests green") also guarantees docs stay present
and internally consistent, whichever agent or human made the change.
"""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_docs.py"
_spec = importlib.util.spec_from_file_location("check_docs", _SCRIPT)
assert _spec is not None and _spec.loader is not None
check_docs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_docs)


def test_every_module_readme_is_reachable_from_the_agents_map():
    assert check_docs.readme_violations() == []


def test_relative_markdown_links_resolve():
    assert check_docs.broken_links() == []


def test_vendor_adapter_files_point_at_agents_md():
    assert check_docs.adapter_violations() == []
