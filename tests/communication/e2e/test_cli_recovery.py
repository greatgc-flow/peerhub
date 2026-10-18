"""Tests for S2-A guidance registry, recovery advice, and explain command."""
from __future__ import annotations

import json
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
from peerhub.core.store import StorageReadOnlyError, StorageFullError, StorageCorruptError

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
    "CasMismatchError": "Store CAS concurrency mismatch falls back to generic unexpected failure.",
    "ClaimFinalizedError": "Attempt to mutate already-finalized claim falls back to generic unexpected failure.",
    "ClaimScopeError": "Internal claim scope mismatch falls back to generic unexpected failure.",
    "ControlRejectedError": "Internal bridge control cycle rejection falls back to generic unexpected failure.",
    "IdempotencyConflictError": "Store idempotency collision falls back to generic unexpected failure.",
    "IllegalCertaintyTransition": "Internal certainty monotonicity violation falls back to generic unexpected failure.",
    "InvalidTerminalResultError": "Internal invalid terminal cycle state falls back to generic unexpected failure.",
    "InvocationMarkerError": "Internal durable invocation marker failure falls back to generic unexpected failure.",
    "NoDeliveryError": "Bridge delivery abstraction failure falls back to generic unexpected failure.",
    "OffsetAckConflictError": "Bridge offset acknowledgment conflict falls back to generic unexpected failure.",
    "ReconcileRejectedError": "Bridge reconciliation rejection falls back to generic unexpected failure.",
    "RecordRejectedError": "Bridge persistence record rejection falls back to generic unexpected failure.",
    "RuntimeTargetError": "Base class for runtime target adapter errors falls back to generic unexpected failure.",
    "SchemaVersionError": "Store schema version mismatch falls back to generic unexpected failure.",
    "SpawnFailure": "Subprocess spawn low-level error wrapper falls back to generic unexpected failure.",
    "StaleClaimError": "Claim state staleness falls back to generic unexpected failure.",
    "TerminalConflictError": "Bridge terminal state race condition falls back to generic unexpected failure.",
    "UnknownReferenceError": "Claim reference lookup failure falls back to generic unexpected failure.",
}


class SessionValueError(ValueError):
    """Subclass of ValueError carrying 'session' identity in type name."""
    pass


EXCEPTION_SCENARIOS: dict[str, list[tuple[Exception, str]]] = {
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
                pytest.fail(f"Recovery argv {argv} for code {code} failed to parse: {exc}")


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
    assert "Recovery Steps:" in out

    # Found - json
    assert run_explain_cli("ask_uncertain", json_output=True) == 0
    out_json = capsys.readouterr().out
    data = json.loads(out_json)
    assert data["code"] == "ask_uncertain"
    assert isinstance(data["recover"], list)

    # Found via alias
    assert run_explain_cli("uncertain", json_output=False) == 0
    assert "Guidance Code: ask_uncertain" in capsys.readouterr().out

    # Unknown - human
    assert run_explain_cli("non_existent_code", json_output=False) == 1
    err = capsys.readouterr().err
    assert "Unknown guidance code: 'non_existent_code'" in err
    assert "Known codes:" in err

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
    assert "Unknown guidance code" in capsys.readouterr().err


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



