"""Artifact path resolution and materialization manifest generation for Slice 5.

Per the ratified design in docs/design/SLICE5-KICKOFF-R1.md
("artifacts.py/completion.py contract RATIFIED (2026-08-03, ag+cx unanimous)").

Enforces safety mechanisms:
1. workspace_roots is a trusted Mapping[str, Path] lookup; workspace_scope is
   treated purely as an opaque key into it, resolved only inside
   resolve_workspace_paths. Unknown scopes raise ValueError.
2. Staging target filenames are SHA-256 digests of stable identity
   (e.g., attempt_id:artifact_id), never raw externally-controlled IDs used
   as physical path segments.
3. Artifact source paths are validated to reject absolute paths, traversal
   ('..'), and resolution outside the workspace root.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
from pathlib import Path
from types import MappingProxyType

from peerhub.adapters.contract import (
    AdapterRequest,
    ArtifactSpec,
    InvocationPlan,
)


@dataclass(frozen=True)
class WorkspacePaths:
    """Resolved workspace root, staging directory, and scope ID."""

    workspace_root: Path
    staging_dir: Path
    scope_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.workspace_root, Path):
            raise ValueError("workspace_root must be a Path")
        if not self.workspace_root.is_absolute():
            raise ValueError("workspace_root must be an absolute Path")
        if not isinstance(self.staging_dir, Path):
            raise ValueError("staging_dir must be a Path")
        if not isinstance(self.scope_id, str) or not self.scope_id:
            raise ValueError("scope_id must be a non-empty string")


def resolve_workspace_paths(
    request: AdapterRequest,
    plan: InvocationPlan,
    *,
    workspace_roots: Mapping[str, Path],
    artifact_staging_relative_root: Path = Path(".artifacts/staging"),
) -> WorkspacePaths:
    """Resolve trusted workspace paths for a request and plan.

    Looks up request.workspace_scope in the trusted workspace_roots mapping.
    Raises ValueError if scope is unknown or staging path attempts traversal.
    """
    if not isinstance(request, AdapterRequest):
        raise ValueError("request must be an AdapterRequest")
    if not isinstance(plan, InvocationPlan):
        raise ValueError("plan must be an InvocationPlan")
    if not isinstance(workspace_roots, Mapping):
        raise ValueError("workspace_roots must be a Mapping")

    scope_id = request.workspace_scope
    if scope_id not in workspace_roots:
        raise ValueError(f"Unknown workspace scope: {scope_id!r}")

    raw_root = workspace_roots[scope_id]
    if not isinstance(raw_root, (str, Path)):
        raise ValueError(
            f"workspace_roots[{scope_id!r}] must be a Path or str"
        )

    workspace_root = Path(raw_root).resolve()

    staging_rel = Path(artifact_staging_relative_root)
    if staging_rel.is_absolute():
        raise ValueError(
            f"artifact_staging_relative_root must be a relative path, got: {staging_rel}"
        )
    if ".." in staging_rel.parts:
        raise ValueError(
            f"artifact_staging_relative_root cannot contain traversal '..': {staging_rel}"
        )

    staging_dir = (workspace_root / staging_rel).resolve()
    try:
        staging_dir.relative_to(workspace_root)
    except ValueError:
        raise ValueError(
            f"staging_dir {staging_dir} resolves outside workspace_root {workspace_root}"
        )

    return WorkspacePaths(
        workspace_root=workspace_root,
        staging_dir=staging_dir,
        scope_id=scope_id,
    )


@dataclass(frozen=True)
class MaterializationItem:
    """Materialization item for a single artifact."""

    artifact_id: str
    placeholder: str
    staging_filename: str
    staging_path: Path
    source_path: Path | None
    content_bytes: bytes | None
    sha256_hex: str
    expected_length: int
    access_mode: str
    lifecycle: str

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_id, str) or not self.artifact_id:
            raise ValueError("artifact_id must be a non-empty string")
        if not isinstance(self.placeholder, str) or not self.placeholder:
            raise ValueError("placeholder must be a non-empty string")
        if (
            not isinstance(self.staging_filename, str)
            or not self.staging_filename
        ):
            raise ValueError("staging_filename must be a non-empty string")
        if not isinstance(self.staging_path, Path):
            raise ValueError("staging_path must be a Path")
        if self.source_path is not None and not isinstance(
            self.source_path, Path
        ):
            raise ValueError("source_path must be None or a Path")
        if self.content_bytes is not None and type(self.content_bytes) is not bytes:
            raise ValueError("content_bytes must be None or bytes")
        if not isinstance(self.sha256_hex, str):
            raise ValueError("sha256_hex must be a string")
        if type(self.expected_length) is not int or self.expected_length < 0:
            raise ValueError("expected_length must be a nonnegative integer")
        if not isinstance(self.access_mode, str) or not self.access_mode:
            raise ValueError("access_mode must be a non-empty string")
        if not isinstance(self.lifecycle, str) or not self.lifecycle:
            raise ValueError("lifecycle must be a non-empty string")


@dataclass(frozen=True)
class MaterializationManifest:
    """Immutable manifest detailing artifact materialization and substituted argv."""

    attempt_id: str
    workspace: WorkspacePaths
    items: tuple[MaterializationItem, ...]
    substituted_argv: tuple[str, ...]
    substitutions: Mapping[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.attempt_id, str) or not self.attempt_id:
            raise ValueError("attempt_id must be a non-empty string")
        if not isinstance(self.workspace, WorkspacePaths):
            raise ValueError("workspace must be a WorkspacePaths instance")

        items = tuple(self.items)
        for item in items:
            if not isinstance(item, MaterializationItem):
                raise ValueError("every item must be a MaterializationItem")
        object.__setattr__(self, "items", items)

        argv = tuple(self.substituted_argv)
        for token in argv:
            if not isinstance(token, str):
                raise ValueError(
                    "every substituted_argv token must be a string"
                )
        object.__setattr__(self, "substituted_argv", argv)


def _validate_source_path(ref: str, root: Path) -> Path:
    """Validate artifact source path against absolute paths and traversal."""
    ref_path = Path(ref)
    if ref_path.is_absolute():
        raise ValueError(
            f"Artifact source path cannot be absolute: {ref!r}"
        )
    if ".." in ref_path.parts:
        raise ValueError(
            f"Artifact source path cannot contain traversal '..': {ref!r}"
        )

    resolved = (root / ref_path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError(
            f"Artifact source path resolves outside workspace root: {ref!r}"
        )
    return resolved


def generate_materialization_manifest(
    plan: InvocationPlan,
    workspace: WorkspacePaths,
    *,
    attempt_id: str,
    artifacts: Sequence[ArtifactSpec] = (),
) -> MaterializationManifest:
    """Generate a MaterializationManifest for an invocation plan and workspace."""
    if not isinstance(plan, InvocationPlan):
        raise ValueError("plan must be an InvocationPlan")
    if not isinstance(workspace, WorkspacePaths):
        raise ValueError("workspace must be a WorkspacePaths instance")
    if not isinstance(attempt_id, str) or not attempt_id:
        raise ValueError("attempt_id must be a non-empty string")

    raw_specs: list[ArtifactSpec] = list(plan.artifacts)
    for a in artifacts:
        if a not in raw_specs:
            raw_specs.append(a)

    seen_ids: set[str] = set()
    seen_placeholders: set[str] = set()
    items: list[MaterializationItem] = []
    substitutions: dict[str, str] = {}

    for spec in raw_specs:
        if not isinstance(spec, ArtifactSpec):
            raise ValueError("every artifact must be an ArtifactSpec")

        if spec.artifact_id in seen_ids:
            raise ValueError(
                f"Duplicate artifact_id found: {spec.artifact_id!r}"
            )
        seen_ids.add(spec.artifact_id)

        if spec.placeholder in seen_placeholders:
            raise ValueError(
                f"Duplicate artifact placeholder found: {spec.placeholder!r}"
            )
        seen_placeholders.add(spec.placeholder)

        # Safety Mechanism 2: SHA-256-hashed physical staging filename
        stable_identity = f"{attempt_id}:{spec.artifact_id}"
        staging_filename = hashlib.sha256(
            stable_identity.encode("utf-8")
        ).hexdigest()
        staging_path = workspace.staging_dir / staging_filename

        # Safety Mechanism 3: Validate source path if content_reference is present
        if spec.content_reference is not None:
            source_path = _validate_source_path(
                spec.content_reference, workspace.workspace_root
            )
        else:
            source_path = None

        item = MaterializationItem(
            artifact_id=spec.artifact_id,
            placeholder=spec.placeholder,
            staging_filename=staging_filename,
            staging_path=staging_path,
            source_path=source_path,
            content_bytes=spec.content_bytes,
            sha256_hex=spec.sha256_hex,
            expected_length=spec.expected_length,
            access_mode=spec.access_mode,
            lifecycle=spec.lifecycle,
        )
        items.append(item)
        substitutions[spec.placeholder] = str(staging_path)

    # Perform placeholder substitution on plan.argv
    substituted_argv: list[str] = []
    for token in plan.argv:
        new_token = token
        for item in items:
            new_token = new_token.replace(item.placeholder, str(item.staging_path))
        substituted_argv.append(new_token)

    return MaterializationManifest(
        attempt_id=attempt_id,
        workspace=workspace,
        items=tuple(items),
        substituted_argv=tuple(substituted_argv),
        substitutions=MappingProxyType(substitutions),
    )
