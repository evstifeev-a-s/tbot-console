#!/usr/bin/env python3
"""Documentation gate for the AI-first docs contract (AGENTS.md "Documentation").

Structural checks (fail CI / unit suite) — deterministic properties only:
  * every module README.md under src/ is reachable from the AGENTS.md repository
    map (agents find nested docs unreliably, so an unlisted README is invisible);
  * every relative link in tracked *.md files resolves to an existing file;
  * vendor adapter files stay thin pointers to AGENTS.md (never forked copies).

Deliberately NOT checked: whether a package "should" have a README. That is a
judgement call (independent contract, own lifecycle, non-obvious boundaries,
critical invariants, an owner) — a file-count threshold is a proxy that forces
template documentation, so it is not enforced mechanically. Nor is semantic
freshness: a structural gate cannot prove a document still describes reality.

Drift check (advisory, --drift / --hook): uncommitted *.py changes inside a
documented package while its README.md is untouched. --hook is the Claude Code
Stop-hook entry: exits 2 with the reminder on first stop, stays silent when
stop_hook_active is set, so it nags at most once per stop cycle. Other agent
CLIs get the same behavior by running `--drift` before finishing (AGENTS.md
Definition of done).

--report: git-history staleness per documented package (code commits since the
last README commit). Advisory only, never fails.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "tbot_console"
ADAPTER_FILES = {
    "CLAUDE.md": "@AGENTS.md",
}

MD_EXCLUDE_PARTS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "htmlcov",
    ".claude",
    "data",
    "logs",
    "run",
    "test-results",
    "playwright-report",
}

_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def readme_violations() -> list[str]:
    agents_md = ROOT / "AGENTS.md"
    if not agents_md.exists():
        return []
    mapped = {
        (agents_md.parent / m.group(1).split("#")[0]).resolve()
        for m in _LINK_RE.finditer(agents_md.read_text(encoding="utf-8"))
        if "://" not in m.group(1) and not m.group(1).startswith("#")
    }
    return [
        f"{readme.relative_to(ROOT)}: not linked from the AGENTS.md repository map — "
        f"agents do not reliably discover nested docs, so either link it or remove it"
        for readme in sorted(SRC.rglob("README.md"))
        if readme.resolve() not in mapped
    ]


def _tracked_md_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.md"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        paths = [ROOT / line for line in out.splitlines() if line.strip()]
    except (subprocess.CalledProcessError, FileNotFoundError):
        paths = list(ROOT.rglob("*.md"))
    return [
        p
        for p in paths
        if p.exists() and not MD_EXCLUDE_PARTS.intersection(p.relative_to(ROOT).parts)
    ]


def broken_links() -> list[str]:
    problems = []
    for md in _tracked_md_files():
        text = _INLINE_CODE_RE.sub("", _FENCE_RE.sub("", md.read_text(encoding="utf-8")))
        for match in _LINK_RE.finditer(text):
            target = match.group(1)
            if "://" in target or target.startswith(("#", "mailto:")):
                continue
            rel = md.relative_to(ROOT)
            if target.startswith("/"):
                problems.append(f"{rel}: absolute link {target} — use a relative path")
                continue
            resolved = (md.parent / target.split("#")[0]).resolve()
            if not resolved.exists():
                problems.append(f"{rel}: broken relative link {target}")
    return problems


def adapter_violations() -> list[str]:
    problems = []
    if not (ROOT / "AGENTS.md").exists():
        problems.append("AGENTS.md missing — it is the canonical agent entry point")
    for rel, marker in ADAPTER_FILES.items():
        path = ROOT / rel
        if not path.exists():
            problems.append(f"{rel} missing — thin adapter pointing at AGENTS.md")
        elif marker not in path.read_text(encoding="utf-8"):
            problems.append(
                f"{rel}: must reference {marker} (thin adapter, not a fork of AGENTS.md)"
            )
    return problems


def _uncommitted_changes() -> list[str]:
    out = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    changed = []
    for line in out.splitlines():
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ")[1]
        changed.append(path)
    return changed


def _documenting_readme(py_rel: str) -> Path | None:
    path = ROOT / py_rel
    for parent in path.parents:
        if parent == SRC or SRC not in parent.parents:
            return None
        readme = parent / "README.md"
        if readme.exists():
            return readme
    return None


def drift_reminders() -> list[str]:
    try:
        changed = _uncommitted_changes()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    changed_set = set(changed)
    stale: dict[str, list[str]] = {}
    for path in changed:
        if not path.endswith(".py") or not path.startswith("src/tbot_console/"):
            continue
        readme = _documenting_readme(path)
        if readme is None:
            continue
        readme_rel = str(readme.relative_to(ROOT)).replace("\\", "/")
        if readme_rel not in changed_set:
            stale.setdefault(readme_rel, []).append(path)
    return [
        f"{readme} — code changed ({', '.join(sorted(files)[:5])}"
        f"{', …' if len(files) > 5 else ''}) but the README was not touched"
        for readme, files in sorted(stale.items())
    ]


def structural() -> int:
    problems = readme_violations() + broken_links() + adapter_violations()
    if problems:
        print("Documentation gate failed:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("Documentation gate: OK")
    return 0


def drift(hook_mode: bool) -> int:
    if hook_mode:
        try:
            payload = json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError):
            payload = {}
        if payload.get("stop_hook_active"):
            return 0
    reminders = drift_reminders()
    if not reminders:
        return 0
    message = (
        "Doc-drift check (AGENTS.md Documentation contract): python files changed in "
        "documented packages, their READMEs did not. If the public surface, module list, or "
        "behavior described there changed, update the README (Modules table, Public API, "
        "Extension points) in the same commit. If the change is invisible to the docs, "
        "no action is needed.\n" + "\n".join(f"  - {r}" for r in reminders)
    )
    print(message, file=sys.stderr)
    return 2 if hook_mode else 0


def report() -> int:
    for pkg_readme in sorted(SRC.rglob("README.md")):
        pkg = pkg_readme.parent
        rel_pkg = str(pkg.relative_to(ROOT))
        last_doc = subprocess.run(
            [
                "git",
                "log",
                "-1",
                "--format=%h %ad",
                "--date=short",
                "--",
                str(pkg_readme.relative_to(ROOT)),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        code_since = subprocess.run(
            [
                "git",
                "log",
                "--oneline",
                f"--since={last_doc.split()[-1]}" if last_doc else "--all",
                "--",
                f"{rel_pkg}/*.py",
                f"{rel_pkg}/**/*.py",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        count = len(code_since.splitlines()) if code_since else 0
        marker = " ← check for drift" if count > 3 else ""
        print(f"{rel_pkg}: README {last_doc or 'uncommitted'}; {count} code commits since{marker}")
    return 0


def main() -> int:
    args = set(sys.argv[1:])
    if "--hook" in args:
        return drift(hook_mode=True)
    if "--drift" in args:
        return drift(hook_mode=False)
    if "--report" in args:
        return report()
    return structural()


if __name__ == "__main__":
    sys.exit(main())
