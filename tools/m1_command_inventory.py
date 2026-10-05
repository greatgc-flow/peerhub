"""Canonical M1 public CLI command inventory, derived from the REAL argparse parser (never from prose).

  python tools/m1_command_inventory.py          # print the derived inventory
  python tools/m1_command_inventory.py --write  # regenerate docs/m1_impl/command_inventory.json
  python tools/m1_command_inventory.py --check  # exit 1 when the committed file diverges from the parser
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "docs" / "m1_impl" / "command_inventory.json"


def _options(parser: argparse.ArgumentParser) -> list[dict[str, Any]]:
    """Every published argument of ONE parser level: options (all aliases) and positionals (flags == [dest])."""
    out: list[dict[str, Any]] = []
    for a in parser._actions:  # noqa: SLF001 - argparse exposes no public walker
        if isinstance(a, (argparse._HelpAction, argparse._SubParsersAction)):  # noqa: SLF001
            continue
        positional = not a.option_strings
        out.append({"flags": [a.dest] if positional else list(a.option_strings), "dest": a.dest, "positional": positional,
                    "required": bool(a.required) if not positional else a.nargs not in ("?", "*"),
                    "takes_value": a.nargs != 0, "default": a.default if isinstance(a.default, (str, int, float, bool, type(None))) else repr(a.default)})
    return sorted(out, key=lambda o: o["flags"][0])


def _walk(parser: argparse.ArgumentParser, path: tuple[str, ...], leaves: list[dict[str, Any]], groups: list[dict[str, Any]]) -> None:
    subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]  # noqa: SLF001
    if not subs:
        leaves.append({"command": " ".join(path), "options": _options(parser)})
        return
    if path:  # intermediate level (the root is reported as global_options)
        groups.append({"command": " ".join(path), "options": _options(parser)})
    for name, sp in subs[0].choices.items():
        _walk(sp, (*path, name), leaves, groups)


def derive(parser: argparse.ArgumentParser | None = None) -> dict[str, Any]:
    if parser is None:
        from peerhub.m1_cli import build_parser

        parser = build_parser()
    leaves: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    _walk(parser, (), leaves, groups)
    return {"program": parser.prog, "global_options": _options(parser), "groups": sorted(groups, key=lambda c: c["command"]),
            "commands": sorted(leaves, key=lambda c: c["command"])}


def option_keys(inv: dict[str, Any]) -> list[str]:
    """One key per published argument alias at EVERY level: '* --db', 'diag quota --json', 'peer get <positional dest>'."""
    nodes = [("*", inv["global_options"])] + [(c["command"], c["options"]) for c in inv["groups"] + inv["commands"]]
    return sorted(f"{cmd} {flag}" for cmd, opts in nodes for o in opts for flag in o["flags"])


def render(inv: dict[str, Any]) -> str:
    return json.dumps(inv, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="regenerate the committed inventory file")
    ap.add_argument("--check", action="store_true", help="fail when the committed file diverges from the parser")
    a = ap.parse_args(argv)
    text = render(derive())
    if a.write:
        INVENTORY.write_text(text, encoding="utf-8", newline="\n")
    elif a.check:
        if not INVENTORY.is_file() or INVENTORY.read_text(encoding="utf-8") != text:
            print("command inventory diverges from the parser: run tools/m1_command_inventory.py --write", file=sys.stderr)
            return 1
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    sys.exit(main())
