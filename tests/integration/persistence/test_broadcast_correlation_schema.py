"""Empirical schema and admission-replay proofs for broadcast correlation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from peerhub.core.errors import (
    DuplicateClientRequestError,
    IdempotencyPayloadMismatchError,
)
from peerhub.core.identity import AuthenticatedSubject
from peerhub.core.protocol import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    CommandEnvelope,
)
from peerhub.dispatch.capability import CapabilityTier
from peerhub.dispatch.contract import (
    CompletionContract,
    CompletionContractKind,
    RequestSnapshot,
)
from peerhub.dispatch.service import DispatchService
from peerhub.persistence.sqlite import SqliteStateStore
from tests.fakes import DeterministicClock, SequentialIdSource


_BROADCAST_CLIENT_ID = "peerhub-broadcast"
_SUBJECT = AuthenticatedSubject("principal-broadcast", "test")


def _store(database_path: Path) -> SqliteStateStore:
    store = SqliteStateStore(
        database_path,
        workspace_home_id="workspace-broadcast-correlation",
    )
    store.initialize()
    return store


@contextmanager
def _connect(database_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _insert_round(
    connection: sqlite3.Connection,
    round_id: str,
    *,
    wave_of: str | None = None,
    requested_targets: int = 1,
    prompt_digest: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO broadcast_rounds (
            broadcast_round_id,
            wave_of,
            prompt_digest,
            requested_targets,
            deadline_at,
            status,
            disposition,
            created_at,
            closed_at
        ) VALUES (?, ?, ?, ?, NULL, 'open', NULL, 100, NULL)
        """,
        (
            round_id,
            wave_of,
            prompt_digest
            or hashlib.sha256(round_id.encode("utf-8")).hexdigest(),
            requested_targets,
        ),
    )


def _multi_insert_rounds(
    connection: sqlite3.Connection,
    first: tuple[str, str | None],
    second: tuple[str, str | None],
) -> None:
    connection.execute(
        """
        INSERT INTO broadcast_rounds (
            broadcast_round_id,
            wave_of,
            prompt_digest,
            requested_targets,
            deadline_at,
            status,
            disposition,
            created_at,
            closed_at
        ) VALUES
            (?, ?, ?, 1, NULL, 'open', NULL, 100, NULL),
            (?, ?, ?, 1, NULL, 'open', NULL, 100, NULL)
        """,
        (
            first[0],
            first[1],
            hashlib.sha256(first[0].encode("utf-8")).hexdigest(),
            second[0],
            second[1],
            hashlib.sha256(second[0].encode("utf-8")).hexdigest(),
        ),
    )


def _canonical_leg_target(leg_target: str) -> str:
    value = leg_target.strip().lower()
    if not value:
        raise ValueError("leg_target must be non-empty")
    return value


def _domain_hash(domain: str, round_id: str, leg_target: str) -> str:
    payload = json.dumps(
        {
            "broadcast_round_id": round_id,
            "domain": domain,
            "leg_target": _canonical_leg_target(leg_target),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _client_request_id(round_id: str, leg_target: str) -> str:
    return "broadcast-leg-request:v1:" + _domain_hash(
        "peerhub.broadcast.client-request.v1",
        round_id,
        leg_target,
    )


def _idempotency_key(round_id: str, leg_target: str) -> str:
    return "broadcast-leg-admission:v1:" + _domain_hash(
        "peerhub.broadcast.admission-idempotency.v1",
        round_id,
        leg_target,
    )


def _prompt_digest(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _envelope(
    *,
    round_id: str,
    leg_target: str,
    prompt: str,
    idempotency_key_override: str | None = None,
) -> CommandEnvelope:
    leg_idempotency_key = _idempotency_key(round_id, leg_target)
    return CommandEnvelope(
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        client_request_id=_client_request_id(round_id, leg_target),
        correlation_id=f"broadcast:{round_id}",
        client_id=_BROADCAST_CLIENT_ID,
        actor_id="broadcast-coordinator",
        scope={"workspace_id": "workspace-broadcast-correlation"},
        method="peer.ask",
        params={
            "broadcast_round_id": round_id,
            "broadcast_leg_idempotency_key": leg_idempotency_key,
            "prompt_digest": _prompt_digest(prompt),
            "required_capability_tier": CapabilityTier.READ_ONLY.name,
        },
        idempotency_key=idempotency_key_override or leg_idempotency_key,
        expected_policy_revision=7,
        expected_configuration_revision=11,
        client_timestamp=100,
    )


def _completion_contract() -> CompletionContract:
    return CompletionContract(
        contract_id="broadcast-correlation-contract",
        kind=CompletionContractKind.DELIVERY_ONLY,
        requirements=(),
        replay_safe=False,
    )


def _admit(
    service: DispatchService,
    *,
    round_id: str,
    leg_target: str,
    prompt: str,
    idempotency_key_override: str | None = None,
) -> RequestSnapshot:
    request, _, _, _ = service.admit_request(
        _envelope(
            round_id=round_id,
            leg_target=leg_target,
            prompt=prompt,
            idempotency_key_override=idempotency_key_override,
        ),
        authenticated_subject=_SUBJECT,
        completion_contract=_completion_contract(),
        policy_revision=7,
        configuration_revision=11,
        required_capability_tier=CapabilityTier.READ_ONLY,
        selected_peer_instance_id=leg_target,
        selected_profile_id=leg_target,
        route_decision_digest=hashlib.sha256(
            f"route:{leg_target}".encode("utf-8")
        ).hexdigest(),
        session_id=f"session:{round_id}:{leg_target}",
        owner_principal_id=_SUBJECT.principal_id,
        owner_instance_id="broadcast-coordinator",
        authority_epoch=1,
        heartbeat_timeout_ms=5_000,
        owner_peer_id="broadcast-coordinator",
    )
    return request


def _insert_leg(
    connection: sqlite3.Connection,
    *,
    round_id: str,
    leg_target: str,
) -> None:
    connection.execute(
        """
        INSERT INTO broadcast_legs (
            broadcast_round_id,
            leg_target,
            client_id,
            client_leg_request_id,
            command_id,
            leg_state,
            terminal_at
        ) VALUES (?, ?, ?, ?, NULL, 'admitting', NULL)
        """,
        (
            round_id,
            leg_target,
            _BROADCAST_CLIENT_ID,
            _client_request_id(round_id, leg_target),
        ),
    )


def _bind_leg(
    connection: sqlite3.Connection,
    *,
    round_id: str,
    leg_target: str,
    command_id: str,
) -> None:
    connection.execute(
        """
        UPDATE broadcast_legs
        SET command_id = ?, leg_state = 'pending'
        WHERE broadcast_round_id = ? AND leg_target = ?
        """,
        (command_id, round_id, leg_target),
    )


def test_migration_0020_registers_broadcast_schema(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "broadcast-schema.sqlite3"
    store = _store(database_path)
    store.close()

    with _connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (23,)
        assert connection.execute(
            "SELECT name FROM schema_migrations WHERE version = 20"
        ).fetchone() == ("0020_broadcast_correlation",)
        assert {
            row[0]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'trigger'
                  AND tbl_name = 'broadcast_rounds'
                """
            )
        } == {
            "broadcast_rounds_reject_existing_id",
            "broadcast_rounds_wave_immutable",
            "broadcast_rounds_wave_parent_must_preexist",
        }


def test_wave_of_original_seven_case_matrix(tmp_path: Path) -> None:
    database_path = tmp_path / "broadcast-wave-matrix.sqlite3"
    store = _store(database_path)
    store.close()

    with _connect(database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            _multi_insert_rounds(
                connection,
                ("cycle-a", "cycle-b"),
                ("cycle-b", "cycle-a"),
            )

        with pytest.raises(sqlite3.IntegrityError):
            _multi_insert_rounds(
                connection,
                ("reverse-child", "reverse-parent"),
                ("reverse-parent", None),
            )

        with pytest.raises(sqlite3.IntegrityError):
            _insert_round(connection, "self", wave_of="self")

        _insert_round(connection, "separate-root")
        _insert_round(
            connection,
            "separate-wave",
            wave_of="separate-root",
        )

        _insert_round(connection, "chain-root")
        _insert_round(connection, "chain-wave-2", wave_of="chain-root")
        _insert_round(
            connection,
            "chain-wave-3",
            wave_of="chain-wave-2",
        )

        _multi_insert_rounds(
            connection,
            ("bulk-parent", None),
            ("bulk-child", "bulk-parent"),
        )

        _insert_round(connection, "update-root")
        _insert_round(
            connection,
            "update-child",
            wave_of="update-root",
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE broadcast_rounds
                SET wave_of = 'update-child'
                WHERE broadcast_round_id = 'update-root'
                """
            )

        assert connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall() == []


def test_insert_or_replace_cannot_rewrite_root_into_cycle(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "broadcast-replace-cycle.sqlite3"
    store = _store(database_path)
    store.close()

    with _connect(database_path) as connection:
        _insert_round(connection, "replace-root")
        _insert_round(
            connection,
            "replace-child",
            wave_of="replace-root",
        )

        with pytest.raises(
            sqlite3.IntegrityError,
            match="broadcast_round_id already exists",
        ):
            connection.execute(
                """
                INSERT OR REPLACE INTO broadcast_rounds (
                    broadcast_round_id,
                    wave_of,
                    prompt_digest,
                    requested_targets,
                    deadline_at,
                    status,
                    disposition,
                    created_at,
                    closed_at
                ) VALUES (
                    'replace-root',
                    'replace-child',
                    ?,
                    1,
                    NULL,
                    'open',
                    NULL,
                    100,
                    NULL
                )
                """,
                (hashlib.sha256(b"replacement").hexdigest(),),
            )

        assert connection.execute(
            """
            SELECT broadcast_round_id, wave_of
            FROM broadcast_rounds
            ORDER BY broadcast_round_id
            """
        ).fetchall() == [
            ("replace-child", "replace-root"),
            ("replace-root", None),
        ]


def test_upsert_cannot_rewrite_root_into_cycle(tmp_path: Path) -> None:
    database_path = tmp_path / "broadcast-upsert-cycle.sqlite3"
    store = _store(database_path)
    store.close()

    with _connect(database_path) as connection:
        _insert_round(connection, "upsert-root")
        _insert_round(
            connection,
            "upsert-child",
            wave_of="upsert-root",
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO broadcast_rounds (
                    broadcast_round_id,
                    wave_of,
                    prompt_digest,
                    requested_targets,
                    deadline_at,
                    status,
                    disposition,
                    created_at,
                    closed_at
                ) VALUES (
                    'upsert-root',
                    'upsert-child',
                    ?,
                    1,
                    NULL,
                    'open',
                    NULL,
                    100,
                    NULL
                )
                ON CONFLICT (broadcast_round_id) DO UPDATE
                SET wave_of = excluded.wave_of
                """,
                (hashlib.sha256(b"upsert").hexdigest(),),
            )

        assert connection.execute(
            """
            SELECT wave_of
            FROM broadcast_rounds
            WHERE broadcast_round_id = 'upsert-root'
            """
        ).fetchone() == (None,)


def test_distinct_broadcast_legs_admit_distinct_commands_and_replay(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "broadcast-admission-replay.sqlite3"
    store = _store(database_path)
    service = DispatchService(
        store,
        clock=DeterministicClock(start=100),
        ids=SequentialIdSource(),
    )
    round_id = "broadcast-round-admission"
    prompt = "independently review this proposal"
    targets = ("cc.deepthink", "cx.deepthink")

    with _connect(database_path) as connection:
        _insert_round(
            connection,
            round_id,
            requested_targets=len(targets),
            prompt_digest=_prompt_digest(prompt),
        )
        for target in targets:
            _insert_leg(
                connection,
                round_id=round_id,
                leg_target=target,
            )

    admitted: dict[str, RequestSnapshot] = {}
    for target in targets:
        admitted[target] = _admit(
            service,
            round_id=round_id,
            leg_target=target,
            prompt=prompt,
        )
        with _connect(database_path) as connection:
            _bind_leg(
                connection,
                round_id=round_id,
                leg_target=target,
                command_id=str(admitted[target].command_id),
            )

    replay = _admit(
        service,
        round_id=round_id,
        leg_target=targets[0],
        prompt=prompt,
    )

    assert replay.command_id == admitted[targets[0]].command_id
    assert admitted[targets[0]].command_id != admitted[targets[1]].command_id
    assert _idempotency_key(round_id, targets[0]) != _idempotency_key(
        round_id,
        targets[1],
    )

    with _connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT legs.leg_target, legs.command_id
            FROM broadcast_legs AS legs
            JOIN dispatch_requests AS requests
              ON requests.command_id = legs.command_id
            WHERE legs.broadcast_round_id = ?
            ORDER BY legs.leg_target
            """,
            (round_id,),
        ).fetchall()
        assert rows == sorted(
            (
                target,
                str(admitted[target].command_id),
            )
            for target in targets
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM dispatch_requests"
        ).fetchone() == (2,)

    store.close()


def test_broadcast_leg_prompt_digest_conflict_is_rejected(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "broadcast-prompt-conflict.sqlite3"
    store = _store(database_path)
    service = DispatchService(
        store,
        clock=DeterministicClock(start=200),
        ids=SequentialIdSource(),
    )
    round_id = "broadcast-round-conflict"
    target = "ag.deepthink"

    with _connect(database_path) as connection:
        _insert_round(
            connection,
            round_id,
            prompt_digest=_prompt_digest("original prompt"),
        )
        _insert_leg(
            connection,
            round_id=round_id,
            leg_target=target,
        )

    original = _admit(
        service,
        round_id=round_id,
        leg_target=target,
        prompt="original prompt",
    )
    with _connect(database_path) as connection:
        _bind_leg(
            connection,
            round_id=round_id,
            leg_target=target,
            command_id=str(original.command_id),
        )

    with pytest.raises(DuplicateClientRequestError):
        _admit(
            service,
            round_id=round_id,
            leg_target=target,
            prompt="different prompt",
        )

    with _connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM dispatch_requests"
        ).fetchone() == (1,)
        assert connection.execute(
            """
            SELECT command_id
            FROM broadcast_legs
            WHERE broadcast_round_id = ? AND leg_target = ?
            """,
            (round_id, target),
        ).fetchone() == (str(original.command_id),)

    store.close()


def test_reused_leg_idempotency_key_conflicts_across_targets(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "broadcast-key-reuse-conflict.sqlite3"
    store = _store(database_path)
    service = DispatchService(
        store,
        clock=DeterministicClock(start=300),
        ids=SequentialIdSource(),
    )
    round_id = "broadcast-round-key-reuse"
    first_target = "cc.deepthink"
    second_target = "cx.deepthink"
    prompt = "shared prompt"

    with _connect(database_path) as connection:
        _insert_round(
            connection,
            round_id,
            requested_targets=2,
            prompt_digest=_prompt_digest(prompt),
        )
        _insert_leg(
            connection,
            round_id=round_id,
            leg_target=first_target,
        )
        _insert_leg(
            connection,
            round_id=round_id,
            leg_target=second_target,
        )

    first = _admit(
        service,
        round_id=round_id,
        leg_target=first_target,
        prompt=prompt,
    )
    with _connect(database_path) as connection:
        _bind_leg(
            connection,
            round_id=round_id,
            leg_target=first_target,
            command_id=str(first.command_id),
        )

    with pytest.raises(IdempotencyPayloadMismatchError):
        _admit(
            service,
            round_id=round_id,
            leg_target=second_target,
            prompt=prompt,
            idempotency_key_override=_idempotency_key(
                round_id,
                first_target,
            ),
        )

    assert first.command_id is not None
    with _connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM dispatch_requests"
        ).fetchone() == (1,)
        assert connection.execute(
            """
            SELECT leg_target, command_id, leg_state
            FROM broadcast_legs
            WHERE broadcast_round_id = ?
            ORDER BY leg_target
            """,
            (round_id,),
        ).fetchall() == [
            (first_target, str(first.command_id), "pending"),
            (second_target, None, "admitting"),
        ]

    store.close()
