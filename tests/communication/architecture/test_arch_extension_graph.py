"""Extensions never import each other except along these explicit edges (M2.0: Extension->Extension implementation import is
prohibited by default; the composition root is peerhub/cli). A new edge must be a conscious change to this allowlist."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / "peerhub" / "extensions"

# Edges inside one extension family (several modules that are one unit) and edges to neutral, dependency-free helpers.
ALLOWED: dict[str, set[str]] = {
    # M1 Session Bridge entry
    "ask": {"accounts", "bridge", "bridge_claims", "observation", "observation_model", "adapters",
            "bridge_control", "bridge_ledger", "bridge_sessions", "runtime_contract", "caller"},  # caller: who started an ask (neutral; stdlib and psutil only)
    "adapters": {"accounts", "runtime_contract", "binary_resolution", "process_tree", "usage_normalization"},  # adapters depend on neutral RuntimeTarget, not the Bridge implementation
    "bridge": {"bridge_claims", "bridge_delivery", "bridge_schema", "bridge_sessions", "catchup", "runtime_contract"},
    "bridge_delivery": {"ask_budgets", "bridge_claims", "bridge_control", "bridge_errors", "bridge_ledger",
                        "bridge_sessions", "runtime_contract", "session_receipts"},
    "bridge_ledger": {"ask_budgets", "bridge_claims", "bridge_control", "bridge_errors", "bridge_registry",
                      "delivery_projections", "liveness", "schema_guard", "session_receipts", "sqlite_tx"},
    "bridge_schema": {"ask_budgets", "bridge_claims", "bridge_control", "bridge_registry", "schema_guard"},
    "bridge_sessions": {"bridge_claims", "bridge_control", "bridge_errors", "bridge_ledger", "bridge_registry",
                        "catchup", "runtime_contract", "session_receipts"},
    "bridge_registry": {"delivery_projections", "session_receipts"},
    "ask_budgets": {"session_receipts"},
    "profile_stats": {"ask_budgets", "quota_probes", "schema_guard", "session_receipts"},
    "delivery_projections": {"session_receipts", "usage_normalization"},
    "bridge_control": {"bridge_claims", "bridge_errors", "bridge_registry", "runtime_contract"},
    "session_receipts": {"usage_normalization"},
    "monitor_read_model": {"adapters", "monitor_activity", "monitor_alerts", "monitor_queries", "monitor_quota",
                           "monitor_sessions"},
    "monitor_activity": {"adapters", "caller", "diag_running", "liveness", "monitor_queries", "monitor_sessions",
                         "monitor_values"},
    "monitor_alerts": {"liveness", "monitor_activity", "monitor_preferences", "monitor_queries", "monitor_quota", "monitor_values"},
    "monitor_queries": {"observation_model"},
    "monitor_quota": {"monitor_queries", "observation_model", "quota_projection", "quota_windows"},
    "monitor_sessions": {"adapters", "monitor_queries", "monitor_values", "session_receipts", "usage_normalization"},
    "monitor_values": {"adapters"},
    "caller": {"process_tree"},
    "liveness": {"caller", "process_tree"},
    "lead_sessions": {"monitor_queries", "monitor_values",  # the activity window and display cleaners (#376)
                      "process_tree"},  # the creation FILETIME reader (neutral)
    # Read-only current-profile inspection, canonical observation evaluation,
    # shared quota projection math, and provider receipt validation.
    "bridge_claims": {"schema_guard"},
    # M1 Observation
    "observation": {"schema_guard", "observation_model"},
    "quota_capture": {"observation", "observation_model", "quota_probes", "quota_types"},
    "quota_probes": {"quota_types", "binary_resolution", "process_tree", "win_job"},
    "quota_types": {"observation_model"},
    # M1 Diag
    "diag": {"observation_model"},
    "diag_readiness": {"adapters", "diag", "observation_model", "quota_projection"},
    "diag_running": {"caller", "diag", "liveness"},
    "diag_quota": {"diag", "observation_model", "quota_projection"},
    "diag_watch": {"diag"},
    "diag_read_model": {"diag", "diag_quota", "observation_model", "pool_health", "quota_windows"},
    "quota_windows": {"observation_model", "quota_projection"},
    "pool_health": {"diag_quota", "observation_model", "quota_projection"},
    # M2
    "batch": {"process_tree"},
    "host": {"manifest", "sqlite_tx"},
    "work": {"authoritative", "sqlite_tx"},
    "skills": {"authoritative", "boundary", "sqlite_tx"},
    "artifact": {"boundary"},
    # M3
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
    m1 = {"ask", "bridge", "bridge_claims", "bridge_control", "bridge_delivery", "bridge_errors", "bridge_ledger",
          "bridge_schema", "bridge_sessions", "catchup", "observation", "observation_model", "quota_capture", "quota_probes", "win_job",
          "quota_types", "diag", "diag_quota", "diag_readiness", "diag_running", "diag_watch", "peer_kinds", "schema_guard", "adapters", "quota_projection", "bridge_registry", "session_receipts",
          "ask_budgets", "delivery_projections", "monitor_read_model", "usage_normalization", "runtime_contract",
          "caller", "liveness", "lead_sessions", "pool_health", "monitor_preferences", "monitor_queries",
          "monitor_activity", "monitor_alerts", "monitor_quota", "monitor_sessions", "monitor_values",
          "diag_read_model", "quota_windows", "profile_stats"}
    helpers = {"peer_kinds", "binary_resolution", "process_tree", "sqlite_tx", "boundary", "authoritative", "source_records", "manifest", "a2a"}  # neutral helpers / same-slice modules
    for src, dst in edges().items():
        if src in m1:
            continue
        assert dst <= helpers, f"{src} imports {sorted(dst - helpers)}: M2/M3 slices connect through public ports, not imports"


def test_the_allowlist_has_no_dead_entries():
    actual = edges()
    dead = {src: sorted(dst - actual.get(src, set())) for src, dst in ALLOWED.items() if dst - actual.get(src, set())}
    assert dead == {}, f"remove edges that no longer exist so the allowlist stays honest: {dead}"
