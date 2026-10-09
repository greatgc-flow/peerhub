"""Local paid canaries: five bounded invocations per peer, no retries or quota probes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from peerhub.extensions.adapters.base import SPECS, resolve_binary
from tests.communication.live.ask_support import ASK_TIMEOUT, ask_args, assert_measured_response, running_session
from tests.communication.live.live_support import ROOT

pytestmark = [pytest.mark.canary, pytest.mark.provider, pytest.mark.timeout(600)]
PEERS = [pytest.param(peer, marks=getattr(pytest.mark, peer)) for peer in ("cc", "cx", "ag")]


@pytest.fixture(params=PEERS)
def peer(request):
    kind = request.param
    if resolve_binary(SPECS[kind]) is None:
        pytest.skip(f"LIVE-PROVIDER-UNAVAILABLE[provider={kind}]: binary absent")
    return kind


def _ask(peer, workspace, prompt, request_id, **flags):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    run = subprocess.run([sys.executable, "-m", "peerhub", *ask_args(peer, workspace, prompt, request_id, **flags)],
                         cwd=workspace, env=env, capture_output=True, text=True, encoding="utf-8",
                         timeout=ASK_TIMEOUT + 60)
    assert run.returncode == 0, run.stdout + run.stderr
    result = json.loads(run.stdout)
    assert result["status"] == "delivered" and result["certainty"] == "TERMINAL", result
    return result


def test_basic_ask_reports_text_and_measured_usage(tmp_path, peer):
    assert_measured_response(_ask(peer, tmp_path, "Reply with OK only.", "basic"))


def test_resume_recalls_first_turn_token(tmp_path, peer):
    token = "canary_" + uuid.uuid4().hex
    first = _ask(peer, tmp_path, f"Remember this token: {token}. Reply with OK only.", "remember", resume=True)
    second = _ask(peer, tmp_path, "Return only the token I asked you to remember.", "recall", resume=True)
    assert second["effective_mode"] == "resumed" and second["fallback_reason"] is None, second
    assert second["session_generation"] == first["session_generation"]
    assert second["injected_record_ids"] == []  # native memory, not fresh Stream catch-up
    assert token in second["response"], second


def test_writable_ask_creates_sentinel(tmp_path, peer):
    token = uuid.uuid4().hex
    sentinel = tmp_path / "sentinel.txt"
    assert not sentinel.exists()
    _ask(peer, tmp_path, f"Create sentinel.txt in the current workspace containing exactly {token}. "
         "Do not edit any other file. Reply with OK only.", "sentinel", writable=True)
    assert sentinel.is_file() and sentinel.read_text(encoding="utf-8").strip() == token


def test_cancel_running_ask(tmp_path, peer, monkeypatch, capsys):
    # Invoke the public CLI in-process so its actual runtime can receive durable Bridge control.
    # There is no public CLI cancel command. Only construction is observed; provider delivery is real.
    from peerhub.cli import main
    from peerhub.core.models import utc_now_iso
    from peerhub.core.store import CoreStore
    from peerhub.core.workspace import Workspace
    from peerhub.extensions import ask as ask_module
    from peerhub.extensions.bridge import Bridge
    from peerhub.extensions.bridge_claims import ClaimStore

    real_target = ask_module.CliRuntimeTarget
    targets = []

    def capture_target(*args, **kwargs):
        target = real_target(*args, **kwargs)
        targets.append(target)
        return target

    monkeypatch.setattr(ask_module, "CliRuntimeTarget", capture_target)
    args = ask_args(peer, tmp_path, "Keep thinking about a short proof that there are infinitely many primes. "
                    "Do not use tools. Finish with OK.", "cancel")
    db = tmp_path / "core.db"
    session = None
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(main, args)
        try:
            deadline = time.monotonic() + 60
            while session is None and not future.done() and time.monotonic() < deadline:
                session = running_session(db, peer)
                if session is None:
                    time.sleep(0.05)
            assert session is not None and not future.done(), "ask did not remain running until cancellation"
            store = CoreStore(db)
            control = store.append_record(stream_id="live-measurement", author_peer_id="user",
                                          kind="control.cancel", body={}, targets=[peer],
                                          idempotency_key="cancel-control", created_at=utc_now_iso())
            bridge = Bridge(store, ClaimStore(db, generation=Workspace(tmp_path).generation), owner_id="live-cancel")
            outcome = bridge.handle_control(control.record_id, targets[0], peer)
            assert outcome.runtime_outcome == "done", outcome
            assert future.result(timeout=30) == 1
            result = json.loads(capsys.readouterr().out)
            assert result["status"] not in ("delivered", "recovered_terminal") and result["response_record_id"] is None
            assert any(e["event"] == "terminated" for e in targets[0].evidence)
        finally:
            # Failure cleanup also handles a spawn that raced the STARTED poll.
            for target in targets:
                with target._active_lock:
                    active = list(target._active.values())
                for process in active:
                    process.abort()
            future.result(timeout=ASK_TIMEOUT + 60)
