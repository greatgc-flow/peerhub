"""Real persisted V1 round state must upcast (schema is a string, not a number)."""
import pytest
from pathlib import Path

from fakes import FakeClock, FakeIdSource
from peerhub.core.errors import SchemaError
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.consensus import ConsensusService
from peerhub.governance.provenance import upcast_v1_round
from peerhub.persistence.sqlite import SqliteStateStore


def _real_v1_state(tmp_path: Path) -> dict:
    store = SqliteStateStore(tmp_path / "v1.sqlite3", workspace_home_id="v1-real")
    store.initialize()
    broker = GovernanceBroker(store, clock=FakeClock(range(1, 50)), ids=FakeIdSource([f"i-{n}" for n in range(1, 99)]))
    svc = ConsensusService(broker, clock=FakeClock(range(1, 50)), ids=FakeIdSource([f"d-{n}" for n in range(1, 99)]))
    svc.propose(round_id="r-v1", title="t", question="q", body="b", proposer_id="p1",
                required_participants=("p1", "p2"), eligible_participants=("p1", "p2"),
                risk="normal", source_hash="sha256:s")
    return dict(broker.get_target("r-v1").state)


def test_real_v1_state_without_evidence_is_ambiguity_hold_and_unchanged(tmp_path):
    state = _real_v1_state(tmp_path)
    snapshot = dict(state)
    res = upcast_v1_round(state, {})
    assert res.status == "ambiguity_hold"
    assert res.record == snapshot and state == snapshot


def test_real_v1_state_with_evidence_upcasts_schema_string(tmp_path):
    state = _real_v1_state(tmp_path)
    res = upcast_v1_round(state, {"r-v1": {"origin": "direct", "action": "consensus.round.propose"}})
    assert res.status == "upcasted"
    assert res.record["schema"] == "peerhub.consensus-round.v2"
    assert (res.record["origin"], res.record["action"]) == ("direct", "consensus.round.propose")
    assert state["schema"] == "peerhub.consensus-round.v1"


def test_unknown_schema_string_fails_closed():
    with pytest.raises(SchemaError):
        upcast_v1_round({"schema": "peerhub.consensus-round.v9", "round_id": "x"}, {})
    with pytest.raises(SchemaError):
        upcast_v1_round({"round_id": "x"}, {})
