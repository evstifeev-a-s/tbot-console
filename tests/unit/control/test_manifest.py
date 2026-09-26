from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from tbot_console.control import registry
from tbot_console.control.manifest import RESERVED_IDS, UnitManifest
from tbot_console.control.registry import load_registry
from tests.unit.control.helpers import REPO_ROOT, console_data, fixture_manifest, write_manifest


def parse(data: dict) -> UnitManifest:
    return UnitManifest.model_validate(data)


def ids(root) -> list[str]:
    return [unit.id for unit in load_registry(root).units]


class TestManifest:
    def test_a_trading_unit_parses(self):
        unit = parse(fixture_manifest("alpha"))
        assert list(unit.processes) == ["bot", "api"]
        assert unit.api_url() == "http://127.0.0.1:18601"
        assert unit.ready_endpoint("api") == (18601, "/api/alpha/health")
        assert unit.ready_endpoint("bot") is None
        assert unit.listen_port("api") == 18601
        assert unit.listen_port("bot") is None
        assert unit.output_path("bot") == "logs/units/alpha.bot.out"

    def test_only_the_consoles_own_names_are_reserved(self):
        assert {
            "control",
            "platform",
            "units",
            "static",
            "api",
            "js",
            "css",
            "vendor",
            "home",
            "processes",
            "docs",
        } == RESERVED_IDS

    @pytest.mark.parametrize("unit_id", ["processes", "control", "api", "units", "js"])
    def test_a_name_the_console_uses_is_refused(self, unit_id):
        with pytest.raises(ValidationError, match="занято консолью"):
            parse(fixture_manifest("alpha", id=unit_id))

    @pytest.mark.parametrize(
        "key", ["ALPHA_DRY_RUN", "SERVICE_CLIENT_SECRET", "IBKR_ALLOW_LIVE_TRADING"]
    )
    def test_settings_and_secrets_never_live_in_a_manifest(self, key):
        data = fixture_manifest("alpha")
        data["processes"]["bot"]["env"][key] = "x"
        with pytest.raises(ValidationError, match=key):
            parse(data)

    @pytest.mark.parametrize(
        "key",
        ["TBOT_ANYTHING", "BETA_WEB_HOST", "NEW_UNIT_CONFIG_DIR", "XY_DATA_DIR", "XY_CONFIG_PATH"],
    )
    def test_wiring_keys_are_allowed(self, key):
        data = console_data()
        data["processes"]["web"]["env"] = {key: "1"}
        assert parse(data).processes["web"].env == {key: "1"}

    @pytest.mark.parametrize("key", ["BETA_WATCHLIST", "HOST", "beta_web_host", "XY_CONFIG_PATH_2"])
    def test_anything_but_wiring_is_refused(self, key):
        data = console_data()
        data["processes"]["web"]["env"] = {key: "1"}
        with pytest.raises(ValidationError, match="только адреса и пути"):
            parse(data)

    @pytest.mark.parametrize(
        "key", ["TBOT_WEB_TOKEN", "TBOT_SECRET", "TBOT_API_KEY", "TBOT_PASSWORD", "TBOT_PASSWD"]
    )
    def test_a_secret_is_refused_even_under_the_platform_prefix(self, key):
        data = console_data()
        data["processes"]["web"]["env"] = {key: "x"}
        with pytest.raises(ValidationError, match=key):
            parse(data)

    def test_the_console_address_is_set_in_its_manifest(self):
        data = console_data()
        wanted = {"TBOT_WEB_HOST": "0.0.0.0", "TBOT_WEB_ALLOWED_HOSTS": "mac.local"}
        data["processes"]["web"]["env"] = wanted
        assert parse(data).processes["web"].env == wanted

    def test_env_files_ignore_takes_prefixes_and_keys(self):
        data = fixture_manifest(
            "beta", env_files_ignore={"prefixes": ["BETA_", "X1_"], "keys": ["IBKR_X"]}
        )
        ignore = parse(data).env_files_ignore
        assert ignore.prefixes == ("BETA_", "X1_")
        assert ignore.keys == ("IBKR_X",)
        assert parse(fixture_manifest("beta")).env_files_ignore.prefixes == ()

    @pytest.mark.parametrize(
        "ignore",
        [
            {"prefixes": ["AL"]},
            {"prefixes": ["al_"]},
            {"prefixes": ["A-L_"]},
            {"prefixes": ["TBOT_"]},
            {"keys": ["TBOT_WEB_TOKEN"]},
            {"keys": ["lower"]},
            {"prefix": ["ALPHA_"]},
        ],
    )
    def test_a_bad_env_files_ignore_is_refused(self, ignore):
        with pytest.raises(ValidationError):
            parse(fixture_manifest("beta", env_files_ignore=ignore))

    @pytest.mark.parametrize(
        "env_file", ["../x/.env", "a/../../.env", "/etc/.env", "C:\\x\\.env", "..\\x\\.env"]
    )
    def test_env_files_stay_inside_the_repo(self, env_file):
        data = fixture_manifest("beta")
        data["processes"]["api"]["env_files"] = [".env", env_file]
        with pytest.raises(ValidationError, match="только из папки репо"):
            parse(data)

    def test_env_files_in_subfolders_of_the_repo_are_fine(self):
        data = fixture_manifest("beta")
        data["processes"]["api"]["env_files"] = [".env", "config/.env.local", "a..b/.env"]
        assert parse(data).processes["api"].env_files[1] == "config/.env.local"

    def test_a_trading_unit_must_drop_the_prefix_of_its_own_settings(self):
        data = fixture_manifest("alpha")
        del data["env_files_ignore"]
        with pytest.raises(ValidationError, match="не отбрасывает ALPHA_ из env-файлов"):
            parse(data)

    def test_platform_keys_need_no_drop_rule(self):
        data = fixture_manifest("alpha", env_files_ignore={"prefixes": ["ALPHA_"]})
        data["processes"]["api"]["env"]["TBOT_ANYTHING"] = "1"
        assert parse(data).processes["api"].env["TBOT_ANYTHING"] == "1"

    def test_legacy_pages_are_page_names_other_than_the_own_id(self):
        assert parse(fixture_manifest("alpha")).legacy_pages == ("old-page",)
        with pytest.raises(ValidationError, match="собственное имя"):
            parse(fixture_manifest("alpha", legacy_pages=["old-page", "alpha"]))
        with pytest.raises(ValidationError):
            parse(fixture_manifest("alpha", legacy_pages=["Old"]))

    def test_a_process_can_carry_its_own_confirmation(self):
        assert "без присмотра" in (parse(fixture_manifest("alpha")).processes["bot"].confirm or "")
        assert parse(fixture_manifest("alpha")).processes["api"].confirm is None
        for bad in ["", "x" * 401]:
            data = fixture_manifest("alpha")
            data["processes"]["bot"]["confirm"] = bad
            with pytest.raises(ValidationError):
                parse(data)

    def test_a_trading_bot_never_starts_on_its_own(self):
        data = fixture_manifest("alpha")
        data["processes"]["bot"]["autostart"] = True
        with pytest.raises(ValidationError, match="autostart"):
            parse(data)

    def test_every_trading_process_but_the_service_is_checked(self):
        data = fixture_manifest("alpha")
        data["processes"]["bot"]["check"] = False
        with pytest.raises(ValidationError, match="check=true для bot"):
            parse(data)
        unit = parse(fixture_manifest("alpha"))
        assert unit.trades("bot") and not unit.trades("api")

    def test_the_api_process_must_exist(self):
        data = fixture_manifest("alpha")
        data["api"]["process"] = "web"
        with pytest.raises(ValidationError, match="api.process"):
            parse(data)

    def test_the_service_listens_on_api_port(self):
        data = fixture_manifest("alpha")
        data["processes"]["api"]["ready"]["port"] = 18601
        assert parse(data).listen_port("api") == 18601
        data["processes"]["api"]["ready"]["port"] = 18602
        with pytest.raises(ValidationError, match="api.port"):
            parse(data)

    def test_the_database_port_is_not_a_service_port(self):
        data = fixture_manifest("alpha")
        data["api"]["port"] = 5433
        with pytest.raises(ValidationError, match="базой данных"):
            parse(data)

    def test_unknown_fields_are_refused(self):
        data = fixture_manifest("alpha")
        data["processes"]["bot"]["restart"] = "always"
        with pytest.raises(ValidationError):
            parse(data)

    def test_process_names_are_short_lowercase_words(self):
        data = fixture_manifest("alpha")
        data["processes"]["Bot"] = data["processes"].pop("bot")
        with pytest.raises(ValidationError):
            parse(data)


class TestRegistry:
    def test_the_fixture_units_are_valid(self, root):
        write_manifest(root, fixture_manifest("alpha"))
        write_manifest(root, fixture_manifest("beta"))
        assert load_registry(root).broken == ()
        assert ids(root) == ["console", "alpha", "beta"]

    def test_the_console_is_built_in(self, root):
        console = load_registry(root).get("console")
        assert console is not None
        assert console.processes["web"].module == "tbot_console.web.app"
        assert console.listen_port("web") == 8420

    def test_units_are_ordered_and_findable(self, root):
        write_manifest(root, fixture_manifest("beta"))
        write_manifest(root, fixture_manifest("alpha"))
        found = load_registry(root)
        assert [unit.id for unit in found.units] == ["console", "alpha", "beta"]
        assert found.get("beta") is not None
        assert found.get("nope") is None

    def test_a_broken_file_does_not_hide_the_others(self, root):
        write_manifest(root, fixture_manifest("alpha"))
        (root / "config/units/bad.json").write_text("{not json", encoding="utf-8")
        found = load_registry(root)
        assert [unit.id for unit in found.units] == ["console", "alpha"]
        assert found.broken[0].file == "config/units/bad.json"
        assert "не JSON" in found.broken[0].error

    def test_the_file_name_must_match_the_id(self, root):
        path = write_manifest(root, fixture_manifest("alpha"))
        path.rename(path.with_name("other.json"))
        found = load_registry(root)
        assert [unit.id for unit in found.units] == ["console"]
        assert "alpha.json" in found.broken[0].error

    def test_two_units_on_one_port_are_both_refused(self, root):
        clash = fixture_manifest("beta")
        clash["api"]["port"] = 18601
        write_manifest(root, fixture_manifest("alpha"))
        write_manifest(root, clash)
        found = load_registry(root)
        assert [unit.id for unit in found.units] == ["console"]
        assert {b.file for b in found.broken} == {
            "config/units/alpha.json",
            "config/units/beta.json",
        }

    def test_a_unit_on_the_console_port_is_refused(self, root):
        clash = fixture_manifest("beta")
        clash["api"]["port"] = 8420
        write_manifest(root, clash)
        found = load_registry(root)
        assert [unit.id for unit in found.units] == ["console"]
        assert found.broken[0].error == "порт 8420 уже занят"

    def test_a_repos_own_console_is_refused(self, root):
        write_manifest(root, console_data())
        found = load_registry(root)
        assert [unit.id for unit in found.units] == ["console"]
        assert found.broken[0].file == "config/units/console.json"
        assert "имя «console» уже занято: консоль" in found.broken[0].error

    def test_a_new_file_is_seen_once_the_memo_expires(self, root, monkeypatch):
        write_manifest(root, fixture_manifest("alpha"))
        assert ids(root) == ["console", "alpha"]
        write_manifest(root, fixture_manifest("beta"))
        assert ids(root) == ["console", "alpha"]
        monkeypatch.setattr(registry, "MEMO_TTL_S", 0.0)
        assert ids(root) == ["console", "alpha", "beta"]

    def test_forget_drops_the_memo(self, root):
        write_manifest(root, fixture_manifest("alpha"))
        assert ids(root) == ["console", "alpha"]
        write_manifest(root, fixture_manifest("beta"))
        registry.forget()
        assert ids(root) == ["console", "alpha", "beta"]

    def test_a_missing_directory_is_only_the_console(self, root):
        found = load_registry(root)
        assert [unit.id for unit in found.units] == ["console"]
        assert found.broken == ()

    def test_a_legacy_page_and_an_id_share_one_namespace(self, root):
        write_manifest(root, fixture_manifest("alpha"))
        write_manifest(root, fixture_manifest("beta", legacy_pages=["old-page"]))
        write_manifest(root, {**fixture_manifest("beta"), "id": "old-page", "api": None})
        found = load_registry(root)
        assert [unit.id for unit in found.units] == ["console", "alpha"]
        assert [b.file for b in found.broken] == [
            "config/units/beta.json",
            "config/units/old-page.json",
        ]
        assert "«old-page» уже занято: config/units/alpha.json" in found.broken[0].error


def test_the_processes_page_stays_reserved():
    route = (REPO_ROOT / "src/tbot_console/web/static/js/shell/route.js").read_text()
    processes = re.search(r'PROCESSES_PAGE = "([a-z]+)"', route)
    assert processes is not None and processes.group(1) in RESERVED_IDS
