"""Side-effect-free resolution of PeerHub configuration and temp paths."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class ConfigPathSource(StrEnum):
    """Why a path was selected."""

    EXPLICIT = "explicit"
    ENV = "env"
    DEFAULT = "default"
    WORKSPACE = "workspace"


@dataclass(frozen=True, slots=True)
class ResolvedConfigPath:
    """A resolved path together with its selection source."""

    path: Path
    source: ConfigPathSource

    def as_dict(self) -> dict[str, str]:
        """Return a JSON-serializable representation."""

        return {"path": str(self.path), "source": self.source.value}


@dataclass(frozen=True, slots=True)
class ResolvedConfigPaths:
    """All stable roots and config-family paths for one workspace."""

    global_config_home: ResolvedConfigPath
    workspace_home: ResolvedConfigPath
    workspace_config_home: ResolvedConfigPath
    workspace_temp: ResolvedConfigPath
    models_toml: ResolvedConfigPath
    ask_toml: ResolvedConfigPath
    arbiter_json: ResolvedConfigPath
    proposals_json: ResolvedConfigPath
    legacy_arbiter_json: ResolvedConfigPath
    legacy_proposals_json: ResolvedConfigPath

    def as_dict(self) -> dict[str, dict[str, str]]:
        """Return a stable machine-readable path map."""

        return {
            name: value.as_dict()
            for name, value in (
                ("global_config_home", self.global_config_home),
                ("workspace_home", self.workspace_home),
                ("workspace_config_home", self.workspace_config_home),
                ("workspace_temp", self.workspace_temp),
                ("models_toml", self.models_toml),
                ("ask_toml", self.ask_toml),
                ("arbiter_json", self.arbiter_json),
                ("proposals_json", self.proposals_json),
                ("legacy_arbiter_json", self.legacy_arbiter_json),
                ("legacy_proposals_json", self.legacy_proposals_json),
            )
        }


def resolve_global_config_home(
    *,
    explicit: Path | None = None,
    environ: Mapping[str, str] | None = None,
    user_home: Path | None = None,
) -> ResolvedConfigPath:
    """Resolve the global config root using explicit > env > default."""

    if explicit is not None:
        return ResolvedConfigPath(explicit, ConfigPathSource.EXPLICIT)
    environment = os.environ if environ is None else environ
    override = environment.get("PEERHUB_CONFIG_HOME")
    if override:
        return ResolvedConfigPath(Path(override), ConfigPathSource.ENV)
    home = Path.home() if user_home is None else user_home
    return ResolvedConfigPath(
        home / ".peerhub" / "config",
        ConfigPathSource.DEFAULT,
    )


def resolve_workspace_config_home(workspace_root: Path) -> ResolvedConfigPath:
    """Resolve the workspace config root without creating it."""

    return ResolvedConfigPath(
        workspace_root / ".peerhub" / "config",
        ConfigPathSource.WORKSPACE,
    )


def resolve_workspace_temp(
    workspace_root: Path,
    *,
    temp_root: Path | None = None,
) -> ResolvedConfigPath:
    """Resolve the deterministic OS-temp namespace for one workspace."""

    root = Path(tempfile.gettempdir()) if temp_root is None else temp_root
    normalized = os.path.normcase(
        os.path.normpath(str(workspace_root.resolve(strict=False)))
    )
    workspace_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return ResolvedConfigPath(
        root / "peerhub" / "workspaces" / workspace_hash,
        ConfigPathSource.DEFAULT,
    )


def _child(root: ResolvedConfigPath, name: str) -> ResolvedConfigPath:
    return ResolvedConfigPath(root.path / name, root.source)


def resolve_config_paths(
    *,
    workspace_root: Path,
    explicit_global_config_home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    user_home: Path | None = None,
    temp_root: Path | None = None,
) -> ResolvedConfigPaths:
    """Resolve every config-family path without reading or creating files."""

    global_home = resolve_global_config_home(
        explicit=explicit_global_config_home,
        environ=environ,
        user_home=user_home,
    )
    workspace_home = ResolvedConfigPath(
        workspace_root / ".peerhub",
        ConfigPathSource.WORKSPACE,
    )
    workspace_config_home = resolve_workspace_config_home(workspace_root)
    return ResolvedConfigPaths(
        global_config_home=global_home,
        workspace_home=workspace_home,
        workspace_config_home=workspace_config_home,
        workspace_temp=resolve_workspace_temp(
            workspace_root,
            temp_root=temp_root,
        ),
        models_toml=_child(global_home, "models.toml"),
        ask_toml=_child(global_home, "ask.toml"),
        arbiter_json=_child(workspace_config_home, "arbiter.json"),
        proposals_json=_child(workspace_config_home, "proposals.json"),
        # Items 8-9 own migration/fallback semantics. Until then, the current
        # readers remain byte-compatible while still using this one resolver.
        legacy_arbiter_json=_child(workspace_home, "arbiter.json"),
        legacy_proposals_json=_child(workspace_home, "proposals.json"),
    )


def resolve_compat_config_path(
    *, new_path: Path, legacy_path: Path, label: str
) -> Path | None:
    """Item 8's compatibility reader: exactly one of ``new_path``/
    ``legacy_path`` may exist. Returns whichever does (preferring ``new_path``
    when only it exists; ``legacy_path`` when only it exists -- an
    unmigrated legacy file must keep working with no semantic change).
    Returns ``None`` if neither exists (caller applies its own default).
    Raises ``ValueError`` if BOTH exist -- never a silent pick; the
    message names ``peerhub config migrate`` as the resolution path.
    """

    new_exists = new_path.is_file()
    legacy_exists = legacy_path.is_file()
    if new_exists and legacy_exists:
        raise ValueError(
            f"both {new_path} and {legacy_path} exist for {label} -- "
            f"refusing to guess which one wins. Run `peerhub config "
            f"migrate` to resolve this (it validates and atomically moves "
            f"the legacy file, or reports the same conflict if the two "
            f"disagree)."
        )
    if new_exists:
        return new_path
    if legacy_exists:
        return legacy_path
    return None
