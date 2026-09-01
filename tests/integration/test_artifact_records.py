from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from peerhub.application.legacy import (
    ArtifactClaimCommand,
    ArtifactFinalizeCommand,
    ArtifactStatusCommand,
    LegacyActionCall,
    LegacyTranslator,
    SubmissionMetadata,
    TranslatedCommand,
)
from peerhub.cli import main
from peerhub.client import Client
from peerhub.core.context import PathLayout, RuntimeContext
from peerhub.core.errors import (
    ArtifactClaimConflictError,
    ArtifactNotClaimedError,
)
from peerhub.core.ports import RequestContext
from peerhub.runtime import create_runtime
from tests.integration.conftest import FakeIdSource


class MutableClock:
    def __init__(self, value: int = 1000) -> None:
        self.value = value

    def now(self) -> int:
        return self.value

    def advance(self, amount: int = 1) -> None:
        self.value += amount


def _context(workspace: Path, clock: MutableClock) -> RuntimeContext:
    return RuntimeContext(
        "home-1",
        PathLayout.for_workspace(workspace),
        clock,
        FakeIdSource(),
    )


def _submission() -> SubmissionMetadata:
    return SubmissionMetadata(
        client_request_id="artifact-request",
        correlation_id="artifact-correlation",
        client_id="artifact-client",
        actor_id="cc",
        scope={},
        idempotency_key="artifact-idempotency",
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1000,
    )


def test_claim_is_sqlite_durable_and_same_owner_reclaim_preserves_claimed_at(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    context = _context(tmp_path, clock)
    with create_runtime(context, adapter_peer_kind="fake") as runtime:
        first = runtime.artifact_record_service.claim("spec.md", "cc")
        assert first.record.target_id == "artifact-record:spec.md"
        assert first.record.state["status"] == "claimed"
        assert first.record.state["claimed_at"] == 1000

        clock.advance(10)
        second = runtime.artifact_record_service.claim("spec.md", "cc")
        assert second.record.state["claimed_at"] == 1000
        assert second.record.state["owner"] == "cc"
        assert second.record.state["updated_at"] == 1000

    # Reopen the real SQLite store rather than relying on an in-memory view.
    with create_runtime(context, adapter_peer_kind="fake") as runtime:
        persisted = runtime.artifact_record_service.get_record("spec.md")
        assert persisted is not None
        assert persisted.state["claimed_at"] == 1000
        assert persisted.state["owner"] == "cc"


def test_different_owner_is_rejected_until_finalized_then_can_reclaim(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    with create_runtime(
        _context(tmp_path, clock), adapter_peer_kind="fake"
    ) as runtime:
        service = runtime.artifact_record_service
        claimed = service.claim("spec.md", "cc").record
        with pytest.raises(ArtifactClaimConflictError) as exc:
            service.claim("spec.md", "cx")
        assert exc.value.current_owner == "cc"

        final_file = tmp_path / "spec.md"
        final_file.write_bytes(b"final spec")
        service.finalize("spec.md", final_file)
        clock.advance()
        reclaimed = service.claim("spec.md", "cx").record

        assert reclaimed.state["owner"] == "cx"
        assert reclaimed.state["status"] == "claimed"
        assert reclaimed.state["claimed_at"] == claimed.state["claimed_at"]
        assert reclaimed.state["hash"] == (
            "sha256:" + hashlib.sha256(b"final spec").hexdigest()
        )
        assert "finalized_at" not in reclaimed.state
        assert "actual_path" not in reclaimed.state


def test_draft_registration_updates_claim_and_rejects_unclaimed_name(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    with create_runtime(
        _context(tmp_path, clock), adapter_peer_kind="fake"
    ) as runtime:
        service = runtime.artifact_record_service
        service.claim("spec.md", "cc")
        draft_path = str(tmp_path / "drafts" / "spec.md")
        drafted = service.register_draft(
            "spec.md",
            peer="cx",
            draft_path=draft_path,
        ).record
        assert drafted.state["status"] == "draft"
        assert drafted.state["drafts"] == {"cx": draft_path}
        assert drafted.state["external_draft_warned"] is False

        clock.advance(10)
        repeated = service.register_draft(
            "spec.md",
            peer="cx",
            draft_path=draft_path,
        ).record
        assert repeated.state == drafted.state

        with pytest.raises(ArtifactNotClaimedError):
            service.register_draft(
                "unclaimed.md",
                peer="cx",
                draft_path=draft_path,
            )


def test_finalize_hashes_real_file_and_repeat_only_advances_timestamp(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    with create_runtime(
        _context(tmp_path, clock), adapter_peer_kind="fake"
    ) as runtime:
        service = runtime.artifact_record_service
        service.claim("spec.md", "cc")
        final_file = tmp_path / "final-spec.md"
        final_file.write_bytes(b"real final bytes\n")
        expected_hash = (
            "sha256:" + hashlib.sha256(b"real final bytes\n").hexdigest()
        )

        first = service.finalize("spec.md", final_file).record
        assert first.state["status"] == "finalized"
        assert first.state["hash"] == expected_hash
        assert first.state["actual_path"] == str(final_file.resolve())
        assert first.state["finalized_at"] == 1000

        clock.advance(25)
        second = service.finalize("spec.md", final_file).record
        assert second.state["hash"] == first.state["hash"]
        assert second.state["actual_path"] == first.state["actual_path"]
        assert second.state["finalized_at"] == 1025


def test_status_queries_one_record_and_the_full_stable_list(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    with create_runtime(
        _context(tmp_path, clock), adapter_peer_kind="fake"
    ) as runtime:
        service = runtime.artifact_record_service
        service.claim("a.md", "cc")
        service.claim("b.md", "cx")

        single = service.status("b.md")
        assert single.single is True
        assert tuple(item.state["artifact"] for item in single.items) == (
            "b.md",
        )

        all_records = service.status()
        assert all_records.single is False
        assert tuple(
            item.state["artifact"] for item in all_records.items
        ) == ("a.md", "b.md")
        assert service.status("missing.md").items == ()


def test_all_three_legacy_actions_translate_and_execute(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    with create_runtime(
        _context(tmp_path, clock), adapter_peer_kind="fake"
    ) as runtime:
        translator = LegacyTranslator()
        client = Client(
            runtime.application_api,
            caller=RequestContext(
                principal="cc",
                client_id="artifact-client",
            ),
        )
        final_file = tmp_path / "legacy-final.md"
        final_file.write_text("legacy final", encoding="utf-8")

        claim = translator.translate(
            LegacyActionCall(
                action="artifact-claim",
                arguments={"name": "legacy.md", "peer": "cc"},
            ),
            _submission(),
        )
        assert isinstance(claim, TranslatedCommand)
        assert isinstance(claim.command, ArtifactClaimCommand)
        assert client.submit(claim.command).ok

        draft = translator.translate(
            LegacyActionCall(
                action="artifact-status",
                arguments={
                    "name": "legacy.md",
                    "agent": "cx",
                    "draft_path": str(tmp_path / "legacy-draft.md"),
                },
            ),
            _submission(),
        )
        assert isinstance(draft, TranslatedCommand)
        assert isinstance(draft.command, ArtifactStatusCommand)
        draft_outcome = client.submit(draft.command)
        assert draft_outcome.ok
        assert draft_outcome.state == "ADMITTED"

        status = translator.translate(
            LegacyActionCall(
                action="artifact-status",
                arguments={"name": "legacy.md"},
            ),
            _submission(),
        )
        assert isinstance(status, TranslatedCommand)
        assert isinstance(status.command, ArtifactStatusCommand)
        status_outcome = client.submit(status.command)
        assert status_outcome.ok
        assert status_outcome.state == "COMPLETED"

        finalize = translator.translate(
            LegacyActionCall(
                action="artifact-finalize",
                arguments={
                    "name": "legacy.md",
                    "file": str(final_file),
                },
            ),
            _submission(),
        )
        assert isinstance(finalize, TranslatedCommand)
        assert isinstance(finalize.command, ArtifactFinalizeCommand)
        assert client.submit(finalize.command).ok

        record = runtime.artifact_record_service.get_record("legacy.md")
        assert record is not None
        assert record.state["status"] == "finalized"


def test_cli_executes_claim_status_draft_and_finalize(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = str(tmp_path)
    final_file = tmp_path / "cli-final.md"
    final_file.write_text("CLI final", encoding="utf-8")
    draft_file = tmp_path / "cli-draft.md"

    assert main([
        "artifact", "claim", "--workspace", workspace,
        "--name", "cli.md", "--peer", "cc",
    ]) == 0
    assert "[HUB] ARTIFACT-CLAIM cli.md | owner=cc" in capsys.readouterr().out

    assert main([
        "artifact", "status", "--workspace", workspace,
        "--name", "cli.md", "--peer", "cx",
        "--draft-path", str(draft_file),
    ]) == 0
    assert "[HUB] ARTIFACT-DRAFT cli.md | peer=cx" in capsys.readouterr().out

    assert main([
        "artifact", "status", "--workspace", workspace,
        "--name", "cli.md", "--json",
    ]) == 0
    assert '"status": "draft"' in capsys.readouterr().out

    assert main([
        "artifact", "finalize", "--workspace", workspace,
        "--name", "cli.md", "--file", str(final_file),
    ]) == 0
    output = capsys.readouterr().out
    assert "[HUB] ARTIFACT-FINALIZE cli.md | hash=sha256:" in output
