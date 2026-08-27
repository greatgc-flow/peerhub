from pathlib import Path

import pytest

from peerhub.application.api import AdmissionInputs, AdmissionInputsProvider
from peerhub.application.commands import AdmitDispatch
from peerhub.client import Client
from peerhub.core.context import PathLayout
from peerhub.core.ports import RequestContext
from peerhub.runtime import RuntimeContext, create_runtime
from tests.fakes import deterministic_uuid4


class FakeClock:
    def now(self) -> int:
        return 1000


class FakeIdSource:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def new_id(self, namespace: str) -> str:
        count = self._counts.get(namespace, 0) + 1
        self._counts[namespace] = count
        token = f"{namespace}-{count}"
        return deterministic_uuid4(token) if namespace == "outbox-event" else token


class FakeAdmissionProvider:
    def resolve(self, command: AdmitDispatch, caller: RequestContext) -> AdmissionInputs:
        class FakeInputs:
            route_request_factory = lambda snap: None
            dispatch_policy_revision = 1
            session_id = "sess-1"
            owner_principal_id = caller.principal
            owner_instance_id = "inst-1"
            authority_epoch = 1
            heartbeat_timeout_ms = 5000
            owner_peer_id = "peer-1"

        return FakeInputs()


@pytest.fixture
def runtime_setup(tmp_path: Path):
    layout = PathLayout.for_workspace(tmp_path)
    context = RuntimeContext("home-1", layout, FakeClock(), FakeIdSource())
    runtime = create_runtime(context, admission_provider=FakeAdmissionProvider())
    caller = RequestContext(principal="user-1", client_id="client-1")
    client = Client(runtime.application_api, caller=caller)
    yield runtime, client, caller
    runtime.close()
