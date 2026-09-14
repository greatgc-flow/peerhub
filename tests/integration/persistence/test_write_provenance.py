"""D-CTX Increment 0 (docs/design/peerhub-dctx-proposal-1-2026-09-13.md
section 5, D0/Q2/D2 closed 2026-09-14): forensic write provenance is
recorded on every governance mutation, additively, without changing
quorum/authorization or the idempotency digest."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fakes import FakeClock, FakeIdSource

from peerhub.core.protocol import CommandID
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.consensus import ConsensusService
from peerhub.governance.contract import (
    ActorBinding,
    EffectIntent,
    MutationRequest,
    PrincipalEvidence,
    WriteProvenance,
    resolve_local_os_write_provenance,
)
from peerhub.governance.mutations import mutation_payload_digest
from peerhub.persistence.sqlite import SqliteStateStore


def _broker(tmp_path: Path) -> GovernanceBroker:
    store = SqliteStateStore(tmp_path / "provenance.sqlite3", workspace_home_id="provenance-test")
    store.initialize()
    return GovernanceBroker(
        store,
        clock=FakeClock(range(1, 300)),
        ids=FakeIdSource([f"id-{i}" for i in range(1, 500)]),
    )


def test_resolve_local_os_write_provenance_resolves_a_real_principal() -> None:
    provenance = resolve_local_os_write_provenance()
    assert provenance.principal_evidence in (
        PrincipalEvidence.LOCAL_OS_ACCOUNT,
        PrincipalEvidence.UNKNOWN,
    )
    if provenance.principal_evidence is PrincipalEvidence.LOCAL_OS_ACCOUNT:
        assert provenance.actor_binding is ActorBinding.ASSERTED
        assert provenance.resolved_principal
    else:
        # OS identity resolution can genuinely fail (sandboxed/odd
        # environments); Increment 0 must degrade to UNKNOWN, never raise.
        assert provenance.actor_binding is ActorBinding.UNKNOWN


def test_real_governance_write_persists_resolved_provenance(tmp_path: Path) -> None:
    """ConsensusService.propose() is a real production MutationRequest
    call site (one of the 21 updated for Increment 0) -- this exercises
    the actual wiring, not just the WriteProvenance type in isolation."""

    broker = _broker(tmp_path)
    consensus = ConsensusService(
        broker,
        clock=FakeClock(range(1, 300)),
        ids=FakeIdSource([f"c-{i}" for i in range(1, 500)]),
    )
    consensus.propose(
        round_id="round-provenance",
        title="T",
        question="Q",
        body="B",
        proposer_id="peer-a",
        required_participants=("peer-a",),
        eligible_participants=("peer-a",),
        risk="normal",
        source_hash="sha256:test",
    )

    db_path = tmp_path / "provenance.sqlite3"
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT principal_evidence, actor_binding, resolved_principal "
            "FROM mutation_requests WHERE target_id = 'round-provenance'"
        ).fetchone()
    assert row is not None
    principal_evidence, actor_binding, resolved_principal = row
    # Whatever this CI/dev machine actually resolves to -- never LEGACY,
    # since that value is reserved for pre-migration historical rows.
    assert principal_evidence in ("LOCAL_OS_ACCOUNT", "UNKNOWN")
    assert principal_evidence != "LEGACY"
    if principal_evidence == "LOCAL_OS_ACCOUNT":
        assert actor_binding == "ASSERTED"
        assert resolved_principal
    else:
        assert actor_binding == "UNKNOWN"


def test_historical_rows_backfill_to_legacy_unknown(tmp_path: Path) -> None:
    """A row written before migration 0035 existed must read back as
    LEGACY/UNKNOWN, never silently described as asserted or verified."""

    db_path = tmp_path / "peerhub.sqlite3"
    store = SqliteStateStore(db_path, workspace_home_id="legacy-test")
    store.initialize()
    store.close()

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO mutation_requests (
                request_id, command_id, correlation_id, client_id,
                command_type, idempotency_key, actor_id, policy_revision,
                target_id, expected_revision, operation, desired_state_json,
                effect_kind, effect_payload_json, payload_digest, created_at
            ) VALUES (
                'r1', 'c1', 'corr1', 'client1', 'type1', 'idem1', 'actor1',
                '0', 'target1', 0, 'op1', '{}', 'kind1', '{}', 'digest1', 0
            )
            """
        )
        conn.commit()
        row = conn.execute(
            "SELECT principal_evidence, actor_binding, resolved_principal "
            "FROM mutation_requests WHERE request_id = 'r1'"
        ).fetchone()
    assert row == ("LEGACY", "UNKNOWN", None)


def test_write_provenance_does_not_affect_idempotency_digest() -> None:
    """D9 (holistic renewal section 8.2): provenance must not participate
    in the idempotency digest, or a legitimate retry whose OS-resolved
    principal changed between attempts would be rejected as a payload
    mismatch."""

    def _request(write_provenance: WriteProvenance) -> MutationRequest:
        return MutationRequest(
            request_id="req-1",
            command_id=CommandID("cmd-1"),
            correlation_id="corr-1",
            client_id="client-1",
            command_type="test.mutate",
            idempotency_key="idem-1",
            actor_id="actor-1",
            policy_revision="policy-r1",
            target_id="target-1",
            expected_revision=0,
            operation="SET",
            desired_state={"a": 1},
            effect_intent=EffectIntent(kind="TEST_EFFECT", payload={}),
            write_provenance=write_provenance,
        )

    unknown = _request(
        WriteProvenance(
            principal_evidence=PrincipalEvidence.UNKNOWN,
            actor_binding=ActorBinding.UNKNOWN,
        )
    )
    resolved = _request(
        WriteProvenance(
            principal_evidence=PrincipalEvidence.LOCAL_OS_ACCOUNT,
            actor_binding=ActorBinding.ASSERTED,
            resolved_principal="local-cli:someone",
        )
    )
    assert mutation_payload_digest(unknown) == mutation_payload_digest(resolved)
