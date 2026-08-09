import pytest
from pathlib import Path
import time

from peerhub.application.direct_ask import execute_direct_ask, DirectAskRequest, DirectAskResult
from peerhub.core.execution import TransportLimits
from peerhub.dispatch.contract import RequestState
from peerhub.core.context import Clock, IdSource

class DummyClock:
    def now(self) -> int:
        return int(time.time() * 1000)

import uuid

class DummyIds:
    def request_id(self) -> str: return str(uuid.uuid4())
    def process_spawn_id(self) -> str: return str(uuid.uuid4())
    def session_id(self) -> str: return str(uuid.uuid4())
    def attempt_id(self) -> str: return str(uuid.uuid4())
    def route_decision_id(self) -> str: return str(uuid.uuid4())
    def delivery_receipt_id(self) -> str: return str(uuid.uuid4())
    def transition_receipt_id(self) -> str: return str(uuid.uuid4())
    
    def new_id(self, prefix: str) -> str:
        # Some consumers like outbox event strictly require an RFC4122 UUIDv4
        return str(uuid.uuid4())

@pytest.fixture
def clock() -> Clock:
    return DummyClock()

@pytest.fixture
def ids() -> IdSource:
    return DummyIds()


@pytest.mark.slow
def test_execute_direct_ask_real_agy(tmp_path: Path, clock: Clock, ids: IdSource) -> None:
    request = DirectAskRequest(
        workspace_root=tmp_path,
        peer_name="ag",
        prompt="say hello in two words",
        profile_id="ag.standard",
        limits=TransportLimits(
            process_timeout_ms=60000,
            silence_timeout_ms=60000,
            max_output_bytes=1000000,
        )
    )
    
    result = execute_direct_ask(request, clock=clock, ids=ids)
    
    assert result.error_code is None
    # Depending on what the adapter says, it'll likely be SUCCEEDED_VERIFIED
    assert result.request_state == RequestState.SUCCEEDED_VERIFIED
    assert result.response_text is not None
    assert len(result.response_text.strip()) > 0


def test_execute_direct_ask_unknown_peer(tmp_path: Path, clock: Clock, ids: IdSource) -> None:
    request = DirectAskRequest(
        workspace_root=tmp_path,
        peer_name="unknown-peer-xyz",
        prompt="say hello",
        profile_id=None,
        limits=TransportLimits(
            process_timeout_ms=60000,
            silence_timeout_ms=60000,
            max_output_bytes=1000000,
        )
    )
    
    # Let the exception propagate naturally (resolve_peer_target should raise ValueError)
    with pytest.raises(ValueError, match="unsupported cli_name"):
        execute_direct_ask(request, clock=clock, ids=ids)
