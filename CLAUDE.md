# CLAUDE.md

@AGENTS.md

The line above imports the canonical, vendor-neutral instructions ([AGENTS.md](AGENTS.md)) —
Claude Code expands `@`-imports at session start. All project rules, the repository map, and the
Definition of done live there; keep this file a thin adapter and never fork content into it
(`scripts/check_docs.py` enforces the `@AGENTS.md` reference).

Claude-Code-specific notes only below this line.

- A `Stop` hook (`.claude/settings.json`) runs `scripts/check_docs.py --hook` after each turn and
  reminds once if code changed in a documented package without its README being touched. It is a
  reminder, not a blocker — act on it per the AGENTS.md "Documentation" contract.
