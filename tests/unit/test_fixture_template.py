import tomllib
from importlib.metadata import version
from pathlib import Path

from tbot_console.control.cli import UI_LINKS
from tbot_console.testing import (
    imported_modules,
    manifest_modules,
    manifest_problems,
    startup_imports,
)

TEMPLATE = Path(__file__).resolve().parents[1] / "fixtures" / "unit_repo"


def test_the_template_pins_this_platform_release() -> None:
    project = tomllib.loads((TEMPLATE / "pyproject.toml").read_text(encoding="utf-8"))
    source = project["tool"]["uv"]["sources"]["tbot-console"]
    assert source["tag"] == f"v{version('tbot-console')}"
    assert source["git"] == "https://github.com/evstifeev-a-s/tbot-console.git"


def test_the_template_manifests_are_valid() -> None:
    assert manifest_problems(TEMPLATE) == []
    assert manifest_modules(TEMPLATE) == ["demo_unit.webapp"]


def test_the_template_modules_import_only_what_the_platform_ships() -> None:
    for module in manifest_modules(TEMPLATE):
        code = "import sys\nsys.path.insert(0, 'src')\n" + startup_imports(module, TEMPLATE / "src")
        assert module in imported_modules(code, TEMPLATE)


def test_the_template_keeps_local_files_out_of_git() -> None:
    ignored = set((TEMPLATE / ".gitignore").read_text(encoding="utf-8").split())
    assert {".env", ".env.*", "!.env.example", "/config/workspace.json"} <= ignored
    assert {f"/{name}" for name in UI_LINKS} <= ignored
