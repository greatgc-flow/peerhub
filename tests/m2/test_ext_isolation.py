"""Wave 0: EXT-001 and EXT-002 - Static and Dynamic Isolation Boundaries.

Verifies:
- EXT-001: Core operates identically with all extensions disabled (dynamic sys.modules check).
- EXT-002: Core package import graph statically forbids extension imports (AST traversal).
"""

import ast
from pathlib import Path
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.architecture
def test_ext_001_core_operates_with_zero_extension_imports(tmp_path):
    """EXT-001: Core executes peer/stream/record workflows without importing M2/extension modules."""
    db_path = tmp_path / "test_core.db"

    # Capture currently loaded extension/m2 modules
    initial_ext_modules = {m for m in sys.modules if "peerhub.extensions" in m or "peerhub.m2" in m}

    # Import and run pure Core operations
    from peerhub.m1.store import CoreStore
    from peerhub.m1.models import Peer, Stream, StreamState, utc_now_iso

    store = CoreStore(db_path)

    peer = Peer(peer_id="peer_a")
    store.register_peer(peer)

    stream = Stream(stream_id="stream_a", title="test", state=StreamState.OPEN, members=["peer_a"])
    store.create_stream(stream)

    store.append_record(
        stream_id="stream_a",
        author_peer_id="peer_a",
        kind="test.event",
        body={"msg": "hello"},
        idempotency_key="key-1",
        created_at=utc_now_iso(),
    )

    # Inspect sys.modules after core operations
    new_ext_modules = {
        m for m in sys.modules
        if ("peerhub.extensions" in m or "peerhub.m2" in m) and m not in initial_ext_modules
    }

    assert new_ext_modules == set(), f"Core operations dynamically imported extension modules: {new_ext_modules}"


@pytest.mark.architecture
def test_ext_002_core_import_graph_statically_forbids_extension_namespaces():
    """EXT-002: Static AST analysis confirms peerhub/m1 core never imports extension or M2 packages."""
    core_dir = REPO_ROOT / "peerhub" / "m1"
    assert core_dir.is_dir(), f"Core directory not found at {core_dir}"

    forbidden_prefixes = ("peerhub.extensions", "peerhub.m2", "peerhub.application", "peerhub.dispatch")
    violations = []

    for py_file in core_dir.glob("**/*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name == f or alias.name.startswith(f + ".") for f in forbidden_prefixes):
                        violations.append((py_file.name, node.lineno, alias.name))
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if any(mod == f or mod.startswith(f + ".") for f in forbidden_prefixes):
                    violations.append((py_file.name, node.lineno, mod))

    assert violations == [], f"Static AST violations found in core: {violations}"
