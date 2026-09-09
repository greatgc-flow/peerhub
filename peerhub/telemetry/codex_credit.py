"""Codex rate-limit reset-credit read/consume operations.

Ratified backlog item J2 (``docs/reviews/p-drive-mece-migration-audit-
2026-09-09.md`` section 5): peerhub already has the vendor-coupled half of
this (``quota_polling.py``'s ``poll_codex_usage`` opens the same codex
app-server session and even reuses the ``"hub-credit"`` client identity
string P:'s own ``hub.py`` uses) and a ``Capability`` enum
(``adapters/contract.py``) to gate vendor-specific features -- only the
actual read/consume RPC methods were missing. Consuming a reset credit is
a real, limited, spend-once vendor resource, so this is intentionally NOT
wired into a periodic poller the way quota READING is; it is only ever
invoked by an explicit, human-confirmed action -- mirroring hub.py's own
``CodexAccountClient``/``action_credit_consume`` design (parity target;
this module is peerhub's own idiom, not a port).

CLI exposure is deliberately out of scope for this pass: there is no
existing ``peerhub credit`` (or similar) subcommand to extend, and
picking that command surface is its own design decision this backlog
item did not ask for -- these functions are the missing primitive, ready
for a CLI layer to call. A manual ``codex`` CLI invocation remains the
fallback in the meantime (this is a P2 item precisely because that
fallback exists).
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, cast

# Deliberate reuse of quota_polling.py's own binary/sys-dir resolution
# rather than re-implementing it -- these two helpers are private to that
# module (not a stable public API), so the reuse is explicitly
# acknowledged here rather than silently suppressed.
from peerhub.telemetry.quota_polling import (
    _real_command,  # pyright: ignore[reportPrivateUsage]
    _resolve_sys_dir,  # pyright: ignore[reportPrivateUsage]
)

_CLIENT_INFO = {"name": "hub-credit", "version": "1.0"}


class CodexCreditError(RuntimeError):
    """A codex app-server credit operation could not complete."""


@dataclass(frozen=True)
class ResetCredit:
    credit_id: str
    status: str
    expires_at: float | None


@dataclass(frozen=True)
class ResetCreditSummary:
    available_count: int
    credits: tuple[ResetCredit, ...]


@dataclass(frozen=True)
class ConsumeResult:
    consumed: bool
    credit_id: str
    reason: str
    """One of "ok", "not_available" (failed the pre-check, never sent the
    RPC), or "unverified" (RPC was sent but the post-read did not
    corroborate the vendor actually spent it)."""


class _CodexAppServerSession:
    """One short-lived codex app-server JSON-RPC session.

    Handshake mirrors P:'s hub.py ``CodexAccountClient`` exactly (same
    ``clientInfo`` identity, same initialize/initialized sequence) so a
    quota dashboard correlating sessions by client identity sees one
    consistent actor -- read-only parity target, not ported code.
    """

    def __init__(self, sys_dir: Optional[Path] = None, deadline_sec: float = 12.0) -> None:
        self._sys_dir = _resolve_sys_dir(sys_dir)
        self._deadline_sec = deadline_sec
        self._proc: subprocess.Popen[str] | None = None
        self._queue: "queue.Queue[str | None]" = queue.Queue()
        self._next_id = 2  # 0=initialize, 1 reserved for this session's first read

    def __enter__(self) -> "_CodexAppServerSession":
        codex_cmd = _real_command("cx", self._sys_dir)
        if not codex_cmd:
            raise CodexCreditError("codex binary not found")
        self._proc = subprocess.Popen(
            [*codex_cmd, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )

        def _reader() -> None:
            proc = self._proc
            assert proc is not None
            try:
                if proc.stdout is not None:
                    while True:
                        line = proc.stdout.readline()
                        if not line or proc.poll() is not None:
                            break
                        self._queue.put(line)
            except Exception:
                pass
            self._queue.put(None)

        threading.Thread(target=_reader, daemon=True).start()
        try:
            self._send({"id": 0, "method": "initialize", "params": {
                "clientInfo": _CLIENT_INFO,
                "capabilities": {"experimentalApi": True},
            }})
            self._recv(0)
            self._send({"method": "initialized"})
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *_exc: object) -> None:
        # Returning None (falsy) rather than a plain `bool` -- an
        # annotation of `bool` makes type-checkers conservatively assume
        # __exit__ might suppress an exception raised inside the `with`
        # block, which then flags every variable first assigned there as
        # "possibly unbound" at every later use. This session's own
        # dispatches hit exactly that false positive.
        self.close()

    def _send(self, obj: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise CodexCreditError("codex app-server session is not open")
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def _recv(self, expected_id: int) -> dict[str, Any]:
        deadline = time.monotonic() + self._deadline_sec
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CodexCreditError(
                    f"codex app-server did not respond to id={expected_id} in time"
                )
            try:
                line = self._queue.get(timeout=min(0.5, remaining))
            except queue.Empty:
                continue
            if line is None:
                raise CodexCreditError(
                    f"codex app-server closed before responding to id={expected_id}"
                )
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if obj.get("id") == expected_id:
                result = obj.get("result")
                if isinstance(result, dict):
                    return cast(dict[str, Any], result)
                raise CodexCreditError(
                    f"codex app-server error for id={expected_id}: {obj.get('error')}"
                )

    def read_rate_limits(self) -> dict[str, Any]:
        rid = self._next_id
        self._next_id += 1
        self._send({"id": rid, "method": "account/rateLimits/read", "params": None})
        return self._recv(rid)

    def consume_reset_credit_rpc(self, *, credit_id: str, idempotency_key: str) -> dict[str, Any]:
        rid = self._next_id
        self._next_id += 1
        self._send({
            "id": rid,
            "method": "account/rateLimitResetCredit/consume",
            "params": {"creditId": credit_id, "idempotencyKey": idempotency_key},
        })
        return self._recv(rid)

    def close(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, timeout=10,
                )
            except Exception:
                pass


def _parse_reset_credit_summary(result: dict[str, Any] | None) -> ResetCreditSummary | None:
    if not isinstance(result, dict):
        return None
    summary_raw = result.get("rateLimitResetCredits")
    if not isinstance(summary_raw, dict):
        return None
    summary = cast(dict[str, Any], summary_raw)
    raw_credits = summary.get("credits")
    if not isinstance(raw_credits, list):
        return None
    entries = cast(list[Any], raw_credits)
    parsed_credits: list[ResetCredit] = []
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            continue
        entry = cast(dict[str, Any], raw_entry)
        credit_id = entry.get("id")
        if credit_id is None:
            continue
        expires_at_raw = entry.get("expiresAt")
        parsed_credits.append(ResetCredit(
            credit_id=str(credit_id),
            status=str(entry.get("status")),
            expires_at=float(expires_at_raw) if isinstance(expires_at_raw, (int, float)) else None,
        ))
    credits = tuple(parsed_credits)

    available_count_raw = summary.get("availableCount")
    available_count = (
        available_count_raw
        if isinstance(available_count_raw, (int, float))
        else sum(1 for c in credits if c.status == "available")
    )
    return ResetCreditSummary(available_count=int(available_count), credits=credits)


def read_reset_credits(sys_dir: Optional[Path] = None) -> ResetCreditSummary | None:
    """Read-only view of codex's rate-limit reset credits. Never consumes.

    Returns ``None`` if the app-server could not be reached or the
    response did not carry a recognizable credit summary -- fails closed,
    same as ``quota_polling.py``'s pollers, rather than fabricating data.
    """
    with _CodexAppServerSession(sys_dir) as session:
        result = session.read_rate_limits()
    return _parse_reset_credit_summary(result)


def _validate_reset_credit(summary: ResetCreditSummary | None, credit_id: str) -> bool:
    """Mirrors hub.py's ``_validate_reset_credit``: True iff ``credit_id`` is
    present with status "available" and not expired. Missing/malformed
    summary, unknown id, wrong status, or expiry all reject."""
    if summary is None:
        return False
    now = time.time()
    for credit in summary.credits:
        if credit.credit_id != credit_id:
            continue
        if credit.status != "available":
            return False
        if credit.expires_at is not None and credit.expires_at <= now:
            return False
        return True
    return False


def _verify_reset_credit_consumed(
    pre: ResetCreditSummary | None,
    post: ResetCreditSummary | None,
    credit_id: str,
) -> bool:
    """Mirrors hub.py's ``_verify_reset_credit``: a fresh post-consume read
    must corroborate the credit was actually spent (available count
    decremented by at least 1 AND the credit absent or no longer
    "available"), rather than trusting the consume RPC's own response."""
    if pre is None or post is None:
        return False
    if post.available_count > pre.available_count - 1:
        return False
    for credit in post.credits:
        if credit.credit_id == credit_id and credit.status == "available":
            return False
    return True


def consume_reset_credit(
    credit_id: str,
    idempotency_key: str,
    sys_dir: Optional[Path] = None,
) -> ConsumeResult:
    """Consume one codex rate-limit reset credit.

    Pre-checks availability, sends the consume RPC only if that check
    passes, then re-reads to verify the vendor actually spent it -- the
    same three-step safety sequence as hub.py's ``action_credit_consume``
    (validate -> consume -> verify), never trusting a single RPC round
    trip for an operation that spends a real, limited resource.
    """
    with _CodexAppServerSession(sys_dir) as session:
        pre = _parse_reset_credit_summary(session.read_rate_limits())
        if not _validate_reset_credit(pre, credit_id):
            return ConsumeResult(consumed=False, credit_id=credit_id, reason="not_available")
        session.consume_reset_credit_rpc(credit_id=credit_id, idempotency_key=idempotency_key)
        post = _parse_reset_credit_summary(session.read_rate_limits())

    if not _verify_reset_credit_consumed(pre, post, credit_id):
        return ConsumeResult(consumed=False, credit_id=credit_id, reason="unverified")
    return ConsumeResult(consumed=True, credit_id=credit_id, reason="ok")
