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
    "billing": {"billing_secret", "observation", "observation_model", "schema_guard", "usage_normalization"},
    "billing_port": {"billing"},  # optional import, exercised with billing blocked by the M1 exit gate
    "bridge": {"bridge_claims", "bridge_delivery", "bridge_schema", "bridge_sessions", "catchup"},
    "bridge_delivery": {"ask_budgets", "billing_port", "bridge_claims", "bridge_control", "bridge_errors", "bridge_ledger",
                        "bridge_sessions", "runtime_contract", "session_receipts", "usage_normalization"},
    "bridge_ledger": {"ask_budgets", "bridge_claims", "bridge_control", "bridge_errors", "bridge_registry",
                      "delivery_projections", "delivery_reads", "liveness", "schema_guard", "session_receipts"},
    "bridge_schema": {"ask_budgets", "bridge_claims", "bridge_control", "bridge_registry", "schema_guard"},
    "bridge_sessions": {"bridge_claims", "bridge_control", "bridge_errors", "bridge_ledger", "bridge_registry",
                        "catchup", "runtime_contract", "session_receipts"},
    "bridge_registry": {"delivery_projections", "session_receipts"},
    "ask_budgets": {"session_receipts"},
    "recovery_classification": {"adapters", "ask", "bridge_claims", "bridge_errors", "runtime_contract"},
    "profile_stats": {"ask_budgets", "quota_probes", "schema_guard", "session_receipts"},
    "delivery_projection_rebuild": {"delivery_projections", "session_receipts"},
    "delivery_projections": {"session_receipts", "usage_normalization"},
    "bridge_control": {"bridge_claims", "bridge_errors", "bridge_registry", "runtime_contract"},
    "session_receipts": {"usage_normalization"},
    "monitor_read_model": {"adapters", "billing_port", "monitor_activity", "monitor_alerts", "monitor_queries", "monitor_quota",
                           "monitor_sessions", "quota_read_model"},
    "monitor_activity": {"adapters", "caller", "diag_running", "liveness", "monitor_queries", "monitor_sessions",
                         "monitor_values"},
    "monitor_alerts": {"liveness", "monitor_activity", "monitor_preferences", "monitor_queries", "monitor_quota", "monitor_values"},
    "monitor_queries": {"delivery_reads", "observation_model"},
    "monitor_quota": {"monitor_queries", "observation_model", "quota_projection", "quota_read_model", "quota_windows"},
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
    "quota_probes": {"quota_types", "observation_model", "binary_resolution", "process_tree", "win_job"},
    "quota_types": {"observation_model"},
    # M1 Diag
    "diag": {"billing_port", "delivery_reads", "observation_model"},
    "diag_readiness": {"adapters", "diag", "observation_model", "quota_projection", "quota_windows"},
    "diag_running": {"caller", "diag", "liveness"},
    "diag_quota": {"diag", "observation_model", "quota_projection"},
    "diag_watch": {"diag"},
    "diag_read_model": {"billing_port", "diag", "observation_model", "pool_health", "quota_projection", "quota_read_model", "quota_windows"},
    "quota_projection": {"observation_model"},
    "quota_read_model": {"observation_model", "pool_health", "quota_windows"},
    "quota_windows": {"observation_model", "quota_projection"},
    "pool_health": {"observation_model", "quota_projection", "quota_windows"},
    # M2
    "batch": {"process_tree"},
    "host": {"manifest", "sqlite_tx"},
    "work": {"authoritative", "sqlite_tx"},
    "skills": {"authoritative", "skill_models", "skill_projection", "skill_source", "sqlite_tx"},
    "skill_projection": {"skill_models", "sqlite_tx"},
    "skill_source": {"boundary", "skill_models"},
    "artifact": {"boundary"},
    # M3
    "a2a_http": {"a2a"},
    "a2a_journal": {"a2a"},
    "orchestration": {"orchestration_deadline", "orchestration_journal", "orchestration_model"},
    "orchestration_deadline": {"orchestration_model"},
    "orchestration_journal": {"orchestration_model"},
}


def edges(root: Path = ROOT, namespace: str = "peerhub.extensions") -> dict[str, set[str]]:
    """Each module or package under root -> the siblings it imports.

    Absolute AND relative imports are resolved, lazy imports inside functions are included (ast.walk), and the Python files inside
    extension packages count as part of their package."""
    modules = {p.stem for p in root.glob("*.py") if p.stem != "__init__"} | {p.name for p in root.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    found: dict[str, set[str]] = {}
    depth = len(namespace.split("."))
    prefix = namespace + "."
    for path in root.rglob("*.py"):
        rel = path.relative_to(root)
        owner = rel.parts[0] if len(rel.parts) > 1 else rel.stem
        if owner == "__init__":
            continue
        package = [*namespace.split("."), *rel.parts[:-1]]  # the package this file lives in
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            targets: list[str] = []
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    base = package[: len(package) - (node.level - 1)]
                    absolute = ".".join(base + ([node.module] if node.module else []))
                else:
                    absolute = node.module or ""
                if absolute == namespace:
                    targets += [alias.name for alias in node.names]
                elif absolute.startswith(prefix):
                    targets.append(absolute.split(".")[depth])
            elif isinstance(node, ast.Import):
                targets += [a.name.split(".")[depth] for a in node.names if a.name.startswith(prefix)]
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


def test_cli_adapter_layers_import_only_their_dependencies():
    actual = edges(ROOT / "adapters", "peerhub.extensions.adapters")
    allowed = {
        "ag_transport": {"common", "process"},
        "cli_runtime": {"ag", "ag_transport", "cc", "common", "event_parser", "live_output", "process",
                        "profiles", "registry", "sanitize", "telemetry"},
        "event_parser": {"common", "process", "telemetry"},
        "live_output": {"common"},
        "registry": {"ag", "cc", "common", "cx"},
    }
    assert {name: actual.get(name, set()) for name in allowed} == allowed
    assert "cli_runtime" not in actual["api"]


def test_m2_m3_extensions_do_not_depend_on_each_other_across_slices():
    m1 = {"ask", "billing_port", "bridge", "bridge_claims", "bridge_control", "bridge_delivery", "bridge_errors", "bridge_ledger",
          "bridge_schema", "bridge_sessions", "catchup", "observation", "observation_model", "quota_capture", "quota_probes", "win_job",
          "quota_types", "diag", "diag_quota", "diag_readiness", "diag_running", "diag_watch", "schema_guard", "adapters", "quota_projection", "bridge_registry", "session_receipts",
          "ask_budgets", "delivery_projection_rebuild", "delivery_projections", "delivery_reads", "monitor_read_model", "usage_normalization", "runtime_contract",
          "caller", "liveness", "lead_sessions", "pool_health", "monitor_preferences", "monitor_queries",
          "monitor_activity", "monitor_alerts", "monitor_quota", "monitor_sessions", "monitor_values",
          "diag_read_model", "quota_read_model", "quota_windows", "recovery_classification", "profile_stats"}
    helpers = {"binary_resolution", "process_tree", "sqlite_tx", "boundary", "authoritative", "manifest"}
    families = (
        {"a2a", "a2a_http", "a2a_journal"},
        {"skills", "skill_models", "skill_projection", "skill_source"},
        {"orchestration", "orchestration_deadline", "orchestration_journal", "orchestration_model"},
    )
    for src, dst in edges().items():
        if src in m1:
            continue
        local = next((family for family in families if src in family), set())
        billing_inputs = ALLOWED["billing"] if src == "billing" else set()
        unexpected = dst - helpers - local - billing_inputs
        assert not unexpected, f"{src} imports {sorted(unexpected)}: M2/M3 slices connect through public ports, not imports"


def test_the_allowlist_has_no_dead_entries():
    actual = edges()
    dead = {src: sorted(dst - actual.get(src, set())) for src, dst in ALLOWED.items() if dst - actual.get(src, set())}
    assert dead == {}, f"remove edges that no longer exist so the allowlist stays honest: {dead}"
