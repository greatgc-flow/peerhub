"""Which database file a CLI call uses, and why.

Order: PEERHUB_DB (when allowed) > explicit --db > discovered workspace store > the default path.
Discovery looks at the current directory, `<cwd>/peerhub`, then every parent. Inside one workspace root `.peerhub/core.db` is the
current name and `.peerhub/m1.db` the legacy one; having BOTH is ambiguous (one would silently hide the other's data) and is refused.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

DEFAULT_DB_PATH = ".peerhub/core.db"
LEGACY_DB_PATH = ".peerhub/m1.db"  # existing durable data: discovered and reused, only renamed by `peerhub store rename-legacy`


class StoreAmbiguousError(Exception):
    """Both the current and the legacy store file exist in one workspace; the caller must choose explicitly."""

    def __init__(self, current: str, legacy: str) -> None:
        super().__init__(f"both {current} and {legacy} exist; choose one with --db PATH or PEERHUB_DB (the other stays untouched)")
        self.current, self.legacy = current, legacy


@dataclass(frozen=True)
class StoreSelection:
    path: str
    source: str  # env | explicit | discovered | workspace-default | default

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "source": self.source}


def select_store(configured_path: str, *, use_env: bool = True, discover: bool = True, workspace_root: Path | None = None,
                 env: Mapping[str, str] | None = None, cwd: Path | None = None) -> StoreSelection:
    env = os.environ if env is None else env
    if use_env and "PEERHUB_DB" in env:
        return StoreSelection(env["PEERHUB_DB"], "env")
    if configured_path != DEFAULT_DB_PATH or not discover:
        return StoreSelection(configured_path, "explicit" if configured_path != DEFAULT_DB_PATH else "default")
    if workspace_root is not None:
        roots = [workspace_root.resolve()]
    else:
        current = (cwd or Path.cwd()).resolve()
        roots = [current, current / "peerhub", *current.parents]
    for root in roots:
        current_db, legacy_db = root / DEFAULT_DB_PATH, root / LEGACY_DB_PATH
        if current_db.is_file() and legacy_db.is_file():
            raise StoreAmbiguousError(str(current_db), str(legacy_db))
        for candidate in (current_db, legacy_db):
            if candidate.is_file():
                return StoreSelection(str(candidate), "discovered")
    if workspace_root is not None:
        return StoreSelection(str(workspace_root / DEFAULT_DB_PATH), "workspace-default")
    return StoreSelection(configured_path, "default")
