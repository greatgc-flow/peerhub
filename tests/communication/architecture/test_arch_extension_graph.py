"""Extensions never import each other except along these explicit edges (M2.0: Extension->Extension implementation import is
prohibited by default; the composition root is peerhub/cli). A new edge must be a conscious change to this allowlist."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / "peerhub" / "extensions"

# Edges inside one extension family (several modules that are one unit) and edges to neutral, dependency-free helpers.
ALLOWED: dict[str, set[str]] = {
    # M1 Session Bridge entry
    "ask": {"bridge", "bridge_claims", "observation", "observation_model", "peer_kinds", "adapters"},
    "adapters": {"bridge"},  # runtime adapters implement the Session Bridge RuntimeTarget protocol
    "bridge": {"bridge_claims", "catchup", "schema_guard"},
    "bridge_claims": {"schema_guard"},
    # M1 Observation
    "observation": {"schema_guard", "observation_model"},
    "quota_capture": {"observation", "observation_model", "peer_kinds", "quota_probes", "quota_types"},
    "quota_probes": {"quota_types", "adapters"},
    "quota_types": {"observation_model"},
    # M1 Diag
    "diag": {"observation_model"},
    "diag_quota": {"diag", "observation_model"},
    "diag_watch": {"diag"},
    # M2
    "host": {"manifest", "sqlite_tx"},
    "work": {"sqlite_tx"},
    "skills": {"boundary", "sqlite_tx"},
    "artifact": {"boundary"},
    # M3
    "a2a": {"a2a_journal"},  # one A2A unit: the adapter lazily uses its durable journal
    "a2a_http": {"a2a"},
    "a2a_journal": {"a2a"},
    "memory": {"source_records"},
    "search": {"source_records"},
}


def edges(root: Path = ROOT) -> dict[str, set[str]]:
    """Top-level extension (module or package) -> the other top-level extensions it imports.

    Absolute AND relative imports are resolved, lazy imports inside functions are included (ast.walk), and the Python files inside
    extension packages count as part of their package."""
    modules = {p.stem for p in root.glob("*.py") if p.stem != "__init__"} | {p.name for p in root.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    found: dict[str, set[str]] = {}
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        owner = rel.parts[0] if len(rel.parts) > 1 else rel.stem
        if owner == "__init__":
            continue
        package = ["peerhub", "extensions", *rel.parts[:-1]]  # the package this file lives in
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            targets: list[str] = []
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    base = package[: len(package) - (node.level - 1)]
                    absolute = ".".join(base + ([node.module] if node.module else []))
                else:
                    absolute = node.module or ""
                if absolute.startswith("peerhub.extensions"):
                    parts = absolute.split(".")
                    targets += [parts[2]] if len(parts) >= 3 else [alias.name for alias in node.names]
            elif isinstance(node, ast.Import):
                targets += [a.name.split(".")[2] for a in node.names if a.name.startswith("peerhub.extensions.")]
            for target in targets:
                if target in modules and target != owner:
                    found.setdefault(owner, set()).add(target)
    return found


def test_the_graph_scan_sees_relative_imports_and_package_internals(tmp_path):
    (tmp_path / "memory.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "eval.py").write_text("def f():\n    from .memory import X\n    return X\n", encoding="utf-8")  # lazy + relative
    (tmp_path / "work.py").write_text("from . import memory\n", encoding="utf-8")  # `from . import x`
    pkg = tmp_path / "adapters"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "inner.py").write_text("from ..memory import X\nfrom peerhub.extensions.eval import f\n", encoding="utf-8")  # inside a package
    graph = edges(tmp_path)
    assert graph == {"eval": {"memory"}, "work": {"memory"}, "adapters": {"memory", "eval"}}


def test_extension_import_graph_matches_the_documented_allowlist():
    actual = edges()
    unexpected = {src: sorted(dst - ALLOWED.get(src, set())) for src, dst in actual.items() if dst - ALLOWED.get(src, set())}
    assert unexpected == {}, f"undocumented extension->extension imports (compose in peerhub/cli instead): {unexpected}"


def test_m2_m3_extensions_do_not_depend_on_each_other_across_slices():
    m1 = {"ask", "bridge", "bridge_claims", "catchup", "observation", "observation_model", "quota_capture", "quota_probes",
          "quota_types", "diag", "diag_quota", "diag_watch", "peer_kinds", "schema_guard", "adapters"}
    helpers = {"sqlite_tx", "boundary", "source_records", "manifest", "a2a", "a2a_journal"}  # neutral helpers / same-slice modules
    for src, dst in edges().items():
        if src in m1:
            continue
        assert dst <= helpers, f"{src} imports {sorted(dst - helpers)}: M2/M3 slices connect through public ports, not imports"


def test_the_allowlist_has_no_dead_entries():
    actual = edges()
    dead = {src: sorted(dst - actual.get(src, set())) for src, dst in ALLOWED.items() if dst - actual.get(src, set())}
    assert dead == {}, f"remove edges that no longer exist so the allowlist stays honest: {dead}"
