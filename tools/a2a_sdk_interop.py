"""Interop check against the OFFICIAL a2a-sdk server (not an in-process fake).

Needs a virtualenv with `a2a-sdk[http-server]` and uvicorn:  python -m tools.a2a_sdk_interop <path-to-that-venv-python>
Starts tools/a2a_sdk_echo_server.py (SDK server classes, A2A 1.0 JSON-RPC on 127.0.0.1:18765), then drives HttpA2ATransport through
SendMessage -> GetTask -> reconcile by messageId -> TASK_NOT_FOUND, plus CancelTask. Exit 1 on any mismatch.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from peerhub.extensions.a2a import A2AAdapter, A2ATaskRequest, A2ATaskUnknownToRemoteError, AgentCard
from peerhub.extensions.a2a_http import HttpA2ATransport

URL = "http://127.0.0.1:18765/"
SERVER = Path(__file__).with_name("a2a_sdk_echo_server.py")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    server = subprocess.Popen([argv[0], "-B", str(SERVER)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(6)
        card = AgentCard("sdk", "SDK", "declared", ["echo"], URL)
        adapter = A2AAdapter(HttpA2ATransport(), timeout_s=10)
        adapter.register_agent_card(card)
        request = A2ATaskRequest("interop-1", "sdk", "echo", {"text": "hello"})
        sent = adapter.dispatch_task(request)
        assert sent.external_ref.remote_task_id and sent.external_ref.remote_task_id != "interop-1", "the server must assign the id"
        done = adapter.poll_task("interop-1")
        assert done.status == "COMPLETED" and done.output, done
        found = HttpA2ATransport().lookup(card, request, 10, remote_task_id=sent.external_ref.remote_task_id)
        assert found.status == "COMPLETED"
        try:
            HttpA2ATransport().lookup(card, request, 10, remote_task_id="no-such-task")
            raise AssertionError("an unknown remote task must raise")
        except A2ATaskUnknownToRemoteError:
            pass
        held = adapter.dispatch_task(A2ATaskRequest("interop-hold", "sdk", "echo", {"text": "hold"}))
        assert held.status in {"SUBMITTED", "RUNNING"}, held
        cancelled = adapter.cancel_task("interop-hold")
        assert cancelled.status == "CANCELLED", cancelled
        remote = HttpA2ATransport().poll(card, cancelled, 10)
        assert remote is not None and remote.status == "CANCELLED", remote
        assert remote.external_ref.opaque_state.get("state") == "TASK_STATE_CANCELED", remote
        print("PASS  a2a-sdk interop: SendMessage, GetTask, reconcile by messageId, TASK_NOT_FOUND, CancelTask")
        return 0
    except AssertionError as exc:
        print(f"FAIL  {exc}")
        return 1
    finally:
        server.terminate()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
