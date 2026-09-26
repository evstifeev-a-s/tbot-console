from __future__ import annotations

import re
from pathlib import PureWindowsPath
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

UNIT_ID_PATTERN = r"^[a-z][a-z0-9-]{1,30}$"
PROCESS_NAME_PATTERN = r"^[a-z][a-z0-9-]{0,20}$"
MODULE_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$"

RESERVED_IDS = frozenset(
    {
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
    }
)

PLATFORM_ENV_PREFIX = "TBOT_"
WIRING_KEY_RE = re.compile(
    r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_(?:HOST|ALLOWED_HOSTS|CONFIG_PATH|CONFIG_DIR|DATA_DIR)$"
)
SECRET_KEY_RE = re.compile(r"(?:TOKEN|SECRET|PASSWORD|PASSWD|KEY)$")

RESERVED_PORTS = frozenset({5432, 5433})

ProcessName = Annotated[str, StringConstraints(pattern=PROCESS_NAME_PATTERN)]
PageName = Annotated[str, StringConstraints(pattern=UNIT_ID_PATTERN)]
EnvPrefix = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9]*_$")]
EnvKey = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]*$")]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ReadySpec(_Frozen):
    kind: Literal["http", "descriptor", "alive"] = "alive"
    timeout_s: int = Field(default=60, ge=1, le=900)
    port: int | None = Field(default=None, ge=1024, le=65535)


class ProcessSpec(_Frozen):
    title: str = Field(min_length=1, max_length=60)
    module: str = Field(pattern=MODULE_PATTERN)
    args: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)
    env_files: tuple[str, ...] = ()
    keep_awake: Literal["idle", "system"] | None = None
    autostart: bool = False
    stop_timeout_s: int = Field(default=30, ge=1, le=900)
    check: bool = False
    confirm: str | None = Field(default=None, min_length=1, max_length=400)
    ready: ReadySpec = ReadySpec()

    @field_validator("env")
    @classmethod
    def _env_allowlisted(cls, value: dict[str, str]) -> dict[str, str]:
        refused = sorted(
            key
            for key in value
            if SECRET_KEY_RE.search(key)
            or not (key.startswith(PLATFORM_ENV_PREFIX) or WIRING_KEY_RE.match(key))
        )
        if refused:
            raise ValueError(
                "в описании можно задавать только адреса и пути к файлам; "
                f"ключи {', '.join(refused)} сюда нельзя: секреты живут в env-файлах, "
                "торговые параметры — в конфиге стратегии"
            )
        return value

    @field_validator("env_files")
    @classmethod
    def _env_files_inside_the_repo(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        outside = [
            raw
            for raw in value
            if PureWindowsPath(raw).anchor or ".." in PureWindowsPath(raw).parts
        ]
        if outside:
            raise ValueError(
                "env-файлы читаются только из папки репо, без абсолютных путей и «..»: "
                + ", ".join(outside)
            )
        return value


class ApiSpec(_Frozen):
    process: str = Field(pattern=PROCESS_NAME_PATTERN)
    port: int = Field(ge=1024, le=65535)

    @field_validator("port")
    @classmethod
    def _port_free_of_services(cls, value: int) -> int:
        if value in RESERVED_PORTS:
            raise ValueError(f"порт {value} занят базой данных")
        return value


class EnvFilesIgnore(_Frozen):
    prefixes: tuple[EnvPrefix, ...] = ()
    keys: tuple[EnvKey, ...] = ()

    @model_validator(mode="after")
    def _platform_env_passes(self) -> Self:
        claimed = sorted(
            name for name in (*self.prefixes, *self.keys) if name.startswith(PLATFORM_ENV_PREFIX)
        )
        if claimed:
            raise ValueError(
                f"{', '.join(claimed)}: переменные {PLATFORM_ENV_PREFIX}* задаёт консоль, "
                "отбрасывать их нельзя"
            )
        return self


class EnvFilesOnly(_Frozen):
    prefixes: tuple[EnvPrefix, ...] = ()
    keys: tuple[EnvKey, ...] = ()

    @model_validator(mode="after")
    def _names_something(self) -> Self:
        if not (self.prefixes or self.keys):
            raise ValueError(
                "env_files_only без prefixes и keys не пропустил бы из env-файлов ни одной "
                "переменной: перечислите нужные или уберите поле"
            )
        return self

    def admits(self, key: str) -> bool:
        return key.startswith(self.prefixes) or key in self.keys


class UnitManifest(_Frozen):
    id: str = Field(pattern=UNIT_ID_PATTERN)
    kind: Literal["strategy", "monitor", "system"]
    title: str = Field(min_length=1, max_length=80)
    glyph: str = Field(default="", max_length=4)
    sub: str = Field(default="", max_length=120)
    order: int = 100
    ui: str | None = Field(default=None, pattern=UNIT_ID_PATTERN)
    legacy_pages: tuple[PageName, ...] = ()
    places_orders: bool = False
    api: ApiSpec | None = None
    env_files_ignore: EnvFilesIgnore = EnvFilesIgnore()
    env_files_only: EnvFilesOnly | None = None
    processes: dict[ProcessName, ProcessSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if self.id in RESERVED_IDS:
            raise ValueError(f"имя «{self.id}» занято консолью")
        if self.api is not None and self.api.process not in self.processes:
            raise ValueError(f"api.process «{self.api.process}» не описан в processes")
        if self.api is not None and self.processes[self.api.process].ready.port not in (
            None,
            self.api.port,
        ):
            raise ValueError("у службы порт задаётся в api.port, ready.port должен совпадать с ним")
        if self.id in self.legacy_pages:
            raise ValueError(f"legacy_pages не может содержать собственное имя «{self.id}»")
        if self.places_orders:
            eager = sorted(n for n, p in self.processes.items() if p.autostart and self.trades(n))
            if eager:
                raise ValueError(
                    "торговый процесс не запускается сам: autostart должен быть false для "
                    + ", ".join(eager)
                )
            unchecked = sorted(
                n
                for n, p in self.processes.items()
                if not p.check and (self.api is None or n != self.api.process)
            )
            if unchecked:
                raise ValueError(
                    "у торговой стратегии каждый процесс, кроме службы, проходит проверку: "
                    "check=true для " + ", ".join(unchecked)
                )
            for key in sorted({name for spec in self.processes.values() for name in spec.env}):
                prefix = key.split("_", 1)[0] + "_"
                if prefix != PLATFORM_ENV_PREFIX and prefix not in self.env_files_ignore.prefixes:
                    raise ValueError(
                        f"торговая стратегия задаёт {key}, но не отбрасывает {prefix} "
                        "из env-файлов: .env мог бы подменить её настройки"
                    )
        return self

    def trades(self, proc: str) -> bool:
        return self.places_orders and self.processes[proc].check

    def api_url(self) -> str | None:
        return None if self.api is None else f"http://127.0.0.1:{self.api.port}"

    def output_path(self, proc: str) -> str:
        return f"logs/units/{self.id}.{proc}.out"

    def listen_port(self, proc: str) -> int | None:
        ready = self.processes[proc].ready
        if ready.kind == "http" and ready.port:
            return ready.port
        return self.api.port if self.api is not None and self.api.process == proc else None

    def ready_endpoint(self, proc: str) -> tuple[int, str] | None:
        port = self.listen_port(proc)
        if self.processes[proc].ready.kind != "http" or port is None:
            return None
        is_api = self.api is not None and self.api.process == proc
        return port, f"/api/{self.id}/health" if is_api else "/"
