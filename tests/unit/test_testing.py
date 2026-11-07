from __future__ import annotations

from pathlib import Path

from tbot_console.testing import startup_imports


def test_a_process_without_a_main_file_imports_only_its_module(tmp_path: Path):
    (tmp_path / "alpha" / "bot").mkdir(parents=True)
    assert startup_imports("alpha.bot", tmp_path) == "import alpha.bot"


def test_the_main_file_imports_are_spelled_from_the_package(tmp_path: Path):
    package = tmp_path / "alpha" / "bot"
    package.mkdir(parents=True)
    (package / "__main__.py").write_text(
        "import json, os.path as p\n"
        "from . import engine\n"
        "from .engine import run as go\n"
        "from .. import shared\n"
        "from alpha.shared import config\n"
        "\n"
        "def main():\n"
        "    from .late import thing\n"
    )
    assert startup_imports("alpha.bot", tmp_path).splitlines() == [
        "import alpha.bot",
        "import json, os.path as p",
        "from alpha.bot import engine",
        "from alpha.bot.engine import run as go",
        "from alpha import shared",
        "from alpha.shared import config",
        "from alpha.bot.late import thing",
    ]
