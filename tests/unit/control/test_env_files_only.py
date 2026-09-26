from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from tbot_console.control.manifest import EnvFilesOnly, UnitManifest


def manifest(**top: Any) -> dict[str, Any]:
    return {
        "id": "demo",
        "kind": "monitor",
        "title": "Demo",
        "processes": {"web": {"title": "Web", "module": "demo.web"}},
        **top,
    }


class TestEnvFilesOnly:
    def test_an_empty_filter_is_refused(self):
        with pytest.raises(ValidationError, match="ни одной переменной"):
            EnvFilesOnly()

    def test_it_admits_by_prefix_and_by_key(self):
        only = EnvFilesOnly(prefixes=("DEMO_",), keys=("HOST_PORT",))
        assert only.admits("DEMO_TOKEN")
        assert only.admits("HOST_PORT")
        assert not only.admits("HOST_PORT_X")
        assert not only.admits("DEMO")
        assert not only.admits("OTHER_SECRET")

    def test_keys_alone_are_enough(self):
        only = EnvFilesOnly(keys=("TBOT_WEB_TOKEN",))
        assert only.admits("TBOT_WEB_TOKEN")
        assert not only.admits("TBOT_WEB_HOST")

    def test_a_unit_carries_the_filter(self):
        unit = UnitManifest.model_validate(
            manifest(env_files_only={"prefixes": ["DEMO_"], "keys": ["HOST_PORT"]})
        )
        assert unit.env_files_only == EnvFilesOnly(prefixes=("DEMO_",), keys=("HOST_PORT",))

    def test_a_unit_without_the_filter_gets_everything_as_before(self):
        assert UnitManifest.model_validate(manifest()).env_files_only is None

    @pytest.mark.parametrize(
        "only",
        [
            {},
            {"prefixes": ["DEMO"]},
            {"prefixes": ["demo_"]},
            {"prefixes": ["DE-MO_"]},
            {"keys": ["lower"]},
            {"prefix": ["DEMO_"]},
        ],
    )
    def test_a_bad_filter_is_refused(self, only):
        with pytest.raises(ValidationError):
            UnitManifest.model_validate(manifest(env_files_only=only))
