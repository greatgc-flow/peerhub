"""Tests for ratified backlog item J2 -- codex reset-credit read/consume.

Mirrors this repo's existing test_quota_polling.py FakeProc pattern for
mocking the codex app-server's threaded stdin/stdout JSON-RPC exchange,
rather than inventing a new mocking style.
"""

import json
import subprocess

import pytest

from peerhub.telemetry.codex_credit import (
    CodexCreditError,
    ConsumeResult,
    consume_reset_credit,
    read_reset_credits,
)


class _FakeStdin:
    def __init__(self, sent: list[str]) -> None:
        self._sent = sent

    def write(self, data: str) -> None:
        self._sent.append(data)

    def flush(self) -> None:
        pass


class _ScriptedProc:
    """A fake Popen replying with one preloaded response line per readline()
    call, in a fixed order -- the same convention this repo's existing
    test_poll_codex_usage_malformed_response uses (a FakeStdout backed by a
    fixed list, popped in order), rather than trying to correlate replies
    to requests by content. ``response_sequence`` lists the "result"
    payload for each expected request IN THE ORDER THE CODE UNDER TEST WILL
    ISSUE THEM -- id 0 (initialize) is always first and implicit.
    """

    def __init__(self, response_sequence: list[dict]) -> None:
        # Real request ids: 0 = initialize (always {}), then 2, 3, 4, ...
        # (id 1 is reserved-but-unused by _CodexAppServerSession's own
        # numbering, matching quota_polling.py's identical convention).
        ids = [0] + list(range(2, 2 + len(response_sequence)))
        results = [{}] + response_sequence
        self._lines = [
            json.dumps({"id": rid, "result": result}) + "\n"
            for rid, result in zip(ids, results)
        ]
        self.sent: list[str] = []
        self.stdin = _FakeStdin(self.sent)
        self.stdout = self
        self.pid = 9999

    def readline(self) -> str:
        if self._lines:
            return self._lines.pop(0)
        return ""

    def poll(self) -> None:
        return None


def _credits_result(available_count: int, credits: list[dict]) -> dict:
    return {"rateLimitResetCredits": {"availableCount": available_count, "credits": credits}}


def test_read_reset_credits_parses_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _ScriptedProc([_credits_result(1, [
        {"id": "cred-1", "status": "available", "expiresAt": None},
    ])])
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: proc)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: None)
    monkeypatch.setattr(
        "peerhub.telemetry.codex_credit._real_command",
        lambda _peer, _sys_dir=None: ["dummy.exe"],
    )

    summary = read_reset_credits()

    assert summary is not None
    assert summary.available_count == 1
    assert summary.credits[0].credit_id == "cred-1"
    assert summary.credits[0].status == "available"


def test_read_reset_credits_returns_none_when_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "peerhub.telemetry.codex_credit._real_command",
        lambda _peer, _sys_dir=None: None,
    )

    with pytest.raises(CodexCreditError, match="codex binary not found"):
        read_reset_credits()


def test_consume_reset_credit_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # id 2 = pre-read (available), id 3 = consume RPC ack, id 4 = post-read
    # (decremented, no longer available) -- the full validate/consume/verify
    # sequence hub.py's action_credit_consume uses.
    proc = _ScriptedProc([
        _credits_result(1, [{"id": "cred-1", "status": "available", "expiresAt": None}]),
        {},
        _credits_result(0, [{"id": "cred-1", "status": "consumed", "expiresAt": None}]),
    ])
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: proc)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: None)
    monkeypatch.setattr(
        "peerhub.telemetry.codex_credit._real_command",
        lambda _peer, _sys_dir=None: ["dummy.exe"],
    )

    result = consume_reset_credit("cred-1", "idem-1")

    assert result == ConsumeResult(consumed=True, credit_id="cred-1", reason="ok")
    # The consume RPC really was sent, with the right params.
    consume_calls = [
        json.loads(s) for s in proc.sent
        if json.loads(s).get("method") == "account/rateLimitResetCredit/consume"
    ]
    assert len(consume_calls) == 1
    assert consume_calls[0]["params"] == {"creditId": "cred-1", "idempotencyKey": "idem-1"}


def test_consume_reset_credit_rejects_unavailable_credit_without_sending_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Only the pre-read (id 2) is scripted; if the implementation
    # incorrectly sent a consume RPC anyway, there would be no response id
    # 3 for it to receive and the test would hang/timeout instead of
    # returning promptly -- proving the RPC really was never sent.
    proc = _ScriptedProc([
        _credits_result(1, [{"id": "cred-1", "status": "expired", "expiresAt": None}]),
    ])
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: proc)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: None)
    monkeypatch.setattr(
        "peerhub.telemetry.codex_credit._real_command",
        lambda _peer, _sys_dir=None: ["dummy.exe"],
    )

    result = consume_reset_credit("cred-1", "idem-1")

    assert result == ConsumeResult(consumed=False, credit_id="cred-1", reason="not_available")
    consume_calls = [
        json.loads(s) for s in proc.sent
        if json.loads(s).get("method") == "account/rateLimitResetCredit/consume"
    ]
    assert consume_calls == []


def test_consume_reset_credit_flags_unverified_if_post_read_disagrees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pre-read says available, consume RPC "succeeds", but the post-read
    # still shows the credit available and the count unchanged -- the
    # vendor's own success response is not trusted on its own.
    proc = _ScriptedProc([
        _credits_result(1, [{"id": "cred-1", "status": "available", "expiresAt": None}]),
        {},
        _credits_result(1, [{"id": "cred-1", "status": "available", "expiresAt": None}]),
    ])
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: proc)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: None)
    monkeypatch.setattr(
        "peerhub.telemetry.codex_credit._real_command",
        lambda _peer, _sys_dir=None: ["dummy.exe"],
    )

    result = consume_reset_credit("cred-1", "idem-1")

    assert result == ConsumeResult(consumed=False, credit_id="cred-1", reason="unverified")
