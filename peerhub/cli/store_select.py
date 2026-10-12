"""Which database file a CLI call uses, and why.

Order: PEERHUB_DB (when allowed) > explicit --db > a discovered workspace store > the default path.
Discovery looks for `.peerhub/core.db` in the current directory, `<cwd>/peerhub`, then every parent.
"""

from __future__ import annotations

import os
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

DEFAULT_DB_PATH = ".peerhub/core.db"


def report_missing_store(path: str | Path, *, json_output: bool = False) -> bool:
    """Refuse absent stores before opening storage or invoking any provider."""
    if Path(path).is_file():
        return False
    message = f"database not found: {path}"
    print(message, file=sys.stderr)
    print(f"Create one explicitly: peerhub --db {path} peer register --peer <id> --name <name>", file=sys.stderr)
    if json_output:
        print(json.dumps({"status": "UNAVAILABLE", "error": message}, ensure_ascii=True))
    return True


@dataclass(frozen=True)
class StoreSelection:
    path: str
    source: str  # env | explicit | discovered | workspace-default | default


def select_store(configured_path: str, *, use_env: bool = True, discover: bool = True, workspace_root: Path | None = None,
                 env: Mapping[str, str] | None = None, cwd: Path | None = None) -> StoreSelection:
    env = os.environ if env is None else env
    if use_env and "PEERHUB_DB" in env:
        return StoreSelection(env["PEERHUB_DB"], "env")
    if configured_path != DEFAULT_DB_PATH or not discover:
        return StoreSelection(configured_path, "explicit")
    if workspace_root is not None:
        roots = [workspace_root.resolve()]
    else:
        current = (cwd or Path.cwd()).resolve()
        roots = [current, current / "peerhub", *current.parents]
    for root in roots:
        candidate = root / DEFAULT_DB_PATH
        if candidate.is_file():
            return StoreSelection(str(candidate), "discovered")
    if workspace_root is not None:
        return StoreSelection(str(workspace_root / DEFAULT_DB_PATH), "workspace-default")
    return StoreSelection(configured_path, "default")
