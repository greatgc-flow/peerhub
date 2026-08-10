import ast
from pathlib import Path
import pytest

# Repo root, derived from this file's own location (tests/static/ -> repo root),
# so the check survives a move or rename of the drive/directory holding the repo.
REPO_ROOT = Path(__file__).resolve().parents[2]

def test_client_never_imports_persistence():
    """Static architectural check: Client must never import from persistence."""
    client_file = REPO_ROOT / "peerhub" / "client.py"
    assert client_file.exists(), f"Could not find {client_file}"
    
    content = client_file.read_text(encoding="utf-8")
    tree = ast.parse(content, filename=str(client_file))
    
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("peerhub.persistence"), f"client.py imports {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert not node.module.startswith("peerhub.persistence"), f"client.py imports from {node.module}"
