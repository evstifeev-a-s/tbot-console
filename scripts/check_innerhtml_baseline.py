#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

BASELINE = 1

ROOT = Path(__file__).resolve().parent.parent
JS_DIR = ROOT / "src" / "tbot_console" / "web" / "static" / "js"
PATTERN = re.compile(r"\.innerHTML\s*=")


def main() -> int:
    sites: list[str] = []
    for path in sorted(JS_DIR.rglob("*.js")):
        if path.name.endswith(".test.js"):
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if PATTERN.search(line):
                sites.append(f"{path.relative_to(ROOT)}:{lineno}")

    count = len(sites)
    if count > BASELINE:
        print(f"innerHTML-стоков: {count}, baseline: {BASELINE}. Появились новые:")
        for site in sites:
            print(f"  {site}")
        print(
            "\nКаждая интерполяция в innerHTML/el(`…`) должна идти через esc(). "
            "Проверьте новый сток и, если он безопасен, поднимите BASELINE "
            f"в {Path(__file__).relative_to(ROOT)}."
        )
        return 1
    if count < BASELINE:
        print(
            f"innerHTML-стоков стало меньше ({count} < baseline {BASELINE}) — "
            f"снизьте BASELINE в {Path(__file__).relative_to(ROOT)}, чтобы tripwire "
            "оставался тугим."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
