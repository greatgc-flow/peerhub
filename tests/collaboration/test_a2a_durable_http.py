"""M3.2: durable A2A evidence, restart/reconciliation, and ONE bounded external binding (JSON-RPC over HTTP).

The HTTP binding is exercised against an in-process server implementing the same JSON-RPC subset. Interoperability with
third-party A2A servers is not claimed by these tests.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from peerhub.core.models import Peer, Stream
from peerhub.core.store import CoreStore
from peerhub.extensions.a2a import (A2AAdapter, A2AExecutionUncertainError, A2AStateTransitionError, A2ATaskRequest,
                                    A2ATransportUnavailableError, AgentCard, LoopbackA2ATransport)
from peerhub.extensions.a2a_http import HttpA2ATransport
from peerhub.extensions.a2a_journal import A2AJournalError, A2ATaskJournal


class FakeRemote:
    """A minimal in-process A2A JSON-RPC server (tasks/send, tasks/get, tasks/cancel)."""

    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}
        self.sent = 0
        self.drop_next_send = False  # accept the task, then close the connection without answering (lost response)
        self.fail_next_send = False  # answer HTTP 500 WITHOUT accepting the task
        self.next_state: str | None = None  # force an unmapped state on the next get
        self.redirect = False
        self.huge = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # silence
                pass

            def _reply(self, payload: dict[str, Any], status: int = 200) -> None:
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                method, params, rid = body["method"], body["params"], body["id"]
                if owner.redirect:
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:1/elsewhere")
                    self.end_headers()
                    return
                if owner.huge:
                    return self._reply({"jsonrpc": "2.0", "id": rid, "result": {"pad": "x" * 5000}})
                if method == "tasks/send":
                    if owner.fail_next_send:
                        owner.fail_next_send = False
                        return self._reply({}, 500)
                    owner.sent += 1
                    task = owner.tasks.setdefault(params["id"], {"id": params["id"], "contextId": "ctx-" + params["id"],
                                                                 "status": {"state": "submitted"}, "gets": 0})
                    if owner.drop_next_send:
                        owner.drop_next_send = False
                        self.close_connection = True
                        return
                    return self._reply({"jsonrpc": "2.0", "id": rid, "result": self._view(task)})
                task = owner.tasks.get(params["id"])
                if task is None:
                    return self._reply({"jsonrpc": "2.0", "id": rid, "error": {"code": -32001, "message": "Task not found"}})
                if method == "tasks/cancel":
                    task["status"] = {"state": "canceled"}
                elif method == "tasks/get":
                    task["gets"] += 1
                    if owner.next_state:
                        task["status"] = {"state": owner.next_state}
                    elif task["status"]["state"] in ("submitted", "working"):
                        task["status"] = {"state": "working" if task["gets"] == 1 else "completed"}
                self._reply({"jsonrpc": "2.0", "id": rid, "result": self._view(task)})

            @staticmethod
            def _view(task: dict[str, Any]) -> dict[str, Any]:
                view = {"id": task["id"], "contextId": task["contextId"], "status": dict(task["status"])}
                if task["status"]["state"] == "completed":
                    view["artifacts"] = [{"parts": [{"kind": "text", "text": "done"}]}]
                return view

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/a2a"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

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
def test_http_binding_runs_a_task_through_to_a_mapped_terminal_state(remote):
    a = adapter(remote.url)
    sub = a.dispatch_task(REQ)
    assert sub.status == "SUBMITTED" and sub.external_ref.opaque_state == {"transport": "a2a-jsonrpc-subset", "state": "submitted"}
    assert a.poll_task("t1").status == "RUNNING"
    done = a.poll_task("t1")
    assert done.status == "COMPLETED" and done.output == {"artifacts": [{"parts": [{"kind": "text", "text": "done"}]}]}
    assert a.dispatch_task(REQ).status == "COMPLETED" and remote.sent == 1  # duplicate dispatch reuses the binding, no second send


def test_http_cancel_is_confirmed_by_the_remote(remote):
    a = adapter(remote.url)
    a.dispatch_task(REQ)
    assert a.cancel_task("t1").status == "CANCELLED" and remote.tasks["t1"]["status"]["state"] == "canceled"


def test_unmapped_remote_state_fails_closed(remote):
    a = adapter(remote.url)
    a.dispatch_task(REQ)
    remote.next_state = "input-required"
    with pytest.raises(A2AStateTransitionError, match="no PeerHub mapping"):
        a.poll_task("t1")
    assert a.get_agent_status("remote")  # nothing was fabricated


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
    found = a.reconcile_task("t1")  # the remote durably has the task
    assert found is not None and found.status == "RUNNING" and a.uncertain_tasks() == []  # the lookup already saw progress
    assert a.poll_task("t1").status == "COMPLETED" and remote.sent == 1  # and the task continues without a resend


def test_reconcile_releases_the_id_only_when_the_remote_definitively_never_saw_it(remote):
    a = adapter(remote.url)
    remote.fail_next_send = True  # HTTP 500 before the remote accepted anything: still UNKNOWN to the client
    with pytest.raises(A2AExecutionUncertainError):
        a.dispatch_task(REQ)
    assert a.reconcile_task("t1") is None  # tasks/get says "not found": definitively not started
    assert a.uncertain_tasks() == []
    assert a.dispatch_task(REQ).status == "SUBMITTED" and remote.sent == 1  # a retry is now safe


def test_reconcile_stays_uncertain_when_the_remote_cannot_answer(remote):
    a = adapter(remote.url)
    remote.drop_next_send = True
    with pytest.raises(A2AExecutionUncertainError):
        a.dispatch_task(REQ)
    remote.redirect = True  # the lookup itself fails: that proves nothing about the task
    with pytest.raises(A2ATransportUnavailableError):
        a.reconcile_task("t1")
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


def test_a_crash_after_the_submitting_record_restores_as_uncertain_and_never_blind_replays(remote, store):
    journal = A2ATaskJournal(store, "a2a-evidence", "a2a")
    journal.record_submitting(REQ)  # the process died right after this record, before/while the remote was called
    a = adapter(remote.url, store)
    assert a.uncertain_tasks() == ["t1"]
    with pytest.raises(A2AExecutionUncertainError):
        a.dispatch_task(REQ)
    assert remote.sent == 0  # nothing was sent: replay needs reconciliation first
    assert a.reconcile_task("t1") is None  # the remote never saw it -> released
    assert a.dispatch_task(REQ).status == "SUBMITTED"
    assert journal.attempts("t1") == 2  # the retry is a NEW durable attempt
    fresh = adapter(remote.url, store)
    assert fresh.uncertain_tasks() == [] and fresh.dispatch_task(REQ).status == "SUBMITTED" and remote.sent == 1


def test_an_unresolved_second_attempt_is_uncertain_after_restart_even_though_the_first_was_released(remote, store):
    journal = A2ATaskJournal(store, "a2a-evidence", "a2a")
    journal.record_submitting(REQ)
    journal.record_not_started("t1", 1)
    journal.record_submitting(REQ)  # attempt 2 started, outcome unknown
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
