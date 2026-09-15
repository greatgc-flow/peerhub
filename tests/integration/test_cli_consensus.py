from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from peerhub.cli import main
from peerhub.core.context import PathLayout
from peerhub.persistence.dispatch_context import issue_credential


def _propose_args(workspace: Path) -> list[str]:
    return [
        "consensus", "propose", "--workspace", str(workspace),
        "--round-id", "round-cli", "--title", "Ship", "--question", "Ready?",
        "--body", "Decide", "--proposer", "cx", "--required", "cx,ag",
        "--eligible", "cx,ag", "--risk", "normal",
    ]


def test_cli_consensus_propose_vote_and_status(tmp_path: Path, capsys) -> None:
    assert main(_propose_args(tmp_path)) == 0
    assert "round-cli" in capsys.readouterr().out

    assert main([
        "consensus", "vote", "--workspace", str(tmp_path),
        "--round-id", "round-cli", "--actor", "cx", "--choice", "agree",
    ]) == 0
    assert "voting" in capsys.readouterr().out

    assert main([
        "consensus", "vote", "--workspace", str(tmp_path),
        "--round-id", "round-cli", "--actor", "ag", "--choice", "agree",
        "--json",
    ]) == 0
    vote_output = json.loads(capsys.readouterr().out)
    assert vote_output["phase"] == "quorum_reached"
    assert vote_output["quorum"]["reached"] is True

    assert main([
        "consensus", "status", "--workspace", str(tmp_path),
        "--round-id", "round-cli", "--json",
    ]) == 0
    status_output = json.loads(capsys.readouterr().out)
    assert status_output["round_id"] == "round-cli"


def test_cli_consensus_status_not_found_returns_nonzero(tmp_path: Path, capsys) -> None:
    assert main(["workspace", "init", "--workspace", str(tmp_path)]) == 0
    capsys.readouterr()
    assert main([
        "consensus", "status", "--workspace", str(tmp_path),
        "--round-id", "missing",
    ]) == 2
    assert "not found" in capsys.readouterr().err


def test_cli_consensus_list_returns_all_rounds(tmp_path: Path, capsys) -> None:
    assert main(_propose_args(tmp_path)) == 0
    capsys.readouterr()

    second = _propose_args(tmp_path)
    second[second.index("round-cli")] = "round-cli-second"
    assert main(second) == 0
    capsys.readouterr()

    assert main([
        "consensus", "list", "--workspace", str(tmp_path), "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert {proposal["target_id"] for proposal in payload["proposals"]} == {
        "round-cli",
        "round-cli-second",
    }


def _seed_dctx_credential(workspace: Path, *, credential_id: str, peer_instance_id: str) -> None:
    """Seed a real dispatch_requests row + a real D-CTX credential directly
    against the workspace DB -- simpler and more controlled for a CLI-level
    test than running a full `peerhub ask` dispatch, and exercises the same
    dispatch_context_credentials table peerhub ask actually writes to."""

    db_path = PathLayout.for_workspace(workspace).database_path
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """INSERT INTO dispatch_requests (command_id, client_id, client_request_id, correlation_id, authenticated_principal, command_type, idempotency_key, payload_digest, scope_json, params_json, expected_policy_revision_json, expected_configuration_revision_json, policy_revision_json, configuration_revision_json, completion_contract_json, required_capability_tier, selected_peer_instance_id, selected_profile_id, route_decision_digest, lease_id, state, revision, created_at, updated_at)
            VALUES (?, 'client', ?, 'corr', 'prin', 'type', ?, 'hash', '[]', '{}', '0', '0', '0', '0', '{"contract_id":"x","kind":"ALL_OF","requirements":[],"replay_safe":true}', 'READ_ONLY', ?, 'prof', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', ?, 'ADMITTED', 1, 0, 0)""",
            (f"cmd-{credential_id}", f"req-{credential_id}", f"idem-{credential_id}", peer_instance_id, f"lease-{credential_id}"),
        )
        row = conn.execute(
            "SELECT workspace_home_id, activation_epoch FROM workspace_identity WHERE singleton = 1"
        ).fetchone()
        workspace_home_id, activation_epoch = row
        issue_credential(
            conn,
            credential_id=credential_id,
            command_id=f"cmd-{credential_id}",
            workspace_home_id=workspace_home_id,
            activation_epoch=activation_epoch,
            issued_at=0,
            # The real CLI's credential_verifier uses SystemClock.now()
            # (real wall-clock epoch seconds) for `now`, not a test double
            # -- a small sentinel like 999999999 (year 2001) would already
            # be "expired" against any real current timestamp. Year 2100
            # epoch seconds, comfortably past any real test run.
            expires_at=4102444800,
        )
        conn.commit()


def test_cli_consensus_verified_required_accepts_real_credential(tmp_path: Path, capsys) -> None:
    assert main(["workspace", "init", "--workspace", str(tmp_path)]) == 0
    capsys.readouterr()

    args = _propose_args(tmp_path)
    args.append("--verified-required")
    assert main(args) == 0
    capsys.readouterr()

    _seed_dctx_credential(tmp_path, credential_id="cred-cx", peer_instance_id="cx")

    assert main([
        "consensus", "vote", "--workspace", str(tmp_path),
        "--round-id", "round-cli", "--actor", "cx", "--choice", "agree",
        "--credential-id", "cred-cx",
    ]) == 0
    assert "voting" in capsys.readouterr().out


def test_cli_consensus_verified_required_rejects_impersonation(tmp_path: Path, capsys) -> None:
    """The real end-to-end version of the impersonation regression: a
    credential PeerHub minted for peer cx must not authorize a vote cast
    as a different peer (ag), even presented through the real CLI."""

    assert main(["workspace", "init", "--workspace", str(tmp_path)]) == 0
    capsys.readouterr()

    args = _propose_args(tmp_path)
    args.append("--verified-required")
    assert main(args) == 0
    capsys.readouterr()

    _seed_dctx_credential(tmp_path, credential_id="cred-cx", peer_instance_id="cx")

    exit_code = main([
        "consensus", "vote", "--workspace", str(tmp_path),
        "--round-id", "round-cli", "--actor", "ag", "--choice", "agree",
        "--credential-id", "cred-cx",
    ])
    assert exit_code == 2
    assert "does not verify" in capsys.readouterr().err


def test_cli_consensus_verified_required_rejects_missing_credential(tmp_path: Path, capsys) -> None:
    assert main(["workspace", "init", "--workspace", str(tmp_path)]) == 0
    capsys.readouterr()

    args = _propose_args(tmp_path)
    args.append("--verified-required")
    assert main(args) == 0
    capsys.readouterr()

    exit_code = main([
        "consensus", "vote", "--workspace", str(tmp_path),
        "--round-id", "round-cli", "--actor", "cx", "--choice", "agree",
    ])
    assert exit_code == 2
    assert "requires a verified credential" in capsys.readouterr().err
