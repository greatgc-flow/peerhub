"""Unit test suite for peerhub/dispatch/artifacts.py.

Per the ratified design in docs/design/SLICE5-KICKOFF-R1.md.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import pytest

from peerhub.adapters.contract import (
    AdapterRequest,
    ArtifactSpec,
    InvocationPlan,
    SessionAction,
)
from peerhub.core.execution import TransportKind, TransportLimits
from peerhub.dispatch.artifacts import (
    MaterializationItem,
    MaterializationManifest,
    WorkspacePaths,
    generate_materialization_manifest,
    resolve_workspace_paths,
)


class DummyCompletionContract:
    @property
    def contract_id(self) -> str:
        return "test_contract_v1"


def make_test_request(scope_id: str = "ws_default") -> AdapterRequest:
    return AdapterRequest(
        request_id="req_001",
        prompt_content="Run analysis",
        prompt_reference=None,
        workspace_scope=scope_id,
        profile_id="profile_standard",
        requested_session_action=SessionAction.NONE,
        completion_contract=DummyCompletionContract(),
    )


def make_test_plan(
    argv: tuple[str, ...] = ("python", "run.py"),
    artifacts: tuple[ArtifactSpec, ...] = (),
) -> InvocationPlan:
    limits = TransportLimits(
        process_timeout_ms=5000,
        silence_timeout_ms=2000,
        max_output_bytes=1024 * 1024,
    )
    return InvocationPlan(
        argv=argv,
        cwd_reference=".",
        environment_delta={},
        transport=TransportKind.PIPE,
        stdin_payload=None,
        limits=limits,
        redacted_display="python run.py",
        artifacts=artifacts,
        session_action=SessionAction.NONE,
    )


def make_test_artifact(
    artifact_id: str = "art_001",
    placeholder: str = "{art_001}",
    content_bytes: bytes | None = b"sample content",
    content_reference: str | None = None,
    sha256_hex: str = "d08678c1...dummy",
) -> ArtifactSpec:
    # 64-character hex string for post_init validation
    valid_sha = (
        sha256_hex
        if len(sha256_hex) == 64
        else hashlib.sha256(content_bytes or b"").hexdigest()
    )
    length = len(content_bytes) if content_bytes is not None else 100
    return ArtifactSpec(
        artifact_id=artifact_id,
        placeholder=placeholder,
        content_bytes=content_bytes,
        content_reference=content_reference,
        sha256_hex=valid_sha,
        expected_length=length,
        access_mode="read_write",
        lifecycle="persistent",
    )


class TestResolveWorkspacePaths:
    def test_valid_scope_resolves_correctly(self, tmp_path: Path):
        root = tmp_path / "workspaces" / "ws_alpha"
        root.mkdir(parents=True, exist_ok=True)
        workspace_roots = {"ws_alpha": root}

        req = make_test_request(scope_id="ws_alpha")
        plan = make_test_plan()

        resolved = resolve_workspace_paths(
            req, plan, workspace_roots=workspace_roots
        )

        assert resolved.scope_id == "ws_alpha"
        assert resolved.workspace_root == root.resolve()
        assert resolved.staging_dir == (root / ".artifacts" / "staging").resolve()

    def test_custom_staging_relative_root(self, tmp_path: Path):
        root = tmp_path / "ws"
        root.mkdir(parents=True, exist_ok=True)
        workspace_roots = {"ws": root}

        req = make_test_request(scope_id="ws")
        plan = make_test_plan()

        resolved = resolve_workspace_paths(
            req,
            plan,
            workspace_roots=workspace_roots,
            artifact_staging_relative_root=Path("custom/staging"),
        )
        assert resolved.staging_dir == (root / "custom" / "staging").resolve()

    def test_unknown_scope_raises_value_error(self, tmp_path: Path):
        workspace_roots = {"known_scope": tmp_path / "ws1"}
        req = make_test_request(scope_id="unknown_scope")
        plan = make_test_plan()

        with pytest.raises(ValueError, match="Unknown workspace scope: 'unknown_scope'"):
            resolve_workspace_paths(req, plan, workspace_roots=workspace_roots)

    def test_workspace_roots_values_never_bypassed_by_caller_scope_path(
        self, tmp_path: Path
    ):
        # A caller passing a path-like string as scope_id cannot bypass lookup
        workspace_roots = {"valid_key": tmp_path / "valid"}
        req = make_test_request(scope_id=str(tmp_path / "other_dir"))
        plan = make_test_plan()

        with pytest.raises(ValueError, match="Unknown workspace scope"):
            resolve_workspace_paths(req, plan, workspace_roots=workspace_roots)

    def test_absolute_staging_root_rejected(self, tmp_path: Path):
        root = tmp_path / "ws"
        workspace_roots = {"ws": root}
        req = make_test_request(scope_id="ws")
        plan = make_test_plan()

        abs_staging = tmp_path / "outside_staging"
        with pytest.raises(ValueError, match="artifact_staging_relative_root must be a relative path"):
            resolve_workspace_paths(
                req,
                plan,
                workspace_roots=workspace_roots,
                artifact_staging_relative_root=abs_staging,
            )

    def test_traversal_in_staging_root_rejected(self, tmp_path: Path):
        root = tmp_path / "ws"
        workspace_roots = {"ws": root}
        req = make_test_request(scope_id="ws")
        plan = make_test_plan()

        with pytest.raises(ValueError, match="cannot contain traversal '..'"):
            resolve_workspace_paths(
                req,
                plan,
                workspace_roots=workspace_roots,
                artifact_staging_relative_root=Path("../outside"),
            )


class TestGenerateMaterializationManifest:
    def test_sha256_hashed_target_filenames_determinism(self, tmp_path: Path):
        workspace = WorkspacePaths(
            workspace_root=tmp_path / "root",
            staging_dir=tmp_path / "root" / ".artifacts" / "staging",
            scope_id="ws",
        )
        spec = make_test_artifact(artifact_id="data_output", placeholder="{out}")
        plan = make_test_plan(argv=("tool", "{out}"), artifacts=(spec,))

        attempt_id = "attempt_12345"
        manifest = generate_materialization_manifest(
            plan, workspace, attempt_id=attempt_id
        )

        assert len(manifest.items) == 1
        item = manifest.items[0]
        expected_hash = hashlib.sha256(b"attempt_12345:data_output").hexdigest()
        assert item.staging_filename == expected_hash
        assert item.staging_path == workspace.staging_dir / expected_hash

        # Verify determinism: repeating with same attempt_id & artifact_id yields identical hash
        manifest2 = generate_materialization_manifest(
            plan, workspace, attempt_id=attempt_id
        )
        assert manifest2.items[0].staging_filename == expected_hash

    def test_raw_ids_never_used_as_physical_path_segments(self, tmp_path: Path):
        workspace = WorkspacePaths(
            workspace_root=tmp_path / "root",
            staging_dir=tmp_path / "root" / "staging",
            scope_id="ws",
        )
        # artifact_id contains slashes and path-like segments
        spec = make_test_artifact(
            artifact_id="sub/folder/secret.txt", placeholder="{secret}"
        )
        plan = make_test_plan(argv=("cmd", "{secret}"), artifacts=(spec,))

        manifest = generate_materialization_manifest(
            plan, workspace, attempt_id="attempt_1"
        )
        item = manifest.items[0]

        # The physical staging filename is pure hex, no subdirectories
        assert "/" not in item.staging_filename
        assert "\\" not in item.staging_filename
        assert item.staging_path.parent == workspace.staging_dir
        # The logical artifact_id is retained unchanged in metadata
        assert item.artifact_id == "sub/folder/secret.txt"

    def test_source_path_traversal_rejected(self, tmp_path: Path):
        ws_root = tmp_path / "workspace"
        ws_root.mkdir(parents=True, exist_ok=True)
        workspace = WorkspacePaths(
            workspace_root=ws_root,
            staging_dir=ws_root / "staging",
            scope_id="ws",
        )

        spec = make_test_artifact(
            artifact_id="art_traversal",
            content_bytes=None,
            content_reference="../outside.txt",
        )
        plan = make_test_plan(artifacts=(spec,))

        with pytest.raises(ValueError, match="cannot contain traversal '..'"):
            generate_materialization_manifest(
                plan, workspace, attempt_id="att_1"
            )

    def test_source_path_absolute_rejected(self, tmp_path: Path):
        ws_root = tmp_path / "workspace"
        workspace = WorkspacePaths(
            workspace_root=ws_root,
            staging_dir=ws_root / "staging",
            scope_id="ws",
        )

        abs_ref = str((tmp_path / "some_file.txt").resolve())
        spec = make_test_artifact(
            artifact_id="art_abs",
            content_bytes=None,
            content_reference=abs_ref,
        )
        plan = make_test_plan(artifacts=(spec,))

        with pytest.raises(ValueError, match="Artifact source path cannot be absolute"):
            generate_materialization_manifest(
                plan, workspace, attempt_id="att_1"
            )

    def test_source_path_valid_relative_resolves(self, tmp_path: Path):
        ws_root = tmp_path / "workspace"
        ws_root.mkdir(parents=True, exist_ok=True)
        source_file = ws_root / "inputs" / "data.csv"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_bytes(b"col1,col2\n1,2\n")

        workspace = WorkspacePaths(
            workspace_root=ws_root,
            staging_dir=ws_root / "staging",
            scope_id="ws",
        )

        spec = make_test_artifact(
            artifact_id="art_valid",
            content_bytes=None,
            content_reference="inputs/data.csv",
        )
        plan = make_test_plan(artifacts=(spec,))

        manifest = generate_materialization_manifest(
            plan, workspace, attempt_id="att_1"
        )
        assert manifest.items[0].source_path == source_file.resolve()

    def test_placeholder_argv_substitution(self, tmp_path: Path):
        workspace = WorkspacePaths(
            workspace_root=tmp_path / "root",
            staging_dir=tmp_path / "root" / "staging",
            scope_id="ws",
        )
        art1 = make_test_artifact(artifact_id="art1", placeholder="{IN_FILE}")
        art2 = make_test_artifact(artifact_id="art2", placeholder="{OUT_FILE}")

        plan = make_test_plan(
            argv=("processor", "--in={IN_FILE}", "--out", "{OUT_FILE}"),
            artifacts=(art1, art2),
        )

        manifest = generate_materialization_manifest(
            plan, workspace, attempt_id="att_sub"
        )

        path1 = str(manifest.items[0].staging_path)
        path2 = str(manifest.items[1].staging_path)

        assert manifest.substituted_argv == (
            "processor",
            f"--in={path1}",
            "--out",
            path2,
        )
        assert manifest.substitutions["{IN_FILE}"] == path1
        assert manifest.substitutions["{OUT_FILE}"] == path2

    def test_duplicate_artifact_ids_rejected(self, tmp_path: Path):
        workspace = WorkspacePaths(
            workspace_root=tmp_path / "root",
            staging_dir=tmp_path / "root" / "staging",
            scope_id="ws",
        )
        art1 = make_test_artifact(artifact_id="dup_id", placeholder="{p1}")
        art2 = make_test_artifact(artifact_id="dup_id", placeholder="{p2}")

        plan = make_test_plan(argv=("cmd", "{p1}", "{p2}"))

        with pytest.raises(ValueError, match="Duplicate artifact_id found: 'dup_id'"):
            generate_materialization_manifest(
                plan, workspace, attempt_id="att_dup", artifacts=(art1, art2)
            )

    def test_duplicate_placeholders_rejected(self, tmp_path: Path):
        workspace = WorkspacePaths(
            workspace_root=tmp_path / "root",
            staging_dir=tmp_path / "root" / "staging",
            scope_id="ws",
        )
        art1 = make_test_artifact(artifact_id="id1", placeholder="{same_placeholder}")
        art2 = make_test_artifact(artifact_id="id2", placeholder="{same_placeholder}")

        plan = make_test_plan(argv=("cmd", "{same_placeholder}"))

        with pytest.raises(ValueError, match="Duplicate artifact placeholder found: '{same_placeholder}'"):
            generate_materialization_manifest(
                plan, workspace, attempt_id="att_dup_p", artifacts=(art1, art2)
            )
