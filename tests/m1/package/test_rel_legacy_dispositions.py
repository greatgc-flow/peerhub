"""Wave 7: REL-012 - the 109 legacy leaf commands are classified exactly once and match the frozen call-map AND the REAL parser."""
import argparse
import copy
import csv
import json
import re

import pytest

from tests.m1.harness.pkg_env import REPO

pytestmark = [pytest.mark.package, pytest.mark.traceability]

SPEC = REPO / "docs/m1_spec"
CSV_PATH = SPEC / "02_EXTENSIONS/109_COMMAND_DISPOSITION.csv"
MD_PATH = SPEC / "02_EXTENSIONS/109_COMMAND_DISPOSITION.md"
EVIDENCE = SPEC / "07_AUDIT/CURRENT_REPO_COMMAND_MAP_20261003.json"
DISPOSITIONS = {"ABSORB_M1": 16, "SUPERSEDE_M1": 5, "M1_EXTENSION": 12, "FUTURE_EXTENSION": 76}  # literal oracle (shell uniq -c of the CSV)


class _Captured(Exception):
    pass


def real_parser(monkeypatch) -> argparse.ArgumentParser:
    """The parser the shipped legacy CLI really builds (captured at parse time, nothing executes)."""
    import peerhub.cli

    got = []

    def capture(self, args=None, namespace=None):
        got.append(self)
        raise _Captured

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", capture)
    with pytest.raises(_Captured):
        peerhub.cli.main([])
    assert len(got) == 1
    return got[0]


def leaf_paths(parser) -> list[str]:
    subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    if not subs:
        return [""]
    out = []
    for name, child in subs[0].choices.items():
        for rest in leaf_paths(child):
            out.append(f"{name} {rest}".strip())
    return out


def load_csv():
    with CSV_PATH.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_evidence():
    return json.loads(EVIDENCE.read_text(encoding="utf-8"))


def md_rows():
    rows = []
    for line in MD_PATH.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*(\d+)\s*\|\s*`([^`]+)`\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*([^|]+?)\s*\|\s*(\w+)\s*\|$", line)
        if m:
            rows.append(m.groups())
    return rows


def compare(cli_leaves, csv_rows, evidence) -> list[str]:
    """All inconsistencies between the real CLI, the disposition inventory and the frozen call-map (empty = consistent)."""
    errs = []
    paths = [r["command"] for r in csv_rows]
    for p in sorted({p for p in paths if paths.count(p) > 1}):
        errs.append(f"duplicate disposition row: {p}")
    errs += [f"unclassified legacy command: {p}" for p in cli_leaves if p not in set(paths)]
    errs += [f"stale disposition row (no such legacy command): {p}" for p in paths if p not in set(cli_leaves)]
    if len(cli_leaves) != len(set(cli_leaves)):
        errs.append("real CLI exposes duplicate leaf paths")
    cmds = evidence["commands"]
    if [c["path"] for c in cmds] != paths:
        errs.append("order/path drift between disposition rows and frozen call-map")
    elif [c["effect"] for c in cmds] != [r["current_effect"] for r in csv_rows]:
        errs.append("effect drift between disposition rows and frozen call-map")
    if [c["path"] for c in cmds] != list(cli_leaves):
        errs.append("order/path drift between real CLI and frozen call-map")
    for r in csv_rows:
        if not (r["disposition"] and r["target_component"] and r["milestone"]):
            errs.append(f"incomplete disposition: {r['command']}")
    return errs


def test_rel_012_109_dispositions_match_real_cli_and_frozen_call_map_both_directions(monkeypatch):
    leaves = leaf_paths(real_parser(monkeypatch))
    rows, ev = load_csv(), load_evidence()
    assert len(leaves) == 109 and len(set(leaves)) == 109  # the real parser, counted independently of the inventory
    assert len(rows) == 109 and len({r["command"] for r in rows}) == 109
    assert ev["measurement"]["leaf_command_count"] == 109 and len(ev["commands"]) == 109
    assert [c["ordinal"] for c in ev["commands"]] == list(range(1, 110))
    assert compare(leaves, rows, ev) == []
    assert {d: sum(r["disposition"] == d for r in rows) for d in DISPOSITIONS} == DISPOSITIONS
    assert sum(DISPOSITIONS.values()) == 109


def test_rel_012_markdown_inventory_is_the_same_109_rows():
    rows, md = load_csv(), md_rows()
    assert len(md) == 109
    assert [(int(n), c, e, d, t, m) for n, c, e, d, t, m in md] == [
        (i, r["command"], r["current_effect"], r["disposition"], r["target_component"], r["milestone"]) for i, r in enumerate(rows, 1)]


@pytest.mark.parametrize("mutation", ["drop_row", "phantom_row", "duplicate_row", "swap_order", "effect_changed", "blank_disposition"])
def test_rel_012_comparator_detects_every_kind_of_drift(monkeypatch, mutation):
    leaves = leaf_paths(real_parser(monkeypatch))
    rows, ev = load_csv(), load_evidence()
    assert compare(leaves, rows, ev) == []  # positive control on the unmodified inputs
    rows, ev = copy.deepcopy(rows), copy.deepcopy(ev)
    expect = {
        "drop_row": "unclassified legacy command: " + rows[10]["command"],
        "phantom_row": "stale disposition row (no such legacy command): ghost cmd",
        "duplicate_row": "duplicate disposition row: " + rows[3]["command"],
        "swap_order": "order/path drift",
        "effect_changed": "effect drift",
        "blank_disposition": "incomplete disposition: " + rows[0]["command"],
    }[mutation]
    if mutation == "drop_row":
        del rows[10]
    elif mutation == "phantom_row":
        rows.append({**rows[0], "command": "ghost cmd"})
    elif mutation == "duplicate_row":
        rows.append(dict(rows[3]))
    elif mutation == "swap_order":
        rows[0], rows[1] = rows[1], rows[0]
    elif mutation == "effect_changed":
        rows[5]["current_effect"] = "READ_ONLY" if rows[5]["current_effect"] != "READ_ONLY" else "MUTATING"
    else:
        rows[0]["disposition"] = ""
    assert any(expect in e for e in compare(leaves, rows, ev)), compare(leaves, rows, ev)


def test_rel_012_comparator_detects_a_new_legacy_command_added_to_the_real_parser(monkeypatch):
    parser = real_parser(monkeypatch)
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    sub.add_parser("brand-new-command")
    leaves = leaf_paths(parser)
    assert len(leaves) == 110
    errs = compare(leaves, load_csv(), load_evidence())
    assert "unclassified legacy command: brand-new-command" in errs


def test_rel_012_every_classified_command_is_actually_dispatchable_in_the_real_cli(capsys):
    """Beyond the parser tree: each of the 109 recorded paths resolves through the shipped CLI entry (`<path> --help` exits 0),
    and a path that is not a legacy command does not (positive and negative control)."""
    import peerhub.cli

    for row in load_csv():
        with pytest.raises(SystemExit) as ei:
            peerhub.cli.main([*row["command"].split(), "--help"])
        assert ei.value.code == 0, row["command"]
    capsys.readouterr()
    with pytest.raises(SystemExit) as bad:
        peerhub.cli.main(["definitely-not-a-command", "--help"])
    assert bad.value.code != 0
    capsys.readouterr()
