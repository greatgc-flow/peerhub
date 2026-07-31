"""Immutable runtime context and low-level dependency protocols."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """A source of deterministic logical or UTC timestamps."""

    def now(self) -> int:
        """Return the current nonnegative timestamp."""

        ...


@runtime_checkable
class IdSource(Protocol):
    """A source of unique identifiers."""

    def new_id(self, namespace: str) -> str:
        """Return a new identifier for the supplied namespace."""

        ...


@dataclass(frozen=True)
class PathLayout:
    """Filesystem paths owned by one resolved workspace identity."""

    workspace_root: Path
    workspace_home: Path
    database_path: Path

    @classmethod
    def for_workspace(cls, workspace_root: Path) -> PathLayout:
        """Return the conventional local path layout for a workspace."""

        workspace_home = workspace_root / ".peerhub"
        return cls(
            workspace_root=workspace_root,
            workspace_home=workspace_home,
            database_path=workspace_home / "peerhub.sqlite3",
        )

    def __post_init__(self) -> None:
        if self.database_path == self.workspace_home:
            raise ValueError(
                "database_path must name a file inside workspace_home"
            )


@dataclass(frozen=True)
class RuntimeContext:
    """Immutable low-level dependencies used to compose a runtime."""

    workspace_home_id: str
    paths: PathLayout
    clock: Clock
    ids: IdSource

    def __post_init__(self) -> None:
        if not self.workspace_home_id.strip():
            raise ValueError("workspace_home_id must be non-empty")
