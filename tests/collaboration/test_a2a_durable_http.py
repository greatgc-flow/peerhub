"""M3.2: durable A2A evidence, restart/reconciliation, and ONE bounded external binding (A2A 1.0 JSON-RPC over HTTP).

The HTTP binding is exercised against an in-process server implementing the same JSON-RPC subset. Interoperability with
third-party A2A servers is not claimed by these tests.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from peerhub.core.models import Peer, Stream
from peerhub.core.store import CoreStore
from peerhub.extensions.a2a import (A2AAdapter, A2AExecutionUncertainError, A2AStateTransitionError, A2ATaskRequest,
                                    A2ATransportUnavailableError, AgentCard, LoopbackA2ATransport)
from peerhub.extensions.a2a_http import HttpA2ATransport
from peerhub.extensions.a2a_journal import A2AClaimLostError, A2AJournalError, A2ATaskJournal


class FakeRemote:
    """A minimal A2A 1.0 JSON-RPC server (SendMessage, GetTask, CancelTask) with server-assigned task ids."""

    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}
        self.by_message_id: dict[str, str] = {}
        self._next_task_id = 1
        self.sent = 0  # counts every SendMessage call, including failures and duplicate messages
        self.drop_next_send = False  # accept the task, then close the connection without answering (lost response)
        self.fail_next_send = False  # answer HTTP 500 WITHOUT accepting the task
        self.next_state: str | None = None  # force a TASK_STATE_* state on the next GetTask
        self.redirect = False
        self.huge = False
        self.bad_version = False  # jsonrpc "1.0"
        self.wrong_rpc_id = False
        self.wrong_task = False  # GetTask/CancelTask answer about someone else's task
        self.float_code = False  # error code -32001.9 instead of the integer -32001
        self.drip = 0.0  # seconds between 1-byte chunks of the body
        self._lock = threading.RLock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # silence
                pass

            def _reply(self, payload: dict[str, Any], status: int = 200) -> None:
                if "jsonrpc" in payload:
                    if owner.bad_version:
                        payload["jsonrpc"] = "1.0"
                    if owner.wrong_rpc_id:
                        payload["id"] = "someone-elses-request"
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                method, params, rid = body["method"], body["params"], body["id"]
                if method == "SendMessage":
                    with owner._lock:
                        owner.sent += 1
                if owner.redirect:
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:1/elsewhere")
                    self.end_headers()
                    return
                if owner.huge:
                    return self._reply({"jsonrpc": "2.0", "id": rid, "result": {"pad": "x" * 5000}})
                if owner.drip:
                    raw = json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"pad": "x" * 200}}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    try:
                        for byte in raw:
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                            time.sleep(owner.drip)
                    except OSError:  # the client closes the connection when its total deadline expires
                        pass
                    return
                if method == "SendMessage":
                    with owner._lock:
                        if owner.fail_next_send:
                            owner.fail_next_send = False
                            return self._reply({}, 500)
                        message = params["message"]
                        mid = message["messageId"]
                        remote_id = owner.by_message_id.get(mid)
                        if remote_id is None:
                            remote_id = f"srv-{owner._next_task_id}"
                            owner._next_task_id += 1
                            owner.by_message_id[mid] = remote_id
                            owner.tasks[remote_id] = {
                                "id": remote_id,
                                "contextId": "ctx-" + remote_id,
                                "status": {"state": "TASK_STATE_SUBMITTED"},
                                "history": [copy.deepcopy(message)],
                                "gets": 0,
                            }
                        task = owner.tasks[remote_id]
                        if owner.drop_next_send:
                            owner.drop_next_send = False
                            self.close_connection = True
                            return
                        result = {"task": self._view(task)}
                    return self._reply({"jsonrpc": "2.0", "id": rid, "result": result})
                if method not in ("GetTask", "CancelTask"):
                    return self._reply({"jsonrpc": "2.0", "id": rid,
                                        "error": {"code": -32601, "message": "Method not found"}})
                with owner._lock:
                    task = owner.tasks.get(params["id"])
                    if task is None:
                        code = -32001.9 if owner.float_code else -32001
                        return self._reply({"jsonrpc": "2.0", "id": rid,
                                            "error": {"code": code, "message": "Task not found"}})
                    if method == "CancelTask":
                        task["status"] = {"state": "TASK_STATE_CANCELED"}
                    else:
                        task["gets"] += 1
                        if owner.next_state is not None:
                            task["status"] = {"state": owner.next_state}
                            owner.next_state = None
                        elif task["status"]["state"] in ("TASK_STATE_SUBMITTED", "TASK_STATE_WORKING"):
                            task["status"] = {
                                "state": "TASK_STATE_WORKING" if task["gets"] == 1 else "TASK_STATE_COMPLETED"
                            }
                    result = self._view(task, wrong_task=owner.wrong_task)
                self._reply({"jsonrpc": "2.0", "id": rid, "result": result})

            @staticmethod
            def _view(task: dict[str, Any], *, wrong_task: bool = False) -> dict[str, Any]:
                view = {
                    "id": "someone-else" if wrong_task else task["id"],
                    "contextId": task["contextId"],
                    "status": dict(task["status"]),
                    "history": copy.deepcopy(task["history"]),
                }
                if task["status"]["state"] == "TASK_STATE_COMPLETED":
                    view["artifacts"] = [{"parts": [{"text": "done"}]}]
                return view

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/a2a"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def remote_id_of(self, local_id: str) -> str:
        with self._lock:
            return self.by_message_id[local_id]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def remote():
    r = FakeRemote()
    yield r
    r.close()


@pytest.fixture
def store(tmp_path: Path) -> CoreStore:
    s = CoreStore(tmp_path / "core.db")
    s.register_peer(Peer(peer_id="a2a"))
    s.create_stream(Stream(stream_id="a2a-evidence", members=["a2a"]))
    return s


def card(url: str) -> AgentCard:
    return AgentCard("remote", "Remote", "declared only", ["review"], url)


REQ = A2ATaskRequest("t1", "remote", "review", {"text": "hello"})


def adapter(url: str, store: CoreStore | None = None, transport: Any = None) -> A2AAdapter:
    journal = A2ATaskJournal(store, "a2a-evidence", "a2a") if store is not None else None
    a = A2AAdapter(transport or HttpA2ATransport(), timeout_s=5, journal=journal)
    a.register_agent_card(card(url))
    return a


# --------------------------------------------------------------------------------------------- HTTP binding
@pytest.mark.catalog_id("A2A-013")
def test_http_binding_runs_a_task_through_to_a_mapped_terminal_state(remote):
    a = adapter(remote.url)
    sub = a.dispatch_task(REQ)
    assert sub.status == "SUBMITTED"
    assert sub.external_ref.opaque_state == {"transport": "a2a-1.0-jsonrpc", "state": "TASK_STATE_SUBMITTED"}
    assert sub.task_id == "t1" and sub.external_ref.remote_task_id == remote.remote_id_of("t1") == "srv-1"
    assert "t1" not in remote.tasks
    assert remote.tasks["srv-1"]["history"] == [{
        "messageId": "t1",
        "role": "ROLE_USER",
        "parts": [{"data": {"action": "review", "input": {"text": "hello"}}}],
    }]
    assert a.poll_task("t1").status == "RUNNING"
    done = a.poll_task("t1")
    assert done.status == "COMPLETED" and done.output == {"artifacts": [{"parts": [{"text": "done"}]}]}
    assert a.dispatch_task(REQ).status == "COMPLETED" and remote.sent == 1  # duplicate dispatch reuses the binding, no second send


def test_http_server_deduplicates_send_message_by_message_id(remote):
    t = HttpA2ATransport()
    first = t.submit(card(remote.url), REQ, 5)
    repeated = t.submit(card(remote.url), REQ, 5)
    other = t.submit(card(remote.url), dataclasses.replace(REQ, task_id="t2"), 5)
    assert first.external_ref.remote_task_id == repeated.external_ref.remote_task_id == "srv-1"
    assert other.external_ref.remote_task_id == "srv-2"
    assert remote.by_message_id == {"t1": "srv-1", "t2": "srv-2"}
    assert set(remote.tasks) == {"srv-1", "srv-2"} and remote.sent == 3
    assert len(remote.tasks["srv-1"]["history"]) == 1


def test_http_cancel_is_confirmed_by_the_remote(remote):
    a = adapter(remote.url)
    a.dispatch_task(REQ)
    assert a.cancel_task("t1").status == "CANCELLED"
    assert remote.tasks[remote.remote_id_of("t1")]["status"]["state"] == "TASK_STATE_CANCELED"


def test_unmapped_remote_state_fails_closed(remote):
    a = adapter(remote.url)
    a.dispatch_task(REQ)
    remote.next_state = "TASK_STATE_INPUT_REQUIRED"
    with pytest.raises(A2AStateTransitionError, match="no PeerHub mapping"):
        a.poll_task("t1")
    assert a.get_agent_status("remote")["measured"]["is_verified"] is False  # nothing was measured or fabricated
    assert a.poll_task.__self__ is a and a._tasks["t1"].status == "SUBMITTED"  # the cached task kept its last valid status


def test_endpoint_policy_https_or_loopback_only_no_redirects_bounded_responses(remote):
    t = HttpA2ATransport()
    with pytest.raises(A2ATransportUnavailableError, match="https"):
        t.submit(card("http://example.invalid/a2a"), REQ, 1)  # remote plain http is refused before any network call
    with pytest.raises(A2ATransportUnavailableError):
        t.submit(card("file:///etc/passwd"), REQ, 1)
    remote.redirect = True
    with pytest.raises(A2ATransportUnavailableError, match="HTTP 302"):
        t.submit(card(remote.url), REQ, 5)  # a redirect could leave the declared endpoint: not followed
    remote.redirect, remote.huge = False, True
    with pytest.raises(A2AStateTransitionError, match="max_response_bytes"):
        HttpA2ATransport(max_response_bytes=100).submit(card(remote.url), REQ, 5)


# ----------------------------------------------------------------------------- uncertainty and reconciliation
def test_a_lost_response_is_uncertain_never_replayed_and_reconciles_to_the_remote_task(remote):
    a = adapter(remote.url)
    remote.drop_next_send = True
    with pytest.raises(A2AExecutionUncertainError):
        a.dispatch_task(REQ)
    assert a.uncertain_tasks() == ["t1"]
    with pytest.raises(A2AExecutionUncertainError):
        a.dispatch_task(REQ)  # not replayed
    assert remote.sent == 1
    with pytest.raises(A2AExecutionUncertainError, match="remote assigns task ids"):
        a.reconcile_task("t1")  # our messageId cannot identify the server-assigned task id
    assert a.uncertain_tasks() == ["t1"]
    assert remote.tasks[remote.remote_id_of("t1")]["gets"] == 0
    found = a.reconcile_task("t1", remote_task_id=remote.remote_id_of("t1"))
    assert found is not None and found.status == "RUNNING" and a.uncertain_tasks() == []  # the lookup already saw progress
    assert found.task_id == "t1" and found.external_ref.remote_task_id == remote.remote_id_of("t1")
    assert a.poll_task("t1").status == "COMPLETED" and remote.sent == 1  # and the task continues without a resend


def test_reconciliation_does_not_adopt_a_candidate_without_our_message_id_in_history(remote):
    a = adapter(remote.url)
    remote.drop_next_send = True
    with pytest.raises(A2AExecutionUncertainError):
        a.dispatch_task(REQ)
    other = dataclasses.replace(REQ, task_id="t2")
    a.dispatch_task(other)
    candidate = remote.remote_id_of("t2")
    assert all(message["messageId"] != "t1" for message in remote.tasks[candidate]["history"])
    with pytest.raises(A2AExecutionUncertainError, match="does not carry our messageId"):
        a.reconcile_task("t1", remote_task_id=candidate)
    assert a.uncertain_tasks() == ["t1"] and "t1" not in a._tasks
    with pytest.raises(A2AExecutionUncertainError):
        a.dispatch_task(REQ)
    assert remote.sent == 2
    found = a.reconcile_task("t1", remote_task_id=remote.remote_id_of("t1"))
    assert found is not None and found.external_ref.remote_task_id == remote.remote_id_of("t1")
    assert a.uncertain_tasks() == [] and remote.sent == 2


@pytest.mark.catalog_id("A2A-013")
def test_http_not_found_never_releases_the_id_it_can_only_be_abandoned_and_burned(remote):
    a = adapter(remote.url)
    remote.fail_next_send = True  # HTTP 500 before the remote accepted anything: still UNKNOWN to the client
    with pytest.raises(A2AExecutionUncertainError):
        a.dispatch_task(REQ)
    assert remote.tasks == {} and remote.by_message_id == {} and remote.sent == 1
    with pytest.raises(A2AExecutionUncertainError, match="cannot fence a late-arriving send"):
        a.reconcile_task("t1", remote_task_id="srv-nonexistent")  # GetTask says "not found", but a slow send could still arrive
    assert a.uncertain_tasks() == ["t1"]
    with pytest.raises(A2AStateTransitionError, match="reason"):
        a.abandon_task("t1", "  ")
    a.abandon_task("t1", "operator: remote confirmed it never ran it")
    assert a.uncertain_tasks() == []
    with pytest.raises(A2AStateTransitionError, match="abandoned"):
        a.dispatch_task(REQ)  # the burned id is never reused (a late send for it must not be bound to new input)
    other = A2ATaskRequest("t2", "remote", "review", {"input": "text"})
    assert a.dispatch_task(other).status == "SUBMITTED" and remote.sent == 2  # a NEW messageId is safe


def test_a_late_arriving_send_cannot_be_bound_to_a_reused_id(remote):
    a = adapter(remote.url)
    remote.drop_next_send = True
    with pytest.raises(A2AExecutionUncertainError):
        a.dispatch_task(REQ)
    a.abandon_task("t1", "operator decision")
    different = A2ATaskRequest("t1", "remote", "review", {"input": "DIFFERENT"})
    with pytest.raises(A2AStateTransitionError, match="abandoned"):
        a.dispatch_task(different)  # the remote still holds the OLD messageId; reuse would return the old input's result
    assert remote.sent == 1
    assert remote.tasks[remote.remote_id_of("t1")]["history"][0]["parts"] == [
        {"data": {"action": "review", "input": {"text": "hello"}}}
    ]


def test_a_deterministic_transport_that_proves_absence_still_releases_and_the_retry_is_a_new_attempt(store):
    transport = LoopbackA2ATransport()
    a = adapter("https://example.invalid/a2a", store, transport)
    journal = A2ATaskJournal(store, "a2a-evidence", "a2a")
    journal.record_submitting(REQ, "https://example.invalid/a2a")  # crashed before the (never made) send
    again = adapter("https://example.invalid/a2a", store, transport)
    assert again.uncertain_tasks() == ["t1"] and a is not again
    assert again.reconcile_task("t1") is None  # the simulated remote definitively never saw it
    assert again.dispatch_task(REQ).status == "SUBMITTED" and journal.attempts("t1") == 2


def test_reconcile_stays_uncertain_when_the_remote_cannot_answer(remote):
    a = adapter(remote.url)
    remote.drop_next_send = True
    with pytest.raises(A2AExecutionUncertainError):
        a.dispatch_task(REQ)
    remote.redirect = True  # the lookup itself fails: that proves nothing about the task
    with pytest.raises(A2ATransportUnavailableError):
        a.reconcile_task("t1", remote_task_id=remote.remote_id_of("t1"))
    assert a.uncertain_tasks() == ["t1"]
    with pytest.raises(A2AStateTransitionError):
        a.reconcile_task("never-seen")  # only uncertain tasks can be reconciled


# ---------------------------------------------------------------------------------------- durable restart
def test_task_state_survives_a_restart_without_resubmission(remote, store):
    a = adapter(remote.url, store)
    a.dispatch_task(REQ)
    a.poll_task("t1")  # RUNNING is durable
    again = adapter(remote.url, store)  # a new process over the same Records
    assert again.poll_task("t1").status == "COMPLETED"
    assert again.dispatch_task(REQ).status == "COMPLETED" and remote.sent == 1
    with pytest.raises(A2AStateTransitionError):
        again.update_task_status("t1", "RUNNING")  # terminal state restored and enforced
    third = adapter(remote.url, store)
    assert third.dispatch_task(REQ).status == "COMPLETED"  # terminal evidence is itself durable


@pytest.mark.catalog_id("A2A-013")
def test_a_crash_after_the_submitting_record_restores_as_uncertain_and_never_blind_replays(remote, store):
    journal = A2ATaskJournal(store, "a2a-evidence", "a2a")
    journal.record_submitting(REQ, remote.url)  # the process died right after this record, before/while the remote was called
    a = adapter(remote.url, store)
    assert a.uncertain_tasks() == ["t1"]
    with pytest.raises(A2AExecutionUncertainError):
        a.dispatch_task(REQ)
    assert remote.sent == 0  # nothing was sent: replay needs reconciliation first
    with pytest.raises(A2AExecutionUncertainError, match="cannot fence a late-arriving send"):
        a.reconcile_task("t1", remote_task_id="srv-nonexistent")  # HTTP cannot prove absence
    a.abandon_task("t1", "operator decision after checking the remote")
    fresh = adapter(remote.url, store)  # the abandonment is durable across a restart
    assert fresh.uncertain_tasks() == []
    with pytest.raises(A2AStateTransitionError, match="abandoned"):
        fresh.dispatch_task(REQ)


def test_an_unresolved_second_attempt_is_uncertain_after_restart_even_though_the_first_was_released(remote, store):
    journal = A2ATaskJournal(store, "a2a-evidence", "a2a")
    journal.record_submitting(REQ, remote.url)
    journal.record_not_started("t1", 1)
    journal.record_submitting(REQ, remote.url)  # attempt 2 started, outcome unknown
    assert adapter(remote.url, store).uncertain_tasks() == ["t1"]


def test_the_journal_requires_a_dedicated_single_author_stream(store):
    store.register_peer(Peer(peer_id="intruder"))
    store.create_stream(Stream(stream_id="shared", members=["a2a", "intruder"]))
    with pytest.raises(A2AJournalError):
        A2ATaskJournal(store, "shared", "a2a").record_submitting(REQ)


def test_loopback_transport_reconciles_what_it_accepted_and_denies_what_it_did_not(store):
    transport = LoopbackA2ATransport()
    a = adapter("https://example.invalid/a2a", store, transport)
    a.dispatch_task(REQ)
    assert transport.lookup(card("x"), REQ, 1).status == "SUBMITTED"
    other = A2ATaskRequest("t2", "remote", "review", {})
    from peerhub.extensions.a2a import A2ATaskUnknownToRemoteError
    with pytest.raises(A2ATaskUnknownToRemoteError):
        transport.lookup(card("x"), other, 1)


# ------------------------------------------------------------------ two adapters, stale not_started, terminal immutability
@pytest.mark.catalog_id("A2A-013")
def test_two_adapters_over_one_journal_cannot_both_send_the_same_task(remote, store):
    first, second = adapter(remote.url, store), adapter(remote.url, store)
    assert first.dispatch_task(REQ).status == "SUBMITTED"
    again = second.dispatch_task(REQ)  # `second` loaded its cache BEFORE `first` sent: it must adopt, not send
    assert again.status == "SUBMITTED" and remote.sent == 1


def test_a_same_attempt_claim_race_has_exactly_one_winner(store):
    one, two = A2ATaskJournal(store, "a2a-evidence", "a2a"), A2ATaskJournal(store, "a2a-evidence", "a2a")
    two.state_of = lambda task_id: None  # type: ignore[method-assign]  # `two` read the journal before `one` wrote
    two._attempts = lambda task_id: 0  # type: ignore[method-assign]
    assert one.claim_submission(REQ, "e") == 1
    with pytest.raises(A2AClaimLostError):
        two.claim_submission(REQ, "e")  # same attempt number, different claim token: a key conflict, never a silent duplicate


def test_an_old_not_started_record_cannot_erase_a_newer_pending_attempt(store):
    journal = A2ATaskJournal(store, "a2a-evidence", "a2a")
    journal.record_submitting(REQ, "e")
    journal.record_not_started("t1", 1)
    journal.record_submitting(REQ, "e")  # attempt 2 pending
    journal.record_not_started("t1", 1)  # a stale reconciler repeats the attempt-1 verdict
    state = journal.state_of("t1")
    assert state is not None and state.attempt == 2 and state.response is None  # attempt 2 is still unresolved


@pytest.mark.catalog_id("A2A-013")
def test_terminal_evidence_is_immutable_and_conflicting_evidence_is_not_swallowed(remote, store):
    a = adapter(remote.url, store)
    a.dispatch_task(REQ)
    a.poll_task("t1")
    done = a.poll_task("t1")
    assert done.status == "COMPLETED"
    journal = A2ATaskJournal(store, "a2a-evidence", "a2a")
    failed = dataclasses.replace(done, status="FAILED", error="late contradictory report")
    with pytest.raises(A2AStateTransitionError, match="immutable"):
        journal.record_observed(failed)  # a stale writer cannot replace a terminal outcome
    journal.record_observed(done)  # the same terminal evidence again is an idempotent no-op
    assert adapter(remote.url, store).poll_task("t1").status == "COMPLETED"
    with pytest.raises(A2AJournalError):
        journal.record_observed(done, attempt=99)  # evidence for another attempt is refused


# ------------------------------------------------------------------------------- binding: identity, codes, endpoint, deadline
@pytest.mark.parametrize("mode", ["bad_version", "wrong_rpc_id"])
def test_a_response_that_is_not_the_answer_to_this_request_is_rejected(remote, mode):
    setattr(remote, mode, True)
    a = adapter(remote.url)
    with pytest.raises(A2AExecutionUncertainError):  # the send may have happened, but the answer is not trusted
        a.dispatch_task(REQ)
    assert a.uncertain_tasks() == ["t1"]
    assert remote.sent == 1 and remote.remote_id_of("t1") == "srv-1"


def test_a_poll_response_about_a_different_task_is_rejected(remote):
    a = adapter(remote.url)
    submitted = a.dispatch_task(REQ)
    remote.wrong_task = True
    with pytest.raises(A2AStateTransitionError, match="different task"):
        a.poll_task("t1")
    assert a._tasks["t1"] == submitted
    assert a.uncertain_tasks() == [] and remote.sent == 1


def test_a_malformed_error_code_is_not_task_not_found(remote):
    a = adapter(remote.url)
    remote.drop_next_send = True
    with pytest.raises(A2AExecutionUncertainError):
        a.dispatch_task(REQ)
    remote_id = remote.remote_id_of("t1")
    remote.tasks.clear()  # now the lookup really gets "not found"...
    remote.float_code = True  # ...but with a malformed (non-integer) code
    with pytest.raises(A2AStateTransitionError, match="integer code"):
        a.reconcile_task("t1", remote_task_id=remote_id)
    assert a.uncertain_tasks() == ["t1"]


def test_reconciliation_cannot_be_redirected_to_a_different_endpoint(remote, store):
    a = adapter(remote.url, store)
    remote.drop_next_send = True
    with pytest.raises(A2AExecutionUncertainError):
        a.dispatch_task(REQ)
    remote_id = remote.remote_id_of("t1")
    other = FakeRemote()
    try:
        a.register_agent_card(card(other.url))  # same agent id, another endpoint
        with pytest.raises(A2AStateTransitionError, match="submitted to"):
            a.reconcile_task("t1", remote_task_id=remote_id)
        restarted = adapter(other.url, store)  # and after a restart the binding is still the original endpoint
        with pytest.raises(A2AStateTransitionError, match="submitted to"):
            restarted.reconcile_task("t1", remote_task_id=remote_id)
        assert other.sent == 0 and other.tasks == {}
    finally:
        other.close()


@pytest.mark.catalog_id("A2A-013")
def test_the_timeout_is_a_total_deadline_not_a_per_read_timeout(remote):
    remote.drip = 0.02  # one byte every 20 ms: every individual read is fast, the whole body takes seconds
    started = time.monotonic()
    with pytest.raises(A2ATransportUnavailableError, match="deadline"):
        HttpA2ATransport().submit(card(remote.url), REQ, 0.3)
    assert time.monotonic() - started < 2.0
