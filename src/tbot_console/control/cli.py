from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from importlib import resources
from pathlib import Path

from tbot_console.control import ops, paths, procs, state
from tbot_console.control.manifest import UnitManifest
from tbot_console.control.registry import Registry, load_registry
from tbot_console.control.status import duration

KIND_ORDER = {"system": 0, "strategy": 1, "monitor": 2}
UI_LINKS = (
    "js/api.js",
    "js/dom.js",
    "js/util.js",
    "js/tooltip.js",
    "js/globals.d.ts",
    "js/units/contract.d.ts",
    "js/require-tests.mjs",
)


class CliError(Exception):
    pass


def _targets(registry: Registry, raw: str) -> list[tuple[UnitManifest, str]]:
    unit_id, _, proc = raw.partition(":")
    unit = registry.get(unit_id)
    if unit is None:
        known = ", ".join(u.id for u in registry.units) or "нет ни одного описания"
        raise CliError(f"не знаю «{unit_id}». Есть: {known}")
    if proc:
        if proc not in unit.processes:
            names = ", ".join(unit.processes)
            raise CliError(f"у «{unit.id}» нет процесса «{proc}». Есть: {names}")
        return [(unit, proc)]
    return [(unit, name) for name in unit.processes]


def cmd_ps(registry: Registry) -> int:
    rows = [("что", "процесс", "состояние", "pid", "работает", "замечание")]
    for unit in sorted(registry.units, key=lambda u: (KIND_ORDER[u.kind], u.order, u.id)):
        for proc in unit.processes:
            view = state.process_view(registry.base(unit), unit, proc)
            rows.append(
                (
                    unit.id,
                    proc,
                    view["label"],
                    str(view["pid"] or ""),
                    duration(time.time() - view["since"]) if view["since"] else "",
                    str(view["problem"] or ""),
                )
            )
    widths = [max(len(row[i]) for row in rows) for i in range(5)]
    for row in rows:
        cells = [row[i].ljust(widths[i]) for i in range(5)]
        print("  ".join([*cells, row[5]]).rstrip())
    for broken in registry.broken:
        print(f"ОШИБКА в {broken.file}: {broken.error}")
    return 0


def _run(
    base: Path,
    unit: UnitManifest,
    proc: str,
    action: str,
    op_id: str | None = None,
    pid: int | None = None,
) -> int:
    operation = ops.Operation(base, unit, proc, action, op_id or ops.new_op_id())
    return operation.execute(pid)


def cmd_action(registry: Registry, action: str, target: str) -> int:
    pairs = _targets(registry, target)
    if action == "stop":
        return max(
            (_run(registry.base(unit), unit, proc, action) for unit, proc in reversed(pairs)),
            default=ops.EXIT_OK,
        )
    for unit, proc in pairs:
        code = _run(registry.base(unit), unit, proc, action)
        if code != ops.EXIT_OK:
            return code
    return ops.EXIT_OK


def cmd_check(registry: Registry, target: str) -> int:
    worst = 0
    for unit, proc in _targets(registry, target):
        if not unit.processes[proc].check:
            print(f"{unit.id}:{proc}: проверка перед запуском не предусмотрена")
            continue
        result = ops.run_check(registry.base(unit), unit, proc)
        print(f"{unit.id}:{proc}: {'проверка пройдена' if result.ok else 'проверка НЕ пройдена'}")
        for line in result.describe():
            print(line)
        if not result.ok:
            print(f"  причина: {result.failures()}")
            worst = 1
    return worst


def _console_url(registry: Registry, codes: dict[tuple[str, str], int]) -> str | None:
    for unit in registry.units:
        if unit.kind != "system":
            continue
        for proc in unit.processes:
            if (port := unit.listen_port(proc)) is not None:
                return None if codes.get((unit.id, proc)) else f"http://127.0.0.1:{port}"
    return None


def cmd_up(home: Path, registry: Registry) -> int:
    if not paths.units_dir(home).is_dir():
        raise CliError(f"в {paths.units_dir(home)} нет описаний — запустите из папки проекта")
    codes: dict[tuple[str, str], int] = {}
    for unit in sorted(registry.units, key=lambda u: (KIND_ORDER[u.kind], u.order, u.id)):
        for proc, spec in unit.processes.items():
            if not spec.autostart:
                continue
            view = state.process_view(registry.base(unit), unit, proc)
            if "start" not in view["can"]:
                print(f"{unit.id}:{proc}: {view['label']}")
                continue
            codes[unit.id, proc] = _run(registry.base(unit), unit, proc, "start")
    if (url := _console_url(registry, codes)) is not None:
        print(f"консоль: {url}")
    return max(codes.values(), default=ops.EXIT_OK)


def cmd_output(registry: Registry, target: str, lines: int) -> int:
    for unit, proc in _targets(registry, target):
        base = registry.base(unit)
        output = state.output_file(base, unit, proc)
        print(f"== {unit.id}:{proc} — {paths.relative(base, output)}")
        for line in ops.tail_lines(output, lines):
            print(line)
    return 0


def _tracked(home: Path, names: tuple[str, ...]) -> list[str]:
    try:
        listed = subprocess.run(
            ["git", "ls-files", "--", *names],
            cwd=home,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    return sorted(listed.stdout.split()) if listed.returncode == 0 else []


def cmd_ui_links(home: Path) -> int:
    tracked = _tracked(home, UI_LINKS)
    if tracked:
        raise CliError(
            "эти файлы лежат в git этого репо, их не заменяю ссылками: " + ", ".join(tracked)
        )
    static = Path(str(resources.files("tbot_console.web") / "static"))
    for name in UI_LINKS:
        source, target = static / name, home / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)
        try:
            target.symlink_to(source)
            print(f"{name} → {source}")
        except OSError:
            shutil.copyfile(source, target)
            print(f"{name}: копия {source}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tbot", description="Управление стратегиями и мониторами tbot"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ps", help="состояние всех процессов")
    sub.add_parser("up", help="запустить всё, что помечено autostart (торговые боты — никогда)")
    for action in ("start", "stop", "restart"):
        cmd = sub.add_parser(action, help=f"{action} <стратегия>[:<процесс>]")
        cmd.add_argument("target")
    check = sub.add_parser("check", help="проверка конфига и правил безопасности без запуска")
    check.add_argument("target")
    adopt = sub.add_parser("adopt", help="взять под присмотр уже работающий процесс")
    adopt.add_argument("target")
    adopt.add_argument("--pid", type=int)
    output = sub.add_parser("output", help="последние строки вывода процесса")
    output.add_argument("target")
    output.add_argument("-n", "--lines", type=int, default=100)
    hidden = sub.add_parser("_op")
    hidden.add_argument("action", choices=ops.ACTIONS)
    hidden.add_argument("target")
    hidden.add_argument("--op-id", required=True)
    hidden.add_argument("--pid", type=int)
    sub.add_parser(
        "ui-links", help="ссылки на общие файлы консоли в js/ этого репо (для node --test и tsc)"
    )
    status = sub.add_parser("json", help="состояние в JSON")
    status.add_argument("target", nargs="?")
    return parser


def cmd_json(registry: Registry, target: str | None) -> int:
    pairs = (
        _targets(registry, target)
        if target
        else [(u, p) for u in registry.units for p in u.processes]
    )
    views = [
        {"unit": unit.id, **state.process_view(registry.base(unit), unit, proc)}
        for unit, proc in pairs
    ]
    print(json.dumps(views, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    home = paths.root()
    registry = load_registry(home)
    try:
        if args.command == "ps":
            return cmd_ps(registry)
        if args.command == "up":
            return cmd_up(home, registry)
        if args.command in ("start", "stop", "restart"):
            return cmd_action(registry, args.command, args.target)
        if args.command == "check":
            return cmd_check(registry, args.target)
        if args.command == "adopt":
            (unit, proc), *rest = _targets(registry, args.target)
            if rest:
                raise CliError("укажите процесс: tbot adopt <стратегия>:<процесс>")
            return _run(registry.base(unit), unit, proc, "adopt", pid=args.pid)
        if args.command == "output":
            return cmd_output(registry, args.target, args.lines)
        if args.command == "ui-links":
            return cmd_ui_links(home)
        if args.command == "json":
            return cmd_json(registry, args.target)
        if args.command == "_op":
            (unit, proc), *rest = _targets(registry, args.target)
            if rest:
                raise CliError("_op нужен конкретный процесс")
            return _run(
                registry.base(unit), unit, proc, args.action, op_id=args.op_id, pid=args.pid
            )
    except (CliError, procs.EnvPolicyError) as e:
        print(f"tbot: {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
