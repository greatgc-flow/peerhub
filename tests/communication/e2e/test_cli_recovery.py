"""Tests for S2-A guidance registry, recovery advice, and explain command."""
from __future__ import annotations

import json
import re
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
from peerhub.extensions.bridge_claims import ClaimHeldError
from peerhub.extensions.bridge import SessionNotFoundError, ConflictingSessionFlagsError
from peerhub.extensions.ask import LegacySelectorUnsupported

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
    ask_file = root / "peerhub" / "extensions" / "ask.py"
    app_file = root / "peerhub" / "cli" / "app.py"

    statuses: set[str] = set()
    # 1. Statuses from bridge.py (CycleResult calls)
    with open(bridge_file, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), str(bridge_file))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Name) and func.id == "CycleResult") or (isinstance(func, ast.Attribute) and func.attr == "CycleResult"):
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    statuses.add(node.args[0].value)

    # 2. Statuses and exceptions from ask.py
    exceptions: set[str] = set()
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
                exceptions.add(node.exc.func.id)

    # 3. Exceptions handled in app.py
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


def test_inventory_driven_ask_path_outcomes_cover_entire_registry():
    """Independent AST scan inventories ask-path emission boundaries; every emitted outcome must be in REGISTRY."""
    from peerhub.extensions.bridge_claims import ClaimHeldError
    from peerhub.extensions.bridge import PrespawnError, SessionNotFoundError, ConflictingSessionFlagsError
    from peerhub.extensions.ask import LegacySelectorUnsupported, StreamPeerMismatchError
    from peerhub.core.store import StorageReadOnlyError, StorageFullError, StorageCorruptError

    inventoried_statuses, inventoried_exceptions = _inventory_ask_path_emission_boundaries()

    # Must find real emitted boundaries in the codebase
    assert len(inventoried_statuses) >= 8, f"Expected >= 8 statuses, found: {inventoried_statuses}"
    assert len(inventoried_exceptions) >= 5, f"Expected >= 5 exceptions, found: {inventoried_exceptions}"

    emitted_codes: set[str] = set()

    # Drive all inventoried statuses through select_guidance_code
    for status in inventoried_statuses:
        if status in ("delivered", "recovered_terminal"):
            assert select_guidance_code(status=status) == "", f"Success status {status} should not produce guidance"
            continue

        if status == "pending_earlier_record":
            for ps in ("delivered", "not_executed", "failed_not_started", "uncertain"):
                code = select_guidance_code(status=status, processed_status=ps)
                if code:
                    assert code in REGISTRY, f"Status outcome {status}/{ps} emitted unregistered code {code!r}"
                    emitted_codes.add(code)
            continue

        if status == "failed_not_started":
            for detail in ("executable not found", "unsupported runtime"):
                code = select_guidance_code(status=status, detail_error=detail)
                assert code in REGISTRY, f"Status outcome {status}/{detail} emitted unregistered code {code!r}"
                emitted_codes.add(code)
            continue

        code = select_guidance_code(status=status)
        assert code in REGISTRY, f"Status {status} emitted unregistered code {code!r}"
        emitted_codes.add(code)

    # Drive inventoried exception classes
    exception_instances = [
        ClaimHeldError("claim held"),
        StreamPeerMismatchError("stream peer mismatch"),
        SessionNotFoundError("session not found"),
        LegacySelectorUnsupported("legacy selector"),
        ConflictingSessionFlagsError("conflicting session flags"),
        PrespawnError("executable not found on PATH"),
        PrespawnError("policy denied"),
        StorageReadOnlyError("read only"),
        StorageFullError("disk full"),
        StorageCorruptError("db corrupt"),
        ValueError("session handle invalid"),
        RuntimeError("unexpected"),
    ]
    for exc in exception_instances:
        code = select_guidance_code(exception=exc)
        assert code in REGISTRY, f"Exception {exc!r} emitted unregistered code {code!r}"
        emitted_codes.add(code)

    # Every code in the registry must be covered by actual inventoried emission boundaries
    assert emitted_codes == set(REGISTRY.keys()), f"Uncovered registry codes: {set(REGISTRY.keys()) - emitted_codes}"

    # Contract: any new unmapped outcome without a registry entry MUST fail
    with pytest.raises(AssertionError):
        fake_unregistered_code = "ask_hypothetical_new_unregistered_code"
        assert fake_unregistered_code in REGISTRY



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



