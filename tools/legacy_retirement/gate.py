"""Fail-closed preservation evidence for LegacyTranslator rewrite batches.

Run ``python tools/legacy_retirement/gate.py --help``. Snapshots and evidence
are retained; this tool never changes the checkout, Git index, or Git history.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile


def read(path):
    # Universal newlines intentionally handle the repository's CRCRLF sources.
    return Path(path).read_text(encoding="utf-8-sig")


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normalized(value, root, temp):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [normalized(v, root, temp) for v in value]
    if isinstance(value, dict):
        return {str(k): normalized(v, root, temp) for k, v in sorted(value.items(), key=lambda x: str(x[0]))}
    text = value if isinstance(value, str) else repr(value)
    for path, replacement in ((str(temp), "<pytest-temp>"), (str(root), "<snapshot>")):
        text = text.replace(path, replacement).replace(path.replace("\\", "/"), replacement)
    return text


def test_ast(item):
    tree = ast.parse(read(item.path))
    qualified = item.obj.__qualname__.split(".")
    body = tree.body
    node = None
    for name in qualified:
        matches = [n for n in body if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
        if len(matches) != 1:
            raise ValueError(f"Cannot uniquely locate AST for {item.nodeid}: {qualified}")
        node = matches[0]
        body = node.body
    assertions = []
    for assertion in sorted((n for n in ast.walk(node) if isinstance(n, ast.Assert)), key=lambda n: n.lineno):
        assertions.append({"line": assertion.lineno, "expression": ast.unparse(assertion.test),
                           "normalized_ast": ast.dump(assertion.test, include_attributes=False),
                           "message_ast": ast.dump(assertion.msg, include_attributes=False) if assertion.msg else None})
    return {"assertion_count": len(assertions), "assertions": assertions,
            "function_ast": ast.dump(node, include_attributes=False), "function_source": ast.unparse(node)}


class EvidencePlugin:
    def __init__(self, root, temp):
        self.root, self.temp = root, temp
        self.items, self.selected, self.deselected, self.reports, self.collection_errors = {}, [], [], {}, []

    def pytest_itemcollected(self, item):
        self.items[item.nodeid] = test_ast(item)

    def pytest_collection_finish(self, session):
        self.selected = [item.nodeid for item in session.items]
        for item in session.items:
            self.items[item.nodeid]["markers"] = [
                {"name": m.name, "args": normalized(m.args, self.root, self.temp),
                 "kwargs": normalized(m.kwargs, self.root, self.temp)} for m in item.iter_markers()]
            self.items[item.nodeid]["fixture_names"] = list(item.fixturenames)

    def pytest_deselected(self, items):
        self.deselected.extend(item.nodeid for item in items)
        for item in items:
            self.items[item.nodeid]["markers"] = [
                {"name": m.name, "args": normalized(m.args, self.root, self.temp),
                 "kwargs": normalized(m.kwargs, self.root, self.temp)} for m in item.iter_markers()]

    def pytest_collectreport(self, report):
        if report.failed:
            self.collection_errors.append(normalized(str(report.longrepr), self.root, self.temp))

    def pytest_runtest_logreport(self, report):
        self.reports.setdefault(report.nodeid, []).append({
            "phase": report.when, "outcome": report.outcome,
            "wasxfail": normalized(getattr(report, "wasxfail", None), self.root, self.temp),
            "longrepr": normalized(str(report.longrepr), self.root, self.temp) if report.longrepr else None,
        })


def production_coverage(cov, root):
    result = {}
    for path in sorted((root / "peerhub").rglob("*.py")):
        source = read(path)
        tree = ast.parse(source)
        excluded = set()
        if path.relative_to(root).as_posix() == "peerhub/application/legacy.py":
            classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "LegacyTranslator"]
            if len(classes) > 1:
                raise ValueError("Ambiguous LegacyTranslator exclusion")
            for node in classes:
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                excluded.update(range(start, node.end_lineno + 1))
        # coverage has no public API exposing all possible arcs. Version is
        # recorded and the private API is guarded by the ordinary failure path.
        analysis = cov._analyze(str(path))
        lines = set(analysis.statements) - excluded
        arcs = {tuple(a) for a in analysis.arc_possibilities
                if abs(a[0]) not in excluded and abs(a[1]) not in excluded}
        exits = Counter(a[0] for a in arcs if a[0] > 0)
        branches = {a for a in arcs if exits[a[0]] > 1}
        executed_arcs = {tuple(a) for a in cov.get_data().arcs(str(path)) or []}
        executed_lines = set(cov.get_data().lines(str(path)) or []) & lines
        # Remove precisely the excluded class text for comparison, retaining
        # every other byte after newline decoding, including legacy helpers.
        remaining_source = "\n".join(line for num, line in enumerate(source.splitlines(), 1) if num not in excluded)
        result[path.relative_to(root).as_posix()] = {
            "source_sha256": digest(path),
            "nonexcluded_source_sha256": hashlib.sha256(remaining_source.encode()).hexdigest(),
            "excluded_class_lines": sorted(excluded),
            "line_denominator": len(lines), "line_possible": sorted(lines),
            "line_executed": sorted(executed_lines),
            "branch_denominator": len(branches), "branch_possible": sorted(branches),
            "branch_executed": sorted(branches & executed_arcs),
            "arc_possible": sorted(arcs), "arc_executed": sorted(arcs & executed_arcs),
        }
    return result


def capture(args):
    root, output = Path(args.root).resolve(), Path(args.output).resolve()
    temp = output / "pytest-temp"
    sys.path.insert(0, str(root))
    sys.path.insert(1, str(root / "tests"))
    import coverage
    cov = coverage.Coverage(data_file=str(output / "coverage.data"), source=[str(root / "peerhub")],
                            branch=True, config_file=False)
    cov.set_option("report:exclude_lines", [])
    cov.set_option("report:partial_branches", [])
    cov.start()
    import pytest
    plugin = EvidencePlugin(root, temp)
    pytest_args = ["-q", "-p", "no:cacheprovider", "-p", "no:cov", "--basetemp", str(temp), *args.tests]
    if args.collect:
        pytest_args.insert(0, "--collect-only")
    code = pytest.main(pytest_args, plugins=[plugin])
    cov.stop()
    cov.save()
    imported = {name: str(Path(mod.__file__).resolve()) for name, mod in sys.modules.items()
                if (name == "peerhub" or name.startswith("peerhub.")) and getattr(mod, "__file__", None)}
    leaked = {name: path for name, path in imported.items() if not Path(path).is_relative_to(root)}
    evidence = {"schema": 1, "python": sys.version, "pytest": pytest.__version__, "coverage": coverage.__version__,
                "pytest_args": normalized(pytest_args, root, temp), "exit_code": int(code),
                "collected": plugin.items, "selected": plugin.selected, "deselected": plugin.deselected,
                "reports": plugin.reports, "collection_errors": plugin.collection_errors,
                "import_leaks": leaked, "production": production_coverage(cov, root) if not args.collect else {}}
    write_json(output / "evidence.json", evidence)
    return int(code)


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def snapshot(repo, ref, destination, worktree=False):
    destination.mkdir(parents=True, exist_ok=False)
    if worktree:
        paths = git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard").decode().split("\0")
        for relative in sorted(set(filter(None, paths))):
            # Runner and evidence are outside the test/product snapshot.
            if relative.startswith(("tools/legacy_retirement/", "docs/reviews/legacy-translator-retirement/gate-")):
                continue
            source, target = repo / relative, destination / relative
            if not source.exists():
                continue
            if not source.resolve().is_relative_to(repo) or not target.resolve().is_relative_to(destination):
                raise ValueError(f"Snapshot path escapes repository: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    else:
        archive = git(repo, "archive", "--format=tar", ref)
        with tarfile.open(fileobj=io.BytesIO(archive)) as members:
            for member in members:
                target = destination / member.name
                if not target.resolve().is_relative_to(destination) or not (member.isfile() or member.isdir()):
                    raise ValueError(f"Unsafe archive member: {member.name}")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with members.extractfile(member) as source, target.open("wb") as sink:
                        shutil.copyfileobj(source, sink)
    return {p.relative_to(destination).as_posix(): digest(p) for p in sorted(destination.rglob("*")) if p.is_file()}


def translator_only(function_source, expression):
    """Conservative syntax proof, not a claim of behavioral equivalence.

    Only field/type/success checks on a direct LegacyTranslator.translate result
    qualify. Wire methods and arbitrary expressions never qualify.
    """
    function = ast.parse(function_source)
    aliases = {n.asname or n.name for imp in ast.walk(function) if isinstance(imp, ast.ImportFrom)
               and imp.module == "peerhub.application.legacy" for n in imp.names if n.name == "LegacyTranslator"}
    if not aliases:
        # Imports may be module scoped. The caller includes verified module imports.
        return False
    assignments = [n for n in ast.walk(function) if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)]
    bindings = Counter(n.targets[0].id for n in assignments)
    translators = {n.targets[0].id for n in assignments if isinstance(n.value, ast.Call)
                   and isinstance(n.value.func, ast.Name) and n.value.func.id in aliases and bindings[n.targets[0].id] == 1}
    results = {n.targets[0].id for n in assignments if isinstance(n.value, ast.Call)
               and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == "translate"
               and isinstance(n.value.func.value, ast.Name) and n.value.func.value.id in translators
               and bindings[n.targets[0].id] == 1}
    expr = ast.parse(expression, mode="eval").body
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
        call = expr.operand
        return (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "hasattr"
                and len(call.args) == 2 and isinstance(call.args[0], ast.Name) and call.args[0].id in results
                and isinstance(call.args[1], ast.Constant) and call.args[1].value == "reason" and not call.keywords)
    if isinstance(expr, ast.Compare) and len(expr.ops) == 1 and isinstance(expr.ops[0], (ast.Eq, ast.Is)):
        value = expr.left
        # outcome.command.FIELD == literal: explicitly NOT encode_params().
        if not (isinstance(value, ast.Attribute) and isinstance(value.value, ast.Attribute)
                and value.value.attr == "command" and isinstance(value.value.value, ast.Name)
                and value.value.value.id in results):
            return False
        try:
            ast.literal_eval(expr.comparators[0])
            return True
        except (ValueError, TypeError):
            return False
    return False


def assertion_key(assertion):
    return (assertion["normalized_ast"], assertion["message_ast"])


def compare(before, after, waivers):
    failures, removed, additions, accepted = [], [], [], []
    for label, data in (("before", before), ("after", after)):
        if data["exit_code"] or data["collection_errors"] or data["import_leaks"]:
            failures.append(f"{label}: run failed, collection errors, or production import leak")
    for key in ("selected", "deselected"):
        if before[key] != after[key]:
            failures.append(f"Collection {key} differs")
    if set(before["collected"]) != set(after["collected"]):
        failures.append("Collected node IDs differ")
    used = set()
    for nodeid, old in before["collected"].items():
        if nodeid not in after["collected"]:
            continue
        new = after["collected"][nodeid]
        if old.get("markers") != new.get("markers"):
            failures.append(f"Markers differ: {nodeid}")
        if before["reports"].get(nodeid) != after["reports"].get(nodeid):
            failures.append(f"Setup/call/teardown outcomes differ: {nodeid}")
        if nodeid in before["selected"] and not before["reports"].get(nodeid):
            failures.append(f"Missing actual execution reports: {nodeid}")
        old_counts, new_counts = Counter(map(assertion_key, old["assertions"])), Counter(map(assertion_key, new["assertions"]))
        for assertion in old["assertions"]:
            key = assertion_key(assertion)
            if new_counts[key]:
                new_counts[key] -= 1
                continue
            entry = {"nodeid": nodeid, **assertion}
            removed.append(entry)
            candidates = [(i, w) for i, w in enumerate(waivers) if i not in used and w.get("nodeid") == nodeid
                          and w.get("expression") == assertion["expression"] and w.get("reason", "").strip()]
            if len(candidates) == 1 and translator_only(old["function_source"], assertion["expression"]):
                i, waiver = candidates[0]
                used.add(i)
                accepted.append({**entry, "reason": waiver["reason"]})
            else:
                failures.append(f"Removed/changed assertion without valid translator-only waiver: {nodeid}: {assertion['expression']}")
        for assertion in new["assertions"]:
            key = assertion_key(assertion)
            if old_counts[key]:
                old_counts[key] -= 1
            else:
                additions.append({"nodeid": nodeid, **assertion})
    if len(used) != len(waivers):
        failures.append("Unused, duplicate, or structurally invalid assertion waiver(s)")
    delta = {}
    if set(before["production"]) != set(after["production"]):
        failures.append("Production file inventory differs")
    for name, old in before["production"].items():
        new = after["production"].get(name)
        if new is None:
            continue
        if old["nonexcluded_source_sha256"] != new["nonexcluded_source_sha256"]:
            failures.append(f"Production source changed outside excluded class: {name}")
        change = {}
        for kind in ("line", "branch"):
            def values(data, key):
                return {tuple(v) if isinstance(v, list) else v for v in data[key]}
            possible_old, possible_new = values(old, kind + "_possible"), values(new, kind + "_possible")
            executed_old, executed_new = values(old, kind + "_executed"), values(new, kind + "_executed")
            if possible_old != possible_new:
                failures.append(f"{kind} denominator/source coordinates differ: {name}; explicit source mapping required")
            lost, gained = sorted(executed_old - executed_new), sorted(executed_new - executed_old)
            change[kind] = {"before": len(executed_old), "after": len(executed_new),
                            "denominator_before": len(possible_old), "denominator_after": len(possible_new),
                            "lost": lost, "gained": gained}
            if lost:
                failures.append(f"Lost {kind} coverage: {name}: {lost}")
        if any(change[k]["lost"] or change[k]["gained"] for k in change):
            delta[name] = change
    return {"passed": not failures, "failures": failures, "removed_assertions": removed,
            "added_assertions": additions, "accepted_translator_only_removals": accepted, "coverage_delta": delta}


def markdown(result, before, after):
    lines = ["# LegacyTranslator preservation gate", "", "Result: " + ("PASS" if result["passed"] else "FAIL"), "",
             "The baseline uses unmodified Git source in an isolated snapshot. Coverage measures every production Python file; only the AST LegacyTranslator class block is excluded. Exact executed sets must be preserved, so gains cannot conceal losses.", "",
             "## Per-node assertions", "", "| Node ID | Before | After |", "| --- | ---: | ---: |"]
    for node, value in before["collected"].items():
        lines.append(f"| `{node}` | {value['assertion_count']} | {after['collected'].get(node, {}).get('assertion_count', 'REMOVED')} |")
    lines += ["", "Normalized expressions, messages, markers, parameter IDs, all phase outcomes, source hashes, exact line/branch denominators and executed sets are retained in the paired evidence JSON files. Raw sources, collection/run logs and coverage databases are retained alongside them.", "", "## Assertion changes", ""]
    accepted = {(a["nodeid"], a["expression"]): a["reason"] for a in result["accepted_translator_only_removals"]}
    for action, key in (("Removed", "removed_assertions"), ("Added", "added_assertions")):
        for a in result[key]:
            reason = accepted.get((a["nodeid"], a["expression"]))
            lines.append(f"- {action} `{a['nodeid']}`: `{a['expression']}`" + (f"; translator-only waiver: {reason}" if reason else ""))
    lines += ["", "## Exact coverage changes", "", "| File | Lines before/after/denominator | Branches before/after/denominator | Lost lines | Lost branches |", "| --- | --- | --- | --- | --- |"]
    for name, change in result["coverage_delta"].items():
        line, branch = change["line"], change["branch"]
        lines.append(f"| `{name}` | {line['before']}/{line['after']}/{line['denominator_before']} | {branch['before']}/{branch['after']}/{branch['denominator_before']} | `{line['lost']}` | `{branch['lost']}` |")
    lines += ["", "## Failures", ""] + [f"- {failure}" for failure in result["failures"]]
    lines += ["", "## Limits", "", "This checks assertion syntax and actual selected-test execution, not universal semantic equivalence or helper assertion reachability. Unchanged assertion text can still observe changed values; application-boundary and fixture review remain necessary. Translator-only waivers never waive coverage loss. Changed production coordinates fail closed. This rewrite gate does not authorize final intentional test retirement.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tests", nargs="+", help="Repository-relative pytest files or node IDs (parameter IDs preserved)")
    parser.add_argument("--repo", default=".", help="Repository root (default: current directory)")
    parser.add_argument("--baseline", default="b9031b4", help="Immutable baseline Git revision")
    parser.add_argument("--output", required=True, help="NEW evidence directory, retained with both source snapshots")
    parser.add_argument("--waivers", help="JSON array: nodeid, exact removed expression, nonempty translator-only reason")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    parser.add_argument("--capture", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--collect", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.capture:
        return capture(args)
    repo, output = Path(args.repo).resolve(), Path(args.output).resolve()
    if output.exists():
        parser.error("--output must not already exist; evidence is never overwritten")
    for test in args.tests:
        if not (repo / test.split("::", 1)[0]).resolve().is_relative_to(repo):
            parser.error("Test paths must stay inside the repository")
    baseline = git(repo, "rev-parse", "--verify", args.baseline + "^{commit}").decode().strip()
    output.mkdir(parents=True)
    manifest = {"baseline_commit": baseline, "head_commit": git(repo, "rev-parse", "HEAD").decode().strip(),
                "runner_sha256": digest(__file__), "tests": args.tests,
                "worktree_status": git(repo, "status", "--short").decode(), "snapshots": {}, "commands": []}
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("COVERAGE_PROCESS_START", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for label in ("before", "after"):
        root = output / (label + "-source")
        manifest["snapshots"][label] = snapshot(repo, baseline, root, worktree=(label == "after"))
        env["PYTHONPATH"] = os.pathsep.join((str(root), str(root / "tests")))
        for mode in ("collect", "run"):
            evidence_dir = output / (label + "-" + mode)
            evidence_dir.mkdir()
            command = [sys.executable, str(Path(__file__).resolve()), "--capture", "--root", str(root),
                       "--output", str(evidence_dir), *(["--collect"] if mode == "collect" else []), *args.tests]
            manifest["commands"].append(command)
            with (evidence_dir / "pytest.log").open("w", encoding="utf-8") as log:
                completed = subprocess.run(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT)
            print(f"{label} {mode}: exit {completed.returncode}; {evidence_dir}", flush=True)
    write_json(output / "manifest.json", manifest)
    before, after = [json.loads(read(output / (label + "-run") / "evidence.json")) for label in ("before", "after")]
    waivers = json.loads(read(args.waivers)) if args.waivers else []
    result = compare(before, after, waivers)
    for label, run in (("before", before), ("after", after)):
        collected = json.loads(read(output / (label + "-collect") / "evidence.json"))
        if collected["exit_code"] or collected["collected"] != run["collected"] or collected["selected"] != run["selected"]:
            result["failures"].append(f"{label}: standalone collect-only differs from actual run or failed")
        # Detect test-created mutations in the source snapshot.
        for relative, expected in manifest["snapshots"][label].items():
            path = output / (label + "-source") / relative
            if not path.exists() or digest(path) != expected:
                result["failures"].append(f"{label}: snapshot source mutated during execution: {relative}")
    result["passed"] = not result["failures"]
    write_json(output / "comparison.json", result)
    write_json(output / "waivers.json", waivers)
    (output / "report.md").write_text(markdown(result, before, after), encoding="utf-8")
    print(("PASS" if result["passed"] else "FAIL") + f": {len(result['failures'])} failure(s); {output / 'report.md'}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
