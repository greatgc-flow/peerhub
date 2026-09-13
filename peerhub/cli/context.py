import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Literal


@dataclass
class WorkspaceResolution:
    root: Path
    identity: Optional[str]
    selection_source: Literal["explicit", "env", "discovered", "cwd", "context"]
    is_initialized: bool
    project_boundary: Optional[Path]
    state_description: str  # e.g., "valid", "missing", "config-only", "corrupt", "unsupported"


def _is_git_boundary(path: Path) -> bool:
    """Both .git directories and .git files used by worktrees/submodules count."""
    git_marker = path / ".git"
    return git_marker.exists()


def _check_store_state(peerhub_dir: Path) -> Tuple[bool, Optional[str], str]:
    """Returns (is_initialized, identity, state_description)"""
    db_path = peerhub_dir / "peerhub.sqlite3"
    if not db_path.exists():
        if (peerhub_dir / "config").exists():
            return False, None, "config-only"
        return False, None, "missing"
    
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = conn.cursor()
        
        # Check if the tables exist to see if it's supported
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='workspace_identity'")
        has_identity_table = cursor.fetchone() is not None
        
        identity = None
        if has_identity_table:
            cursor.execute("SELECT id FROM workspace_identity LIMIT 1")
            row = cursor.fetchone()
            if row:
                identity = row[0]
                
        # If it has some tables but not what we expect, we still consider it valid for this layer
        conn.close()
        return True, identity, "valid"
    except sqlite3.OperationalError:
        # More specific subclass of DatabaseError -- must be checked first,
        # or it is silently swallowed by the DatabaseError clause below and
        # always reported "corrupt" instead of "unsupported".
        return True, None, "unsupported"
    except sqlite3.DatabaseError:
        return False, None, "corrupt"


def resolve_workspace(explicit_workspace: Optional[str] = None, cwd_override: Optional[Path] = None) -> WorkspaceResolution:
    """
    Resolves the workspace root and its initialization state according to the purity contract (Section 4.1).
    """
    cwd = cwd_override or Path.cwd()
    
    # 2. Selected context handling (D-CTX)
    context_file = os.environ.get("PEERHUB_CONTEXT_FILE")
    if context_file:
        # Invalid/unverified selected context must fail closed, not downgrade.
        # Stub for follow-up: Context verification logic to be implemented here.
        raise RuntimeError("Selected context via PEERHUB_CONTEXT_FILE is present but context verification is not yet implemented (D-CTX held). Fails closed.")

    # 3a. Explicit --workspace PATH
    if explicit_workspace is not None:
        root = Path(explicit_workspace).resolve()
        is_init, identity, state = _check_store_state(root / ".peerhub")
        return WorkspaceResolution(
            root=root,
            identity=identity,
            selection_source="explicit",
            is_initialized=is_init,
            project_boundary=root if _is_git_boundary(root) else None,
            state_description=state
        )
        
    # 3b. PEERHUB_WORKSPACE env var
    env_workspace = os.environ.get("PEERHUB_WORKSPACE")
    if env_workspace:
        env_path = Path(env_workspace)
        if not env_path.is_absolute():
            raise ValueError(f"PEERHUB_WORKSPACE must be an absolute path, got: {env_workspace}")
        root = env_path.resolve()
        is_init, identity, state = _check_store_state(root / ".peerhub")
        return WorkspaceResolution(
            root=root,
            identity=identity,
            selection_source="env",
            is_initialized=is_init,
            project_boundary=root if _is_git_boundary(root) else None,
            state_description=state
        )
        
    # 3c, 3d, 3e. Discover walking up
    current = cwd
    first_git_boundary = None
    
    while True:
        if _is_git_boundary(current) and first_git_boundary is None:
            first_git_boundary = current
            
        peerhub_dir = current / ".peerhub"
        if peerhub_dir.exists():
            is_init, identity, state = _check_store_state(peerhub_dir)
            return WorkspaceResolution(
                root=current,
                identity=identity,
                selection_source="discovered",
                is_initialized=is_init,
                project_boundary=first_git_boundary or current,
                state_description=state
            )
            
        if first_git_boundary and current == first_git_boundary:
            break
            
        parent = current.parent
        if parent == current:
            break
        current = parent
        
    if first_git_boundary:
        is_init, identity, state = _check_store_state(first_git_boundary / ".peerhub")
        return WorkspaceResolution(
            root=first_git_boundary,
            identity=identity,
            selection_source="discovered",
            is_initialized=is_init,
            project_boundary=first_git_boundary,
            state_description=state
        )
        
    is_init, identity, state = _check_store_state(cwd / ".peerhub")
    return WorkspaceResolution(
        root=cwd,
        identity=identity,
        selection_source="cwd",
        is_initialized=is_init,
        project_boundary=None,
        state_description=state
    )
