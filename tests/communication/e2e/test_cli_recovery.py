"""Tests for S2-A guidance registry, recovery advice, and explain command."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import pytest

from peerhub.cli.app import build_parser
from peerhub.cli.recovery import (
    REGISTRY,
    GuidanceEntry,
    get_guidance,
    make_guidance_payload,
    render_guidance_hint,
    render_explain,
    run_explain_cli,
    select_guidance_code,
)
from typing import Any

from peerhub.extensions.bridge_claims import ClaimHeldError
from peerhub.extensions.bridge import (
    ConflictingSessionFlagsError,
    InvalidSessionHandleSyntaxError,
    PrespawnError,
    SessionBindingMismatchError,
    SessionDeltaOverflowError,
    SessionFingerprintMismatchError,
    SessionLostError,
    SessionNotFoundError,
    SessionPeerMismatchError,
    SessionStreamMismatchError,
    SessionTombstonedError,
    SessionUnavailableError,
    SessionWorkspaceMismatchError,
)
from peerhub.extensions.ask import LegacySelectorUnsupported, StreamPeerMismatchError
from peerhub.core.store import (
    CasMismatchError,
    IdempotencyConflictError,
    SchemaVersionError,
    StorageReadOnlyError,
    StorageFullError,
    StorageCorruptError,
)

pytestmark = [pytest.mark.unit]

def test_registry_well_formedness():
    """Every registered guidance entry satisfies structural well-formedness contracts."""
    assert len(REGISTRY) >= 9
    for code, entry in REGISTRY.items():
        assert isinstance(entry, GuidanceEntry)
        assert entry.code == code
        assert entry.meaning and isinstance(entry.meaning, str) and len(entry.meaning.strip()) > 10
        assert entry.why and isinstance(entry.why, str) and len(entry.why.strip()) > 10
        assert entry.avoid and isinstance(entry.avoid, str) and len(entry.avoid.strip()) > 10
        assert entry.docs and isinstance(entry.docs, str) and entry.docs.startswith("docs/")
        assert isinstance(entry.recover, list) and len(entry.recover) > 0
        for step in entry.recover:
            assert isinstance(step, dict)
            assert "argv" in step and isinstance(step["argv"], list) and len(step["argv"]) > 0
            assert "purpose" in step and isinstance(step["purpose"], str) and len(step["purpose"].strip()) > 5
            assert "prereqs" in step and isinstance(step["prereqs"], str)


def _inventory_ask_path_emission_boundaries() -> tuple[set[str], set[str]]:
    """Independently inventory emission boundaries across ask path and app.py error paths via AST."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    bridge_file = root / "peerhub" / "extensions" / "bridge.py"
    claims_file = root / "peerhub" / "extensions" / "bridge_claims.py"
    adapter_base = root / "peerhub" / "extensions" / "adapters" / "base.py"
    ask_file = root / "peerhub" / "extensions" / "ask.py"
    app_file = root / "peerhub" / "cli" / "app.py"

    statuses: set[str] = set()
    exceptions: set[str] = set()

    def _extract_raises(file_path: Path) -> None:
        with open(file_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), str(file_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise):
                if isinstance(node.exc, ast.Call) and isinstance(node.exc.func, ast.Name):
                    if node.exc.func.id and node.exc.func.id[0].isupper():
                        exceptions.add(node.exc.func.id)

    # 1. Statuses and raises from bridge.py
    with open(bridge_file, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), str(bridge_file))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Name) and func.id == "CycleResult") or (isinstance(func, ast.Attribute) and func.attr == "CycleResult"):
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    statuses.add(node.args[0].value)
        elif isinstance(node, ast.Raise):
            if isinstance(node.exc, ast.Call) and isinstance(node.exc.func, ast.Name):
                if node.exc.func.id and node.exc.func.id[0].isupper():
                    exceptions.add(node.exc.func.id)

    # 2. Raises from bridge_claims.py and adapter base.py
    _extract_raises(claims_file)
    _extract_raises(adapter_base)

    # 3. Statuses and raises from ask.py
    with open(ask_file, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), str(ask_file))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant) and target.slice.value == "status":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        statuses.add(node.value.value)
        elif isinstance(node, ast.Raise):
            if isinstance(node.exc, ast.Call) and isinstance(node.exc.func, ast.Name):
                if node.exc.func.id and node.exc.func.id[0].isupper():
                    exceptions.add(node.exc.func.id)

    # 4. Exceptions handled in app.py error paths
    with open(app_file, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), str(app_file))
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if isinstance(node.type, ast.Name):
                exceptions.add(node.type.id)
            elif isinstance(node.type, ast.Tuple):
                for elt in node.type.elts:
                    if isinstance(elt, ast.Name):
                        exceptions.add(elt.id)

    return statuses, exceptions


STATUS_TERMINAL_SUCCESS: dict[str, str] = {
    "delivered": "Terminal success delivery completes successfully without recovery guidance.",
    "recovered_terminal": "Idempotent recovery of already-completed terminal record produces no guidance.",
}

STATUS_FALLBACK_ALLOWLIST: dict[str, str] = {
    "ack_failed": "Internal bridge acknowledgment failure during execution loop falls back to generic unexpected failure.",
    "cancelled": "Bridge cycle cancellation falls back to generic unexpected failure.",
    "fenced": "Bridge cycle fencing token conflict falls back to generic unexpected failure.",
    "idle": "Bridge loop idle tick without dispatch falls back to generic unexpected failure.",
    "paused": "Bridge loop pause state falls back to generic unexpected failure.",
}

STATUS_SCENARIOS: dict[str, list[tuple[dict[str, Any], str]]] = {
    "uncertain": [
        ({}, "ask_uncertain"),
    ],
    "blocked_uncertain": [
        ({}, "ask_blocked_uncertain"),
    ],
    "session_unavailable": [
        ({}, "ask_selector_error"),
    ],
    "not_executed": [
        ({}, "ask_refusal"),
    ],
    "failed_not_started": [
        ({"detail_error": "executable not found"}, "ask_launch_failed"),
        ({"detail_error": "unsupported runtime"}, "ask_delivery_failed"),
    ],
    "pending_earlier_record": [
        ({"processed_status": "uncertain"}, "ask_blocked_uncertain"),
        ({"processed_status": "not_executed"}, "ask_refusal"),
        ({"processed_status": "failed_not_started", "detail_error": "executable not found"}, "ask_launch_failed"),
        ({"processed_status": "failed_not_started", "detail_error": "unsupported"}, "ask_delivery_failed"),
        ({"processed_status": "delivered"}, ""),
    ],
}

EXCEPTION_CONTROL_FLOW_ALLOWLIST: dict[str, str] = {
    "Exception": "Standard library exception base class caught in top-level app error dispatch.",
    "KeyboardInterrupt": "User SIGINT/Ctrl+C aborts CLI command without guidance output.",
    "SystemExit": "Process exit handler; translates exit code without guidance output.",
    "ImportError": "Optional dependency loading failure caught during dynamic module import.",
    "OSError": "Low-level OS/socket error caught during file/socket operations.",
    "TypeError": "Standard library type error caught during internal argument validation.",
}

EXCEPTION_FALLBACK_ALLOWLIST: dict[str, str] = {
    "BoundaryConflictError": "Internal bridge state boundary conflict falls back to generic unexpected failure.",
    "ClaimFinalizedError": "Attempt to mutate already-finalized claim falls back to generic unexpected failure.",
    "ClaimScopeError": "Internal claim scope mismatch falls back to generic unexpected failure.",
    "ControlRejectedError": "Internal bridge control cycle rejection falls back to generic unexpected failure.",
    "IllegalCertaintyTransition": "Internal certainty monotonicity violation falls back to generic unexpected failure.",
    "InvalidTerminalResultError": "Internal invalid terminal cycle state falls back to generic unexpected failure.",
    "InvocationMarkerError": "Internal durable invocation marker failure falls back to generic unexpected failure.",
    "NoDeliveryError": "Bridge delivery abstraction failure falls back to generic unexpected failure.",
    "OffsetAckConflictError": "Bridge offset acknowledgment conflict falls back to generic unexpected failure.",
    "ReconcileRejectedError": "Bridge reconciliation rejection falls back to generic unexpected failure.",
    "RecordRejectedError": "Bridge persistence record rejection falls back to generic unexpected failure.",
    "RuntimeTargetError": "Base class for runtime target adapter errors falls back to generic unexpected failure.",
    "SpawnFailure": "Subprocess spawn low-level error wrapper falls back to generic unexpected failure.",
    "StaleClaimError": "Claim state staleness falls back to generic unexpected failure.",
    "TerminalConflictError": "Bridge terminal state race condition falls back to generic unexpected failure.",
    "UnknownReferenceError": "Claim reference lookup failure falls back to generic unexpected failure.",
}


class SessionValueError(ValueError):
    """Subclass of ValueError carrying 'session' identity in type name."""
    pass


EXCEPTION_SCENARIOS: dict[str, list[tuple[Exception, str]]] = {
    "CasMismatchError": [(CasMismatchError("revision changed"), "ask_cas_conflict")],
    "IdempotencyConflictError": [(IdempotencyConflictError("conflicting request"), "ask_idempotency_conflict")],
    "SchemaVersionError": [(SchemaVersionError("unsupported schema"), "ask_schema_version_error")],
    "ClaimHeldError": [
        (ClaimHeldError("claim held by peer"), "ask_claim_held"),
    ],
    "StreamPeerMismatchError": [
        (StreamPeerMismatchError("stream peer mismatch"), "ask_stream_peer_mismatch"),
    ],
    "LegacySelectorUnsupported": [
        (LegacySelectorUnsupported("legacy selector unsupported"), "ask_selector_error"),
    ],
    "ConflictingSessionFlagsError": [
        (ConflictingSessionFlagsError("conflicting session flags"), "ask_selector_error"),
    ],
    "InvalidSessionHandleSyntaxError": [
        (InvalidSessionHandleSyntaxError("invalid session handle syntax"), "ask_selector_error"),
    ],
    "SessionNotFoundError": [
        (SessionNotFoundError("session not found"), "ask_selector_error"),
    ],
    "SessionStreamMismatchError": [
        (SessionStreamMismatchError("session stream mismatch"), "ask_selector_error"),
    ],
    "SessionPeerMismatchError": [
        (SessionPeerMismatchError("session peer mismatch"), "ask_selector_error"),
    ],
    "SessionTombstonedError": [
        (SessionTombstonedError("session tombstoned"), "ask_selector_error"),
    ],
    "SessionLostError": [
        (SessionLostError("session lost"), "ask_selector_error"),
    ],
    "SessionUnavailableError": [
        (SessionUnavailableError("session unavailable"), "ask_selector_error"),
    ],
    "SessionWorkspaceMismatchError": [
        (SessionWorkspaceMismatchError("session workspace mismatch"), "ask_selector_error"),
    ],
    "SessionFingerprintMismatchError": [
        (SessionFingerprintMismatchError("session fingerprint mismatch"), "ask_selector_error"),
    ],
    "SessionBindingMismatchError": [
        (SessionBindingMismatchError("session binding mismatch"), "ask_selector_error"),
    ],
    "SessionDeltaOverflowError": [
        (SessionDeltaOverflowError("session delta overflow"), "ask_selector_error"),
    ],
    "PrespawnError": [
        (PrespawnError("executable not found on PATH"), "ask_launch_failed"),
        (PrespawnError("ag execution requires --writable"), "ask_delivery_failed"),
    ],
    "StorageReadOnlyError": [
        (StorageReadOnlyError("read only"), "ask_delivery_failed"),
    ],
    "StorageFullError": [
        (StorageFullError("disk full"), "ask_delivery_failed"),
    ],
    "StorageCorruptError": [
        (StorageCorruptError("db corrupt"), "ask_delivery_failed"),
    ],
    "ValueError": [
        (SessionValueError("invalid session selector"), "ask_selector_error"),
    ],
}


def test_inventory_driven_ask_path_outcomes_cover_entire_registry():
    """Independent AST scan inventories ask-path emission boundaries; every emitted outcome must map to REGISTRY."""
    inventoried_statuses, inventoried_exceptions = _inventory_ask_path_emission_boundaries()

    # Must find real emitted boundaries in the codebase
    assert len(inventoried_statuses) >= 8, f"Expected >= 8 statuses, found: {inventoried_statuses}"
    assert len(inventoried_exceptions) >= 5, f"Expected >= 5 exceptions, found: {inventoried_exceptions}"

    # Verify inventory completeness against accounted status and exception categories
    exercised_statuses = set(STATUS_SCENARIOS.keys())
    accounted_statuses = exercised_statuses | set(STATUS_TERMINAL_SUCCESS.keys()) | set(STATUS_FALLBACK_ALLOWLIST.keys())
    assert inventoried_statuses == accounted_statuses, (
        f"Inventoried statuses do not match accounted statuses. "
        f"Unaccounted: {inventoried_statuses - accounted_statuses}; "
        f"Stale: {accounted_statuses - inventoried_statuses}"
    )

    exercised_exceptions = set(EXCEPTION_SCENARIOS.keys())
    accounted_exceptions = exercised_exceptions | set(EXCEPTION_CONTROL_FLOW_ALLOWLIST.keys()) | set(EXCEPTION_FALLBACK_ALLOWLIST.keys())
    assert inventoried_exceptions == accounted_exceptions, (
        f"Inventoried exceptions do not match accounted exceptions. "
        f"Unaccounted: {inventoried_exceptions - accounted_exceptions}; "
        f"Stale: {accounted_exceptions - inventoried_exceptions}"
    )

    emitted_codes: set[str] = set()

    # 1. Drive terminal success statuses (must produce no guidance code)
    for status in STATUS_TERMINAL_SUCCESS:
        code = select_guidance_code(status=status)
        assert code == "", f"Success status {status} should not produce guidance, got {code!r}"

    # 2. Drive allowlisted fallback statuses (must produce ask_unexpected_failure, but NOT satisfy coverage)
    for status in STATUS_FALLBACK_ALLOWLIST:
        code = select_guidance_code(status=status)
        assert code == "ask_unexpected_failure", f"Fallback status {status} must yield ask_unexpected_failure, got {code!r}"

    # 3. Drive non-fallback status scenarios (each status must map to its OWN registry entry, never fallback)
    for status, scenarios in STATUS_SCENARIOS.items():
        for kwargs, expected_code in scenarios:
            code = select_guidance_code(status=status, **kwargs)
            assert code == expected_code, f"Status {status} with {kwargs} yielded {code!r}, expected {expected_code!r}"
            if expected_code:
                assert code != "ask_unexpected_failure", f"Status scenario for {status} must not rely on generic unexpected failure fallback"
                assert code in REGISTRY, f"Status outcome {status} emitted unregistered code {code!r}"
                emitted_codes.add(code)

    # 4. Drive allowlisted fallback exceptions (must produce ask_unexpected_failure, but NOT satisfy coverage)
    for exc_name in EXCEPTION_FALLBACK_ALLOWLIST:
        fake_cls = type(exc_name, (Exception,), {})
        code = select_guidance_code(exception=fake_cls("simulated failure"))
        assert code == "ask_unexpected_failure", f"Fallback exception {exc_name} must yield ask_unexpected_failure, got {code!r}"

    # 5. Drive non-fallback exception scenarios (each exception must map to its OWN registry entry, never fallback)
    for exc_name, scenarios in EXCEPTION_SCENARIOS.items():
        for exc_inst, expected_code in scenarios:
            code = select_guidance_code(exception=exc_inst)
            assert code == expected_code, f"Exception {exc_name} yielded {code!r}, expected {expected_code!r}"
            assert code != "ask_unexpected_failure", f"Exception scenario for {exc_name} must not rely on generic unexpected failure fallback"
            assert code in REGISTRY, f"Exception {exc_inst!r} emitted unregistered code {code!r}"
            emitted_codes.add(code)

    # 6. Drive explicit unexpected failure scenario to cover ask_unexpected_failure legitimately
    code = select_guidance_code(exception=RuntimeError("unexpected dispatch failure"))
    assert code == "ask_unexpected_failure"
    emitted_codes.add(code)

    # Every code in the registry must be covered by actual driven emission boundaries
    assert emitted_codes == set(REGISTRY.keys()), f"Uncovered registry codes: {set(REGISTRY.keys()) - emitted_codes}"


@pytest.mark.parametrize("fake_status, fake_exception", [
    ("unhandled_mutation_status_xyz", None),
    (None, "UnhandledMutationExceptionXYZ"),
])
def test_mutation_unhandled_emitted_outcome_fails_coverage(monkeypatch, fake_status, fake_exception):
    """Mutating the inventory with a fake unhandled status or exception must fail coverage."""
    real_statuses, real_exceptions = _inventory_ask_path_emission_boundaries()
    mutated_statuses = real_statuses | ({fake_status} if fake_status else set())
    mutated_exceptions = real_exceptions | ({fake_exception} if fake_exception else set())
    monkeypatch.setattr(
        sys.modules[__name__],
        "_inventory_ask_path_emission_boundaries",
        lambda: (mutated_statuses, mutated_exceptions),
    )
    expected_token = fake_status or fake_exception
    with pytest.raises(AssertionError, match=expected_token):
        test_inventory_driven_ask_path_outcomes_cover_entire_registry()



def test_recovery_actions_parse_with_real_parser():
    parser = build_parser()
    for code, entry in REGISTRY.items():
        for action in entry.recover:
            assert "argv" in action and isinstance(action["argv"], list)
            assert "purpose" in action and isinstance(action["purpose"], str)
            assert "prereqs" in action and isinstance(action["prereqs"], str)
            # argv must start with 'peerhub' or be parseable directly
            argv = action["argv"]
            tokens = argv[1:] if argv and argv[0] == "peerhub" else argv
            try:
                parsed = parser.parse_args(tokens)
                assert parsed is not None
            except SystemExit as exc:
                assert tokens == ["--version"] and exc.code == 0, (
                    f"Recovery argv {argv} for code {code} failed to parse: {exc}"
                )


def test_recovery_text_never_recommends_unsafe_operations():
    """Negative contract: recovery never recommends blind retry, new request-id of original, --fresh, or deleting claims."""
    unsafe_patterns = [
        re.compile(r"\bblind[- ]retry\b", re.IGNORECASE),
        re.compile(r"\bretry (the )?(original|same) (prompt|request) (with|under) (a )?new request[-_]?id\b", re.IGNORECASE),
        re.compile(r"\bdelete claims?\b", re.IGNORECASE),
        re.compile(r"\bpass --fresh to retry\b", re.IGNORECASE),
    ]
    for code, entry in REGISTRY.items():
        for action in entry.recover:
            purpose = action["purpose"]
            prereqs = action["prereqs"]
            for pat in unsafe_patterns:
                assert not pat.search(purpose), f"Unsafe recommendation in {code} purpose: {purpose}"
                assert not pat.search(prereqs), f"Unsafe recommendation in {code} prereqs: {prereqs}"


def test_select_guidance_code_by_composite_context():
    # 1. uncertain / STARTED
    assert select_guidance_code(status="uncertain", certainty="STARTED") == "ask_uncertain"
    assert select_guidance_code(status="uncertain", certainty="MAY_HAVE_STARTED") == "ask_uncertain"

    # 2. blocked-uncertain / pending_earlier_record
    assert select_guidance_code(status="pending_earlier_record", processed_status="uncertain") == "ask_blocked_uncertain"
    assert select_guidance_code(status="pending_earlier_record", processed_status="delivered") == ""
    assert select_guidance_code(status="pending_earlier_record", processed_status="recovered_terminal") == ""
    assert select_guidance_code(status="blocked_uncertain") == "ask_blocked_uncertain"

    # 3. claim-held (ClaimHeldError)
    assert select_guidance_code(exception=ClaimHeldError("claim held")) == "ask_claim_held"

    # 4. selector errors
    assert select_guidance_code(exception=SessionNotFoundError("lost")) == "ask_selector_error"
    assert select_guidance_code(exception=LegacySelectorUnsupported("legacy")) == "ask_selector_error"
    assert select_guidance_code(exception=ConflictingSessionFlagsError("conflict")) == "ask_selector_error"
    assert select_guidance_code(status="session_unavailable") == "ask_selector_error"

    # 5. refusals
    assert select_guidance_code(status="not_executed", reason="quota_exhausted") == "ask_refusal"
    assert select_guidance_code(status="not_executed", reason="invalid_model") == "ask_refusal"

    # 6. delivery_failed
    assert select_guidance_code(status="failed_not_started") == "ask_delivery_failed"

    # 7. unexpected_failure
    assert select_guidance_code(exception=RuntimeError("unexpected")) == "ask_unexpected_failure"

    # 8. success
    assert select_guidance_code(status="delivered") == ""
    assert select_guidance_code(status="recovered_terminal") == ""


def test_guidance_payload_structure():
    payload = make_guidance_payload("ask_uncertain")
    assert payload["code"] == "ask_uncertain"
    assert payload["explain_cmd"] == "peerhub explain ask_uncertain"
    assert isinstance(payload["summary"], str)
    assert len(payload["summary"]) > 0


def test_render_guidance_hint_contains_all_components():
    entry = REGISTRY["ask_uncertain"]
    hint = render_guidance_hint(entry)
    assert "What happened:" in hint
    assert "Rationale:" in hint
    assert entry.why in hint
    assert "Next action:" in hint
    assert "What NOT to do:" in hint
    assert "peerhub explain ask_uncertain" in hint


def test_guidance_context_instantiation(tmp_path):
    db_file = tmp_path / "test.db"
    abs_db = str(db_file.resolve())
    entry = get_guidance("ask_uncertain", db_path=db_file, stream_id="stream-123")
    assert entry is not None
    first_step = entry.recover[0]
    assert first_step["argv"] == ["peerhub", "--db", abs_db, "diag", "health", "--stream", "stream-123"]
    assert "STREAM_ID" not in first_step["argv"]
    second_step = entry.recover[1]
    assert second_step["argv"] == ["peerhub", "--db", abs_db, "diag", "health"]


def test_recovery_wording_honesty():
    entry_blocked = REGISTRY["ask_blocked_uncertain"]
    assert "exactly-once" not in entry_blocked.why.lower()
    assert "only inspects status" not in entry_blocked.why.lower()

    entry_claim = REGISTRY["ask_claim_held"]
    purpose = entry_claim.recover[0]["purpose"]
    assert "not active" in purpose.lower()



def test_explain_cli_output_and_exit_codes(capsys):
    # Found - human
    assert run_explain_cli("ask_uncertain", json_output=False) == 0
    out = capsys.readouterr().out
    assert "Guidance Code: ask_uncertain" in out
    assert "Exit Code:     1" in out
    assert "Recovery Steps:" in out

    # Found - json (single exit)
    assert run_explain_cli("ask_uncertain", json_output=True) == 0
    out_json = capsys.readouterr().out
    data = json.loads(out_json)
    assert data["code"] == "ask_uncertain"
    assert data["exit_code"] == 1
    assert "exits" in data
    assert len(data["exits"]) == 1
    assert isinstance(data["recover"], list)

    # Context-dependent entries: scalar exit_code removed from JSON, exits list present
    for code in ("ask_selector_error", "ask_delivery_failed", "ask_unexpected_failure"):
        entry = REGISTRY[code]
        assert entry.exit_code is None
        data = entry.to_dict()
        assert "exit_code" not in data, f"Scalar exit_code must not be present in {code} JSON"
        assert "exits" in data
        assert len(data["exits"]) >= 2
        for exit_info in data["exits"]:
            assert "code" in exit_info and isinstance(exit_info["code"], int)
            assert "context" in exit_info and isinstance(exit_info["context"], str)

        # Human output shows context-dependent exits
        assert run_explain_cli(code, json_output=False) == 0
        out_human = capsys.readouterr().out
        assert "Exit Codes:    Context-dependent:" in out_human
        for exit_info in data["exits"]:
            assert str(exit_info["code"]) in out_human

        # CLI --json output matches to_dict()
        assert run_explain_cli(code, json_output=True) == 0
        cli_json = json.loads(capsys.readouterr().out)
        assert "exit_code" not in cli_json
        assert cli_json["exits"] == data["exits"]

    # Single-exit entries: scalar exit_code present in JSON, exits present
    for code in ("ask_uncertain", "ask_blocked_uncertain", "ask_claim_held", "ask_refusal", "ask_launch_failed", "ask_stream_peer_mismatch"):
        entry = REGISTRY[code]
        assert entry.exit_code is not None
        data = entry.to_dict()
        assert "exit_code" in data and data["exit_code"] == entry.exit_code
        assert "exits" in data
        assert len(data["exits"]) == 1

        assert run_explain_cli(code, json_output=False) == 0
        out_human = capsys.readouterr().out
        assert f"Exit Code:     {entry.exit_code}" in out_human

    # Found via alias
    assert run_explain_cli("uncertain", json_output=False) == 0
    assert "Guidance Code: ask_uncertain" in capsys.readouterr().out

    # Unknown - human
    assert run_explain_cli("non_existent_code", json_output=False) == 1
    err = capsys.readouterr().err
    assert "No matching lessons" in err
    assert "Try fewer keywords" in err

    # Unknown - json
    assert run_explain_cli("non_existent_code", json_output=True) == 1
    err_json = capsys.readouterr().err
    err_data = json.loads(err_json)
    assert "Unknown guidance code" in err_data["error"]
    assert "known_codes" in err_data


def test_main_explain_dispatch(capsys):
    from peerhub.cli.app import main

    assert main(["explain", "ask_uncertain"]) == 0
    assert "Guidance Code: ask_uncertain" in capsys.readouterr().out

    assert main(["explain", "ask_uncertain", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["code"] == "ask_uncertain"

    assert main(["explain", "unknown_xyz"]) == 1
    assert "No matching lessons" in capsys.readouterr().err


# ------------------------------------------------------------------ Ask path scenario tests
def test_scenario_uncertain_and_pending_earlier_record(tmp_path, monkeypatch, capsys):
    from peerhub.cli.app import main
    from peerhub.extensions import ask as ask_mod
    from tests.communication.fakes import FakeRuntimeTarget

    db = tmp_path / "core.db"
    rt = FakeRuntimeTarget()
    rt.script_deliver(("started", "exec-1"), ("timeout", 5))
    rt.script_deliver(("started", "exec-2"), ("timeout", 5))
    real_ask = ask_mod.ask
    monkeypatch.setattr(ask_mod, "ask", lambda *a, **kw: real_ask(*a, **kw, runtime=rt))

    # 1. Ask becomes uncertain
    code = main(["--db", str(db), "ask", "cx", "first question", "--stream", "s1", "--json"])
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "uncertain"
    assert data["guidance"]["code"] == "ask_uncertain"
    assert data["guidance"]["explain_cmd"] == "peerhub explain ask_uncertain"
    assert data["guidance"]["code"] in REGISTRY

    # Check human stderr on uncertain outcome
    code = main(["--db", str(db), "ask", "cx", "first question (text)", "--stream", "s2"])
    assert code == 1
    err = capsys.readouterr().err
    assert "Guidance [ask_uncertain]:" in err
    assert "default timeout" in err
    assert "do not retry on the same stream; start a new stream with explicit --timeout-seconds" in err
    assert "long reviews: 1800 or more" in err
    assert "Do not retry the prompt under a new request-id, do not pass --fresh, and do not delete claims." in err
    assert "What happened:" in err
    assert "Next action:" in err
    assert "What NOT to do:" in err
    assert "peerhub explain ask_uncertain" in err

    # 2. Subsequent ask is blocked as pending_earlier_record
    rt2 = FakeRuntimeTarget()
    monkeypatch.setattr(ask_mod, "ask", lambda *a, **kw: real_ask(*a, **kw, runtime=rt2))
    code = main(["--db", str(db), "ask", "cx", "second question", "--stream", "s1", "--json"])
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "pending_earlier_record"
    assert data["processed_status"] in ("uncertain", "blocked_uncertain")
    assert data["guidance"]["code"] == "ask_blocked_uncertain"
    assert data["guidance"]["explain_cmd"] == "peerhub explain ask_blocked_uncertain"
    assert data["guidance"]["code"] in REGISTRY

    # Check human stderr for pending_earlier_record
    code = main(["--db", str(db), "ask", "cx", "second question (text)", "--stream", "s1"])
    assert code == 1
    err = capsys.readouterr().err
    assert "Guidance [ask_blocked_uncertain]:" in err
    assert "default timeout" in err
    assert "do not retry on the same stream; start a new stream with explicit --timeout-seconds" in err
    assert "long reviews: 1800 or more" in err
    assert "Do not retry the prompt under a new request-id, do not pass --fresh, and do not delete claims." in err
    assert "peerhub explain ask_blocked_uncertain" in err


def test_scenario_claim_held(tmp_path, monkeypatch, capsys):
    from peerhub.cli.app import main
    from peerhub.core.store import CoreStore
    from peerhub.core.models import Peer, Stream
    from peerhub.core.workspace import Workspace
    from peerhub.extensions.bridge_claims import ClaimStore

    db = tmp_path / "core.db"
    store = CoreStore(db)
    store.register_peer(Peer(peer_id="user"))
    store.register_peer(Peer(peer_id="cx", adapter_ref="cx"))
    store.create_stream(Stream(stream_id="peer:cx:chat", members=["user", "cx"]))
    claims = ClaimStore(db, generation=Workspace(tmp_path).generation)
    claims.acquire("cx", "peer:cx:chat", "active_worker", 300)

    # JSON mode
    code = main(["--db", str(db), "ask", "cx", "hello", "--json"])
    assert code == 1
    out_json = capsys.readouterr().out
    data = json.loads(out_json)
    assert data["status"] == "error"
    assert data["error"]["code"] == "delivery_failed"
    assert data["guidance"]["code"] == "ask_claim_held"
    assert data["guidance"]["explain_cmd"] == "peerhub explain ask_claim_held"
    assert data["guidance"]["code"] in REGISTRY
    assert isinstance(data["guidance"]["recover"], list)
    assert data["guidance"]["recover"][0]["argv"] == [
        "peerhub", "--db", str(db.resolve()), "diag", "health", "--stream", "peer:cx:chat"
    ]

    # Human mode
    code = main(["--db", str(db), "ask", "cx", "hello"])
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR: claim for (cx, peer:cx:chat) held by 'active_worker'" in err
    assert "Guidance [ask_claim_held]:" in err
    assert "What happened:" in err
    assert "Next action:" in err
    assert "What NOT to do:" in err
    assert "peerhub explain ask_claim_held" in err


def test_real_cli_json_guidance_instantiates_concrete_argv_for_non_default_store(tmp_path, capsys):
    """Real CLI execution in JSON mode against non-default store preserves concrete recovery argv."""
    from peerhub.cli.app import main
    from peerhub.core.store import CoreStore
    from peerhub.core.models import Peer, Stream
    from peerhub.core.workspace import Workspace
    from peerhub.extensions.bridge_claims import ClaimStore

    custom_dir = tmp_path / "custom_workspace" / "store"
    custom_dir.mkdir(parents=True)
    custom_db = custom_dir / "non_default.sqlite"

    store = CoreStore(custom_db)
    store.register_peer(Peer(peer_id="user"))
    store.register_peer(Peer(peer_id="cx", adapter_ref="cx"))
    store.create_stream(Stream(stream_id="stream:custom:work", members=["user", "cx"]))
    claims = ClaimStore(custom_db, generation=Workspace(custom_db.resolve().parent).generation)
    claims.acquire("cx", "stream:custom:work", "occupant", 300)

    code = main(["--db", str(custom_db), "ask", "cx", "do work", "--stream", "stream:custom:work", "--json"])
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "error"
    guidance = data["guidance"]
    assert guidance["code"] == "ask_claim_held"
    assert isinstance(guidance["recover"], list)
    assert len(guidance["recover"]) > 0

    abs_db_str = str(custom_db.resolve())
    for step in guidance["recover"]:
        argv = step["argv"]
        assert isinstance(argv, list)
        assert argv[0] == "peerhub"
        assert "--db" in argv
        assert argv[argv.index("--db") + 1] == abs_db_str
        assert "STREAM_ID" not in argv

    assert guidance["recover"][0]["argv"] == [
        "peerhub", "--db", abs_db_str, "diag", "health", "--stream", "stream:custom:work"
    ]



def test_scenario_session_selector_errors(tmp_path, capsys):
    from peerhub.cli.app import main

    db = tmp_path / "core.db"

    # Conflicting flags
    code = main(["--db", str(db), "ask", "cx", "hello", "--fresh", "--session", "sess_0123456789abcdef", "--json"])
    assert code == 2
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "error"
    assert data["error"]["code"] == "conflicting_session_flags"
    assert data["guidance"]["code"] == "ask_selector_error"
    assert data["guidance"]["explain_cmd"] == "peerhub explain ask_selector_error"

    # Invalid handle syntax
    code = main(["--db", str(db), "ask", "cx", "hello", "--session", "invalid_handle", "--json"])
    assert code == 2
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "error"
    assert data["error"]["code"] == "invalid_session_handle_syntax"
    assert data["guidance"]["code"] == "ask_selector_error"

    # Session not found
    code = main(["--db", str(db), "ask", "cx", "hello", "--session", "sess_0123456789abcdef", "--json"])
    assert code == 2
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "error"
    assert data["error"]["code"] == "session_not_found"
    assert data["guidance"]["code"] == "ask_selector_error"

    # Human mode check
    code = main(["--db", str(db), "ask", "cx", "hello", "--session", "invalid_handle"])
    assert code == 2
    err = capsys.readouterr().err
    assert "Guidance [ask_selector_error]:" in err
    assert "peerhub explain ask_selector_error" in err


def test_scenario_refusal_and_cached_replay(tmp_path, monkeypatch, capsys):
    import sys
    import textwrap
    from peerhub.cli.app import main
    from peerhub.extensions.adapters import CliRuntimeTarget
    from peerhub.extensions import ask as ask_mod

    db = tmp_path / "core.db"
    ws = tmp_path / "ws_refusal"
    ws.mkdir()
    script = tmp_path / "refuse.py"
    script.write_text(textwrap.dedent("""
        import sys
        sys.stdout.write('{"type":"error","message":"You\\'ve hit your usage limit. You can continue with a smaller model, or try again at 12:24 PM."}\\n')
        sys.exit(1)
    """), encoding="utf-8")
    target = CliRuntimeTarget("cx", ws, command=[sys.executable, str(script)], timeout_s=15.0)
    real_ask = ask_mod.ask
    monkeypatch.setattr(ask_mod, "ask", lambda *a, **kw: real_ask(*a, **kw, runtime=target))

    # 1. Refusal execution
    code = main(["--db", str(db), "ask", "cx", "check quota", "--request-id", "req-refused", "--json"])
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "not_executed"
    assert data["reason"] == "usage_limit"
    assert data["guidance"]["code"] == "ask_refusal"
    assert data["guidance"]["explain_cmd"] == "peerhub explain ask_refusal"
    assert data["guidance"]["code"] in REGISTRY

    # Human mode
    code = main(["--db", str(db), "ask", "cx", "check quota human", "--request-id", "req-refused-2"])
    assert code == 1
    err = capsys.readouterr().err
    assert "Task outcome: NOT_EXECUTED" in err
    assert "Guidance [ask_refusal]:" in err
    assert "peerhub explain ask_refusal" in err

    # 2. Cached replay of refusal (early return at ask.py line 134)
    code = main(["--db", str(db), "ask", "cx", "check quota", "--request-id", "req-refused", "--json"])
    assert code == 1
    replay_data = json.loads(capsys.readouterr().out)
    assert replay_data["status"] == "not_executed"
    assert replay_data["guidance"]["code"] == "ask_refusal"
    assert replay_data["guidance"]["explain_cmd"] == "peerhub explain ask_refusal"


def test_scenario_success_has_no_guidance(tmp_path, monkeypatch, capsys):
    from peerhub.cli.app import main
    from peerhub.extensions import ask as ask_mod
    from tests.communication.fakes import FakeRuntimeTarget

    db = tmp_path / "core.db"
    rt = FakeRuntimeTarget()
    rt.script_deliver(("started", "exec-1"), ("terminal", {"response": "Success answer"}))
    real_ask = ask_mod.ask
    monkeypatch.setattr(ask_mod, "ask", lambda *a, **kw: real_ask(*a, **kw, runtime=rt))

    code = main(["--db", str(db), "ask", "cx", "hello", "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "delivered"
    assert data["response"] == "Success answer"
    assert "guidance" not in data


def test_bare_peerhub_prints_guide_and_missing_store_guidance(tmp_path, capsys):
    """#121: bare peerhub runs guide (exit 0); missing store prints path, source, quoted --db example, and Learn."""
    from peerhub.cli.app import main

    assert main([]) == 0
    out = capsys.readouterr().out
    assert "PeerHub Quick Reference & Best Practices" in out

    missing_db = tmp_path / "dir with spaces" / "missing.db"
    assert main(["--db", str(missing_db), "peer", "list"]) == 5
    err = capsys.readouterr().err
    assert str(missing_db) in err
    assert "(source: explicit)" in err
    assert f'peerhub --db "{missing_db}" peer register --peer local --name local' in err
    assert "Learn: peerhub guide" in err


def test_scenario_stream_peer_mismatch_and_launch_failed(tmp_path, monkeypatch, capsys):
    """#125 and #120: reusing an ask stream across different peers fails closed with ask_stream_peer_mismatch; missing binary maps to ask_launch_failed."""
    from peerhub.cli.app import main
    from peerhub.extensions import ask as ask_mod
    from peerhub.extensions.adapters import CliRuntimeTarget
    from tests.communication.fakes import FakeRuntimeTarget

    db = tmp_path / "core.db"
    rt = FakeRuntimeTarget()
    rt.script_deliver(("started", "exec-1"), ("terminal", {"response": "ok"}))
    real_ask = ask_mod.ask
    monkeypatch.setattr(ask_mod, "ask", lambda *a, **kw: real_ask(*a, **kw, runtime=rt))

    # Establish stream "shared-task" with peer cx
    assert main(["--db", str(db), "ask", "cx", "step 1", "--stream", "shared-task", "--json"]) == 0
    capsys.readouterr()

    # Attempt to reuse "shared-task" with peer cc -> ask_stream_peer_mismatch (exit 2)
    assert main(["--db", str(db), "ask", "cc", "step 2", "--stream", "shared-task", "--json"]) == 2
    mismatch = json.loads(capsys.readouterr().out)
    assert mismatch["status"] == "error"
    assert mismatch["error"]["code"] == "stream_peer_mismatch"
    assert mismatch["guidance"]["code"] == "ask_stream_peer_mismatch"

    # Launch failure (#120) -> ask_launch_failed
    ws = tmp_path / "ws_launch"
    ws.mkdir()
    bad_target = CliRuntimeTarget("cx", ws, command=["nonexistent-provider-binary-xyz123"])
    monkeypatch.setattr(ask_mod, "ask", lambda *a, **kw: real_ask(*a, **kw, runtime=bad_target))
    assert main(["--db", str(db), "ask", "cx", "hello", "--stream", "launch-fail", "--json"]) == 1
    launch_data = json.loads(capsys.readouterr().out)
    assert launch_data["status"] == "failed_not_started"
    assert launch_data["guidance"]["code"] == "ask_launch_failed"


def test_tool_only_turn_empty_response_propagated(tmp_path):
    """#126: empty terminal response sets empty_response=True and task_effects='unverified' in ask() result."""
    from peerhub.extensions.ask import ask
    from tests.communication.fakes import FakeRuntimeTarget

    db = tmp_path / "core.db"
    rt = FakeRuntimeTarget()
    rt.script_deliver(
        ("started", "exec-empty"),
        ("terminal", {"response": "", "empty_response": True, "task_effects": "unverified"}),
    )
    res = ask(db, "cx", "stage files", runtime=rt)
    assert res["status"] == "delivered"
    assert res["certainty"] == "TERMINAL"
    assert res["response"] == ""
    assert res["empty_response"] is True
    assert res["task_effects"] == "unverified"


def test_scenario_pending_earlier_record_processed_status_cases(tmp_path, monkeypatch, capsys):
    """P2: pending_earlier_record guidance and safe actions depend on earlier processed_status."""
    from peerhub.cli.app import main
    from peerhub.extensions import ask as ask_mod

    db = tmp_path / "core.db"

    # Case 1: earlier delivered / recovered_terminal -> guidance is empty, re-run prompt on warm session
    assert select_guidance_code(status="pending_earlier_record", processed_status="delivered") == ""
    assert select_guidance_code(status="pending_earlier_record", processed_status="recovered_terminal") == ""

    monkeypatch.setattr(ask_mod, "ask", lambda *a, **kw: {
        "status": "pending_earlier_record",
        "processed_status": "delivered",
        "record_id": 10,
        "response": "earlier answer",
        "session": {"session_id": "sess_1", "generation": 1},
    })
    code = main(["--db", str(db), "ask", "cx", "queued prompt", "--json"])
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "pending_earlier_record"
    assert data["processed_status"] == "delivered"
    assert "guidance" not in data

    # Case 2: earlier not_executed -> ask_refusal
    assert select_guidance_code(status="pending_earlier_record", processed_status="not_executed") == "ask_refusal"
    monkeypatch.setattr(ask_mod, "ask", lambda *a, **kw: {
        "status": "pending_earlier_record",
        "processed_status": "not_executed",
        "reason": "quota_exhausted",
        "record_id": 11,
    })
    code = main(["--db", str(db), "ask", "cx", "queued prompt", "--json"])
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "pending_earlier_record"
    assert data["guidance"]["code"] == "ask_refusal"

    # Case 3: earlier failed_not_started -> launch_failed or delivery_failed
    assert select_guidance_code(status="pending_earlier_record", processed_status="failed_not_started", detail_error="Executable not found: codex") == "ask_launch_failed"
    assert select_guidance_code(status="pending_earlier_record", processed_status="failed_not_started", detail_error="Execution policy rejected") == "ask_delivery_failed"
    monkeypatch.setattr(ask_mod, "ask", lambda *a, **kw: {
        "status": "pending_earlier_record",
        "processed_status": "failed_not_started",
        "error": {"code": "delivery_failed", "message": "Executable not found: codex"},
        "record_id": 12,
    })
    code = main(["--db", str(db), "ask", "cx", "queued prompt", "--json"])
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "pending_earlier_record"
    assert data["guidance"]["code"] == "ask_launch_failed"

    # Case 4: earlier uncertain / blocked_uncertain -> ask_blocked_uncertain
    assert select_guidance_code(status="pending_earlier_record", processed_status="uncertain") == "ask_blocked_uncertain"
    assert select_guidance_code(status="pending_earlier_record", processed_status="blocked_uncertain") == "ask_blocked_uncertain"
    monkeypatch.setattr(ask_mod, "ask", lambda *a, **kw: {
        "status": "pending_earlier_record",
        "processed_status": "uncertain",
        "record_id": 13,
    })
    code = main(["--db", str(db), "ask", "cx", "queued prompt", "--json"])
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "pending_earlier_record"
    assert data["guidance"]["code"] == "ask_blocked_uncertain"


@pytest.mark.parametrize("exception, guidance_code, expected_exit", [
    (SessionLostError("continuity lost"), "ask_selector_error", 1),
    (SessionNotFoundError("missing handle"), "ask_selector_error", 2),
    (PrespawnError("execution policy rejected"), "ask_delivery_failed", 1),
    (StorageReadOnlyError("read only"), "ask_delivery_failed", 4),
    (StorageFullError("disk full"), "ask_delivery_failed", 4),
    (StorageCorruptError("corrupt store"), "ask_delivery_failed", 4),
])
def test_context_exit_registry_matches_cli(tmp_path, monkeypatch, capsys, exception, guidance_code, expected_exit):
    from peerhub.cli.app import main
    from peerhub.extensions import ask as ask_mod

    def fail(*args, **kwargs):
        raise exception

    monkeypatch.setattr(ask_mod, "ask", fail)
    actual_exit = main(["--db", str(tmp_path / "core.db"), "ask", "cx", "hello", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["guidance"]["code"] == guidance_code
    assert actual_exit == expected_exit
    assert main(["explain", guidance_code, "--json"]) == 0
    explanation = json.loads(capsys.readouterr().out)
    assert "exit_code" not in explanation
    assert actual_exit in {case["code"] for case in explanation["exits"]}
    assert explanation["exits"] == REGISTRY[guidance_code].exits


@pytest.mark.parametrize("exception, error_code, guidance_code, expected_exit", [
    (RuntimeError("dispatch failed"), "delivery_failed", "ask_unexpected_failure", 1),
    (ValueError("invalid input"), "invalid_arguments", "ask_unexpected_failure", 2),
    (IdempotencyConflictError("conflicting request"), "idempotency_conflict", "ask_idempotency_conflict", 2),
    (CasMismatchError("revision changed"), "cas_conflict", "ask_cas_conflict", 3),
    (SchemaVersionError("unsupported schema"), "schema_version_error", "ask_schema_version_error", 6),
])
def test_failure_exit_contexts(tmp_path, monkeypatch, capsys, exception, error_code, guidance_code, expected_exit):
    from peerhub.cli.app import main
    from peerhub.extensions import ask as ask_mod

    def fail(*args, **kwargs):
        raise exception

    monkeypatch.setattr(ask_mod, "ask", fail)
    actual_exit = main(["--db", str(tmp_path / "core.db"), "ask", "cx", "hello", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == error_code
    assert payload["guidance"]["code"] == guidance_code
    assert actual_exit == expected_exit
    assert main(["explain", guidance_code, "--json"]) == 0
    explanation = json.loads(capsys.readouterr().out)
    if guidance_code == "ask_unexpected_failure":
        assert "exit_code" not in explanation
        assert {case["code"] for case in explanation["exits"]} == {1, 2}
    else:
        assert explanation["exit_code"] == expected_exit
        assert explanation["code"] == guidance_code
    assert actual_exit in {case["code"] for case in explanation["exits"]}


@pytest.mark.parametrize("processed_status, detail_error, expected_guidance", [
    ("delivered", None, ""),
    ("recovered_terminal", None, ""),
    ("not_executed", None, "ask_refusal"),
    ("failed_not_started", "executable not found", "ask_launch_failed"),
    ("failed_not_started", "execution policy rejected", "ask_delivery_failed"),
    ("uncertain", None, "ask_blocked_uncertain"),
    ("blocked_uncertain", None, "ask_blocked_uncertain"),
])
def test_pending_case_registry_matches_cli(tmp_path, monkeypatch, capsys, processed_status, detail_error, expected_guidance):
    from peerhub.cli.app import main
    from peerhub.cli.recovery import RESULT_STATUS_REGISTRY
    from peerhub.extensions import ask as ask_mod

    monkeypatch.setattr(ask_mod, "ask", lambda *a, **kw: {
        "status": "pending_earlier_record", "processed_status": processed_status,
        "detail": {"error": detail_error},
    })
    actual_exit = main(["--db", str(tmp_path / "core.db"), "ask", "cx", "queued", "--request-id", "queued-1", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload.get("guidance", {}).get("code", "") == expected_guidance
    entry = RESULT_STATUS_REGISTRY["pending_earlier_record"]
    assert actual_exit == entry.exit_code == 1
    cases = [case for case in entry.processed_status_cases if processed_status in case["processed_status"].split(" / ")]
    assert len(cases) == 1
    assert (expected_guidance or "(none)") in cases[0]["guidance_code"].split(" / ")
    if not expected_guidance:
        assert "same request-id" in cases[0]["safe_action"]


def test_actual_cli_exit_codes_across_contexts(tmp_path, monkeypatch, capsys):
    """P2: Verify actual CLI exit codes match documented context-dependent exits."""
    from peerhub.cli.app import main
    from peerhub.extensions import ask as ask_mod

    db = tmp_path / "core.db"

    # 1. ask_selector_error:
    # Context 1a: SessionLostError -> CLI exits 1
    monkeypatch.setattr(ask_mod, "ask", lambda *a, **kw: (_ for _ in ()).throw(SessionLostError("Session lost")))
    code = main(["--db", str(db), "ask", "cx", "hello", "--json"])
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["error"]["code"] == "session_lost"
    assert data["guidance"]["code"] == "ask_selector_error"

    # Context 1b: Conflicting session flags -> CLI exits 2
    code = main(["--db", str(db), "ask", "cx", "hello", "--fresh", "--session", "sess_0123456789abcdef", "--json"])
    assert code == 2
    data = json.loads(capsys.readouterr().out)
    assert data["error"]["code"] == "conflicting_session_flags"
    assert data["guidance"]["code"] == "ask_selector_error"

    # Context 1c: Invalid session handle syntax -> CLI exits 2
    code = main(["--db", str(db), "ask", "cx", "hello", "--session", "bad_handle", "--json"])
    assert code == 2
    data = json.loads(capsys.readouterr().out)
    assert data["error"]["code"] == "invalid_session_handle_syntax"
    assert data["guidance"]["code"] == "ask_selector_error"

    # 2. ask_delivery_failed:
    # Context 2a: Adapter prespawn / execution failure -> CLI exits 1
    monkeypatch.setattr(ask_mod, "ask", lambda *a, **kw: (_ for _ in ()).throw(PrespawnError("Policy check rejected execution")))
    code = main(["--db", str(db), "ask", "cx", "hello", "--json"])
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["error"]["code"] == "delivery_failed"
    assert data["guidance"]["code"] == "ask_delivery_failed"

    # Context 2b: Storage failure (StorageReadOnlyError) -> CLI exits 4
    monkeypatch.setattr(ask_mod, "ask", lambda *a, **kw: (_ for _ in ()).throw(StorageReadOnlyError("Disk read-only")))
    code = main(["--db", str(db), "ask", "cx", "hello", "--json"])
    assert code == 4
    data = json.loads(capsys.readouterr().out)
    assert data["error"]["code"] == "storage_corrupt"
    assert data["guidance"]["code"] == "ask_delivery_failed"

    # 3. ask_stream_peer_mismatch: CLI exits 2
    monkeypatch.setattr(ask_mod, "ask", lambda *a, **kw: (_ for _ in ()).throw(StreamPeerMismatchError("Stream peer mismatch")))
    code = main(["--db", str(db), "ask", "cx", "hello", "--json"])
    assert code == 2
    data = json.loads(capsys.readouterr().out)
    assert data["error"]["code"] == "stream_peer_mismatch"
    assert data["guidance"]["code"] == "ask_stream_peer_mismatch"

    # 4. ask_claim_held: CLI exits 1
    monkeypatch.setattr(ask_mod, "ask", lambda *a, **kw: (_ for _ in ()).throw(ClaimHeldError("Claim held")))
    code = main(["--db", str(db), "ask", "cx", "hello", "--json"])
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["guidance"]["code"] == "ask_claim_held"

    # 5. ask_launch_failed: CLI exits 1
    monkeypatch.setattr(ask_mod, "ask", lambda *a, **kw: (_ for _ in ()).throw(PrespawnError("executable not found: codex")))
    code = main(["--db", str(db), "ask", "cx", "hello", "--json"])
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["guidance"]["code"] == "ask_launch_failed"

    # 6. ask_uncertain: CLI exits 1
    monkeypatch.setattr(ask_mod, "ask", lambda *a, **kw: {
        "status": "uncertain",
        "certainty": "STARTED",
        "response": None,
    })
    code = main(["--db", str(db), "ask", "cx", "hello", "--json"])
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "uncertain"
    assert data["guidance"]["code"] == "ask_uncertain"

    # 7. ask_refusal: CLI exits 1
    monkeypatch.setattr(ask_mod, "ask", lambda *a, **kw: {
        "status": "not_executed",
        "reason": "quota_exhausted",
        "response": None,
    })
    code = main(["--db", str(db), "ask", "cx", "hello", "--json"])
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "not_executed"
    assert data["guidance"]["code"] == "ask_refusal"


def test_guidance_reference_doc_parity():
    """Issue #135: Parity test between guidance registry, session definitions, and docs/guidance-reference.md."""
    from peerhub.cli.recovery import render_guidance_reference, REGISTRY, RESULT_STATUS_REGISTRY

    doc_path = Path("docs/guidance-reference.md")
    assert doc_path.is_file(), "docs/guidance-reference.md does not exist"
    doc_content = doc_path.read_text(encoding="utf-8")

    # Parity with generator
    expected_content = render_guidance_reference()
    assert doc_content.strip() == expected_content.strip(), "docs/guidance-reference.md differs from render_guidance_reference()"

    # Verify coverage of every registered result status
    for status_name, res_entry in RESULT_STATUS_REGISTRY.items():
        assert f"`{status_name}`" in doc_content, f"Result status {status_name} missing from doc"
        assert res_entry.meaning in doc_content, f"Meaning for status {status_name} missing from doc"
        assert str(res_entry.exit_code) in doc_content

    # Verify pending_earlier_record cases in doc
    pending_entry = RESULT_STATUS_REGISTRY["pending_earlier_record"]
    assert pending_entry.processed_status_cases
    for case in pending_entry.processed_status_cases:
        assert case["processed_status"] in doc_content, f"Case {case['processed_status']} missing from doc"
        assert case["meaning"] in doc_content, f"Meaning for {case['processed_status']} missing from doc"
        assert case["safe_action"] in doc_content, f"Safe action for {case['processed_status']} missing from doc"

    # Verify coverage of every registered guidance code and its exits
    for code, entry in REGISTRY.items():
        assert code in doc_content, f"Guidance code {code} missing from doc"
        assert entry.meaning in doc_content, f"Meaning for {code} missing from doc"
        for step in entry.recover:
            assert step["purpose"] in doc_content, f"Recovery purpose for {code} missing"
        if entry.exit_code is not None:
            assert f"- **Exit Code**: {entry.exit_code}" in doc_content
        else:
            assert f"#### `{code}`\n\n- **Exit Codes** (context-dependent):" in doc_content
            for e in entry.exits:
                assert f"- `{e['code']}`: {e['context']}" in doc_content

    # Verify session state definitions and qualified semantics in doc
    assert "ACTIVE" in doc_content
    assert "actual eligibility also requires" in doc_content
    assert "LOST" in doc_content
    assert "does not mean the next request automatically starts a new generation" in doc_content
    assert "select another compatible retained session" in doc_content
    assert "subsequent turns automatically allocate a new session generation" not in doc_content
    assert "FRESH" in doc_content
    assert "TOMBSTONED" in doc_content
    assert "resumable" in doc_content
    assert "session_generation" in doc_content
    assert "binding change" in doc_content.lower()


def test_guidance_reference_relative_links_validity():
    """#135 / S2-A: Markdown links inside docs/guidance-reference.md must be relative to docs/ and resolve to existing files."""
    doc_path = Path("docs/guidance-reference.md")
    assert doc_path.is_file()
    doc_content = doc_path.read_text(encoding="utf-8")

    link_pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    matches = link_pattern.findall(doc_content)
    assert len(matches) >= 4, f"Expected at least 4 markdown links, found {len(matches)}"

    for text, link in matches:
        if link.startswith("http://") or link.startswith("https://") or link.startswith("#"):
            continue
        # Relative link must not start with docs/ (which would cause docs/docs/...)
        assert not link.startswith("docs/"), (
            f"Link {link!r} in docs/guidance-reference.md should be relative to docs/ without 'docs/' prefix"
        )
        resolved_target = (doc_path.parent / link).resolve()
        assert resolved_target.is_file(), (
            f"Link {link!r} in docs/guidance-reference.md does not resolve to an existing file ({resolved_target})"
        )







@pytest.mark.parametrize("status", ["delivered", "recovered_terminal"])
@pytest.mark.parametrize("response", ["", "answer"])
@pytest.mark.parametrize("json_output", [False, True])
def test_empty_response_cli_note(tmp_path, monkeypatch, capsys, status, response, json_output):
    from peerhub.cli.app import main
    from peerhub.extensions import ask as ask_mod

    session = {"handle": "sess_0000000000000001", "mode": "reuse", "generation": 1}
    monkeypatch.setattr(ask_mod, "ask", lambda *a, **kw: {
        "status": status, "response": response, "session": session,
        "empty_response": response == "", "task_effects": "unverified",
    })
    argv = ["--db", str(tmp_path / "core.db"), "ask", "cx", "hello"]
    assert main(argv + (["--json"] if json_output else [])) == 0
    output = capsys.readouterr()
    assert output.err == ("note: empty response (see --json)\n" if response == "" and not json_output else "")
    if json_output:
        payload = json.loads(output.out)
        assert payload["response"] == response
        assert payload["empty_response"] == (response == "")
    else:
        assert output.out == f"{response}\nsession: {session['handle']} reuse gen1\n"


@pytest.mark.parametrize("flags, selector_error", [
    (["--session"], True),
    (["--fresh=yes"], True),
    (["--fresh", "--timeout-seconds", "bad"], False),
    (["--session", "sess_0000000000000001", "--max-output-bytes", "bad"], False),
    (["--fresh", "--unknown"], False),
    (["--session=sess_0000000000000001", "--unknown"], False),
])
def test_argparse_guidance_uses_failing_argument(tmp_path, capsys, flags, selector_error):
    from peerhub.cli.app import main

    assert main(["--db", str(tmp_path / "core.db"), "ask", "cx", "hello", "--json", *flags]) == 2
    output = capsys.readouterr()
    payload = json.loads(output.out)
    expected = "ask_selector_error" if selector_error else "ask_unexpected_failure"
    assert payload["guidance"]["code"] == expected
    assert payload["session"] is None
    assert not (tmp_path / "core.db").exists()


def test_bare_cli_exit_change_documented_under_breaking():
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    breaking = changelog.split("### Breaking", 1)[1].split("### ", 1)[0]
    assert "Bare `peerhub` exit 2 -> 0" in breaking


def test_provider_limit_in_uncertain_error_is_surfaced():
    from peerhub.cli.app import _provider_limit
    got = _provider_limit("ag exited 3: error: Individual quota reached. Please upgrade. Resets in 1h22m56s.\nAGY_ERROR: {}")
    assert got == {"reason": "quota_exhausted", "reset_time": "1h22m56s"}
    assert _provider_limit("cx exited 1: You've hit your usage limit. try again at 3:05 PM") == {"reason": "usage_limit", "reset_time": "3:05 PM"}
    assert _provider_limit("ag exited 1: fatal: unrelated") is None
    assert _provider_limit(None) is None
