"""Which database file a CLI call uses, and why.

Order: explicit --db > PEERHUB_DB (when allowed) > a discovered workspace store > the default path.
Discovery looks for `.peerhub/core.db` in the current directory, then every parent.
On a miss, Git's main worktree and its parents are searched (workspace_root:
main root only). An implicit default is only a diagnostic path; CLI store
consumers must refuse to create it.
"""

from __future__ import annotations

import os
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

DEFAULT_DB_PATH = ".peerhub/core.db"


def find_legacy_store(cwd: Path | None = None) -> Path | None:
    """Detect an existing legacy store under <cwd>/peerhub if present."""
    base = (cwd or Path.cwd()).resolve()
    for candidate in (
        base / "peerhub" / "core.db",
        base / "peerhub" / DEFAULT_DB_PATH,
    ):
        if candidate.is_file():
            return candidate.resolve()
    return None


def report_missing_store(
    path: str | Path,
    *,
    json_output: bool = False,
    cwd: Path | None = None,
    selected: bool | None = None,
    source: str | None = None,
) -> bool:
    """Refuse absent stores before opening storage or invoking any provider."""
    if Path(path).is_file():
        return False
    path = Path(path).resolve()
    message = f"database not found: {path}"
    is_selected = selected if selected is not None else (path != Path(DEFAULT_DB_PATH).resolve())
    resolved_source = source or ("explicit" if is_selected else "default")
    hint = ('run from the main checkout or select the intended store with --db PATH / PEERHUB_DB; '
            f'to create a store: peerhub --db "{path}" peer register --peer local --name local')
    legacy = find_legacy_store(cwd) if not is_selected else None
    legacy_note = (f'; Ignored legacy store: {legacy} (no longer auto-discovered). Use --db "{legacy}" to keep using it; stores of other schema versions are refused as usual.' if legacy else "")
    print(f"[store_missing] UNAVAILABLE: {message} (source: {resolved_source}){legacy_note}; next: {hint}", file=sys.stderr)
    if json_output:
        print(json.dumps({"status": "UNAVAILABLE", "error": message}, ensure_ascii=True))
    return True



@dataclass(frozen=True)
class StoreSelection:
    path: str
    source: str  # env | explicit | discovered | default


def _main_worktree(base: Path) -> Path | None:
    """Ask the innermost Git repository for a verifiable main worktree."""
    git_env = {key: value for key, value in os.environ.items()
               if key.upper() not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"}}
    try:
        common = subprocess.run(
            ["git", "-C", str(base), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            check=True, capture_output=True, timeout=2, env=git_env,
        ).stdout.decode("utf-8").rstrip("\r\n")
        if not common or not Path(common).is_absolute() or not Path(common).is_dir():
            return None
        listing = subprocess.run(
            ["git", "--git-dir", common, "worktree", "list", "--porcelain", "-z"],
            check=True, capture_output=True, timeout=2, env=git_env,
        ).stdout.decode("utf-8")
        # Git lists the main worktree first. NUL framing preserves spaces and Unicode.
        fields = listing.split("\0\0", 1)[0].split("\0")
        if not fields[0].startswith("worktree ") or "bare" in fields:
            return None
        root = Path(fields[0][len("worktree "):])
        if not root.is_absolute() or not root.is_dir():
            return None
        # With --separate-git-dir Git may report the metadata directory itself.
        # Never search upward from that directory as though it were a checkout.
        marker = root / ".git"
        if marker.is_file():
            pointer = marker.read_text(encoding="utf-8").strip()
            if not pointer.startswith("gitdir: "):
                return None
            marker = root / pointer[len("gitdir: "):]
        return root.resolve() if marker.resolve() == Path(common).resolve() else None
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        return None


def select_store(configured_path: str, *, use_env: bool = True, discover: bool = True, workspace_root: Path | None = None,
                 env: Mapping[str, str] | None = None, cwd: Path | None = None) -> StoreSelection:
    env = os.environ if env is None else env
    base = (cwd or Path.cwd()).resolve()
    if configured_path != DEFAULT_DB_PATH or not discover:
        return StoreSelection(str((base / configured_path).resolve()), "explicit")
    if use_env and "PEERHUB_DB" in env:
        return StoreSelection(str((base / env["PEERHUB_DB"]).resolve()), "env")
    if workspace_root is not None:
        roots = [workspace_root.resolve()]
    else:
        current = (cwd or Path.cwd()).resolve()
        roots = [current, *current.parents]
    for root in roots:
        candidate = root / DEFAULT_DB_PATH
        if candidate.is_file():
            return StoreSelection(str(candidate), "discovered")
    main_root = _main_worktree(workspace_root.resolve() if workspace_root is not None else base)
    if main_root is not None and main_root not in roots:
        fallback_roots = [main_root] if workspace_root is not None else [main_root, *main_root.parents]
        for root in fallback_roots:
            candidate = root / DEFAULT_DB_PATH
            if candidate.is_file():
                return StoreSelection(str(candidate), "discovered")
    if workspace_root is not None:
        return StoreSelection(str((workspace_root / DEFAULT_DB_PATH).resolve()), "default")
    return StoreSelection(str(base / configured_path), "default")
