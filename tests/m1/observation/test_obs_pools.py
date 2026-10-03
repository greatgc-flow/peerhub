"""Wave 5 OBS-006/007/008/015/016 + wire persistence for extension-owned models (D-W0-2)."""
import copy
import sqlite3
from contextlib import closing

import pytest

from peerhub.extensions.observation import ObservationStore
from peerhub.extensions.observation_model import (Observation, ObservationExistsError, PoolConflictError, ResourcePool,
                                                  UnknownResourcePoolError)
from peerhub.m1.models import Peer
from peerhub.m1.wire import WireValidationError
from tests.m1.fakes.observation import FakeObservationSource, measured
from tests.m1.harness.observation import ObservationHarness
from tests.m1.spec import example, valid_objects

pytestmark = [pytest.mark.integration, pytest.mark.observation]


def pool(pid, kind="ACCOUNT", provider="openai", **extra):
    return {"schema_version": "1.0", "resource_pool_id": pid, "provider": provider, "kind": kind, **extra}


def q(frac):
    return FakeObservationSource(measured({"remaining_fraction": frac}))


@pytest.mark.m1_id("OBS-006")
def test_obs_006_peers_share_one_pool_without_copying_quota_into_peer(obs_h):
    for p in ("cx-01", "cx-02"):
        obs_h.create_peer({"peer_id": p})
    obs_h.register_pool(pool("openai-account-X"))
    peers_before, pools_before = obs_h.core_digests()["peers"], obs_h.pool_rows()
    obs_h.capture("peer:cx-01", "quota", q(0.3), resource_pool_ref="openai-account-X")
    obs_h.capture("peer:cx-02", "quota", q(0.3), resource_pool_ref="openai-account-X")
    views = obs_h.list_for_resource_pool("openai-account-X")
    assert sorted(v.observation.subject_ref for v in views) == ["peer:cx-01", "peer:cx-02"]
    assert len(obs_h.pool_rows()) == 1 == len(pools_before)  # one shared pool object, referenced twice
    assert {r[3] for r in obs_h.obs_rows()} == {"openai-account-X"}
    assert obs_h.core_digests()["peers"] == peers_before  # Peers untouched by observations
    with closing(sqlite3.connect(obs_h.db_path)) as c:
        assert {r[1] for r in c.execute("PRAGMA table_info(peers)")} == {"peer_id", "display_name", "adapter_ref", "metadata_json", "created_at"}
    assert set(Peer.model_fields) == {"schema_version", "peer_id", "display_name", "adapter_ref", "metadata", "created_at"}
    for p in ("cx-01", "cx-02"):
        dumped = str(obs_h.get_peer(p).model_dump(mode="json")).lower()
        assert "quota" not in dumped and "openai-account-x" not in dumped
    # positive control: the quota evidence IS retrievable, per subject, through the pool reference
    assert obs_h.latest("peer:cx-02", "quota", resource_pool_ref="openai-account-X").observation.payload == {"remaining_fraction": 0.3}


@pytest.mark.m1_id("OBS-007")
def test_obs_007_independent_pools_do_not_leak(obs_h):
    obs_h.register_pool(pool("A")), obs_h.register_pool(pool("B", provider="anthropic"))
    oa = obs_h.capture("peer:a", "quota", q(0.1), resource_pool_ref="A")
    ob = obs_h.capture("peer:b", "quota", q(0.9), resource_pool_ref="B")
    assert [v.observation.observation_id for v in obs_h.list_for_resource_pool("A")] == [oa.observation_id]
    assert [v.observation.observation_id for v in obs_h.list_for_resource_pool("B")] == [ob.observation_id]
    assert obs_h.latest("peer:a", "quota", resource_pool_ref="B") is None  # attributed to the correct pool only
    assert obs_h.latest("peer:b", "quota", resource_pool_ref="A") is None
    assert obs_h.latest("peer:a", "quota", resource_pool_ref="A").observation.payload["remaining_fraction"] == 0.1


@pytest.mark.m1_id("OBS-008")
def test_obs_008_observation_append_is_immutable_evidence(obs_h):
    first = obs_h.capture("peer:a", "quota", q(0.5))
    rows = obs_h.obs_rows()
    dup = Observation(observation_id=first.observation_id, subject_ref="peer:a", kind="quota", source="x",
                      observed_at="2026-10-01T00:00:00Z", state="MEASURED", payload={"remaining_fraction": 0.99})
    with pytest.raises(ObservationExistsError):
        obs_h.obs.persist(dup)
    assert obs_h.obs_rows() == rows
    stmts = [("UPDATE observations SET payload_json='{}' WHERE observation_id=?", (first.observation_id,)),
             ("DELETE FROM observations WHERE observation_id=?", (first.observation_id,)),
             ("INSERT OR REPLACE INTO observations (observation_id, subject_ref, kind, source, state, payload_json, observed_at, captured_at, effective_at_us) "
              "VALUES (?, 'peer:a', 'quota', 'evil', 'MEASURED', '{}', '2026-10-01T00:00:00Z', NULL, 0)", (first.observation_id,))]
    for recursive in (False, True):  # replace protection must not depend on recursive_triggers
        for sql, args in stmts:
            with closing(sqlite3.connect(obs_h.db_path)) as c:
                c.execute(f"PRAGMA recursive_triggers = {'ON' if recursive else 'OFF'}")
                with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                    c.execute(sql, args)
                    c.commit()
            assert obs_h.obs_rows() == rows, (sql, recursive)
    # refresh = a NEW observation identity; the old evidence is unchanged
    second = obs_h.capture("peer:a", "quota", q(0.4))
    assert second.observation_id != first.observation_id
    assert obs_h.obs_rows()[0] == rows[0] and len(obs_h.obs_rows()) == 2


@pytest.mark.m1_id("OBS-008")
def test_obs_008_stale_and_legacy_triggers_are_replaced_on_reopen(obs_h):
    first = obs_h.capture("peer:a", "quota", q(0.5))
    with closing(sqlite3.connect(obs_h.db_path)) as c:  # simulate an older/tampered schema: protection dropped, a swallowing trigger added
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='observations'").fetchall():
            c.execute(f'DROP TRIGGER "{name}"')
        c.execute("CREATE TRIGGER legacy_swallow BEFORE INSERT ON observations BEGIN SELECT RAISE(IGNORE); END")
        c.execute("UPDATE observations SET payload_json='{\"tampered\":1}'")  # proves the protection really was gone
        c.commit()
    obs_h.reopen()
    with closing(sqlite3.connect(obs_h.db_path)) as c:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='observations'")}
        assert "legacy_swallow" not in names and len(names) >= 3
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            c.execute("UPDATE observations SET payload_json='{}'")
    nxt = obs_h.capture("peer:a", "quota", q(0.2))  # the swallowing trigger is gone: the insert really happens
    assert [r[1] for r in obs_h.obs_rows()] == [first.observation_id, nxt.observation_id]


@pytest.mark.m1_id("OBS-015")
def test_obs_015_unknown_pool_reference_is_rejected_atomically(obs_h):
    obs_h.create_peer({"peer_id": "cx-01"})
    obs_h.register_pool(pool("real"))
    before, src = obs_h.full_state(), q(0.5)
    with pytest.raises(UnknownResourcePoolError):
        obs_h.capture("peer:cx-01", "quota", src, resource_pool_ref="ghost")
    assert src.calls == 0  # binding validated before the probe ran
    assert obs_h.full_state() == before and obs_h.obs_rows() == []
    ok = Observation(observation_id="w", subject_ref="peer:cx-01", kind="quota", source="s", observed_at="2026-10-01T00:00:00Z",
                     state="MEASURED", payload={"remaining_fraction": 1.0}, resource_pool_ref="ghost")
    with pytest.raises(UnknownResourcePoolError):
        obs_h.obs.persist(ok)
    assert obs_h.full_state() == before
    with closing(sqlite3.connect(obs_h.db_path)) as c:  # defence in depth: the database itself enforces the reference
        c.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            c.execute("INSERT INTO observations (observation_id, subject_ref, resource_pool_ref, kind, source, state, payload_json, observed_at, "
                      "captured_at, effective_at_us) VALUES ('raw','p','ghost','quota','s','MEASURED','{}','2026-10-01T00:00:00Z',NULL,0)")
            c.commit()
    assert obs_h.full_state() == before
    # positive control: a registered pool is accepted by the same path
    obs_h.capture("peer:cx-01", "quota", q(0.5), resource_pool_ref="real")
    assert len(obs_h.obs_rows()) == 1


@pytest.mark.m1_id("OBS-016")
def test_obs_016_pool_and_subject_isolation(obs_h):
    for pid in ("P1", "P2"):
        obs_h.register_pool(pool(pid))
    ids = {}
    for subj in ("peer:A", "peer:B"):
        for pid, frac in (("P1", 0.1), ("P2", 0.2)):
            ids[(subj, pid)] = obs_h.capture(subj, "quota", q(frac if subj == "peer:A" else frac + 0.5), resource_pool_ref=pid).observation_id
    for (subj, pid), oid in ids.items():
        assert obs_h.latest(subj, "quota", resource_pool_ref=pid).observation.observation_id == oid
    assert sorted(v.observation.observation_id for v in obs_h.list_for_resource_pool("P1")) == sorted([ids[("peer:A", "P1")], ids[("peer:B", "P1")]])
    assert [v.observation.observation_id for v in obs_h.list_for_resource_pool("P2", subject_ref="peer:A")] == [ids[("peer:A", "P2")]]
    assert obs_h.list_for_resource_pool("P1", subject_ref="peer:nobody") == []
    assert obs_h.latest("peer:A", "quota", resource_pool_ref="P1").observation.payload == {"remaining_fraction": 0.1}
    with pytest.raises(UnknownResourcePoolError):  # an unknown pool is an error, not an empty "all clear"
        obs_h.list_for_resource_pool("P3")


@pytest.mark.m1_id("OBS-006")
def test_obs_pool_registration_is_idempotent_and_immutable(obs_h):
    p1 = obs_h.register_pool(pool("P", metadata={"tier": "1"}))
    rows = obs_h.pool_rows()
    assert obs_h.register_pool(pool("P", metadata={"tier": "1"})) == p1 and obs_h.pool_rows() == rows
    for changed in (pool("P", provider="other", metadata={"tier": "1"}), pool("P", kind="QUOTA", metadata={"tier": "1"}), pool("P", metadata={"tier": "2"})):
        with pytest.raises(PoolConflictError):
            obs_h.register_pool(changed)
        assert obs_h.pool_rows() == rows
    for sql in ("UPDATE resource_pools SET provider='x'", "DELETE FROM resource_pools"):
        with closing(sqlite3.connect(obs_h.db_path)) as c:
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                c.execute(sql)
    assert obs_h.obs.get_resource_pool("P") == p1
    assert obs_h.obs.get_resource_pool("missing") is None


# ---------------------------------------------------------------- wire persistence (D-W0-2)
def _base():
    return {"peer-observation": {**copy.deepcopy(example("peer-observation")), "resource_pool_ref": "pool:provider-account"},
            "resource-pool": copy.deepcopy(valid_objects()["resource-pool"])}


def _reject(h, kind, obj, expect, exc=WireValidationError):
    before = h.full_state()
    with pytest.raises(exc) as ei:
        h.persist_wire(kind, obj)
    assert expect in str(ei.value), (kind, obj, str(ei.value))
    assert "; " not in str(ei.value), str(ei.value)  # only the intended violation
    assert h.full_state() == before


@pytest.mark.m1_id("SCH-011")
def test_w5_wire_persistence_ports_are_live_positive_controls(obs_h):
    b = _base()
    obs_h.create_peer({"peer_id": "cx-01"})
    pool_ = obs_h.persist_wire("resource-pool", {**b["resource-pool"], "resource_pool_id": "pool:provider-account"})
    assert isinstance(pool_, ResourcePool) and len(obs_h.pool_rows()) == 1
    o = obs_h.persist_wire("peer-observation", b["peer-observation"])
    assert o.observation_id == "obs-1" and obs_h.obs_rows()[0][1:5] == ("obs-1", "peer:cx-01", "pool:provider-account", "quota")
    assert obs_h.obs_rows()[0][8:10] == ("2026-10-01T00:00:00Z", "2026-10-01T00:00:01Z")  # source-supplied times kept verbatim


@pytest.mark.m1_id("SCH-011")
def test_w5_wire_observation_required_and_null_fields(obs_h):
    b = _base()
    obs_h.register_pool({**b["resource-pool"], "resource_pool_id": "pool:provider-account"})
    for field in ("schema_version", "observation_id", "subject_ref", "kind", "source", "observed_at", "state", "payload"):
        _reject(obs_h, "peer-observation", {k: v for k, v in b["peer-observation"].items() if k != field}, f"'{field}' is a required property")
        _reject(obs_h, "peer-observation", {**b["peer-observation"], field: None}, f"{field}:")
    for field in ("subject_ref", "kind", "source", "observation_id"):
        _reject(obs_h, "peer-observation", {**b["peer-observation"], field: ""}, f"{field}:")
    # optional nullable fields accept null/omission (positive control for the null rejection above)
    ok = {k: v for k, v in b["peer-observation"].items() if k not in ("captured_at", "resource_pool_ref")}
    obs_h.persist_wire("peer-observation", {**ok, "observation_id": "o-null", "captured_at": None, "resource_pool_ref": None})
    obs_h.persist_wire("peer-observation", {**ok, "observation_id": "o-omit"})
    assert [r[1] for r in obs_h.obs_rows()] == ["o-null", "o-omit"] and obs_h.obs_rows()[0][9] is None


@pytest.mark.m1_id("SCH-013")
def test_w5_wire_observation_malformed_values_fail_closed(obs_h):
    b = _base()
    obs_h.register_pool({**b["resource-pool"], "resource_pool_id": "pool:provider-account"})
    base = b["peer-observation"]
    for bad in ("2026-13-45T00:00:00Z", "yesterday", "2026-10-01 00:00:00", "2026-10-01T00:00:00"):
        _reject(obs_h, "peer-observation", {**base, "observed_at": bad}, "observed_at:")
        _reject(obs_h, "peer-observation", {**base, "captured_at": bad}, "captured_at:")
    _reject(obs_h, "peer-observation", {**base, "state": "measured"}, "state:")
    _reject(obs_h, "peer-observation", {**base, "schema_version": "2.0"}, "schema_version:")
    _reject(obs_h, "peer-observation", {**base, "unknown_field": 1}, "unknown_field")
    _reject(obs_h, "peer-observation", {**base, "payload": []}, "payload:")
    _reject(obs_h, "peer-observation", {**base, "ttl_seconds": 5}, "ttl_seconds")  # no TTL on the wire: policy owns it
    # schema-valid but dishonest / unbound evidence is also rejected without state change
    from peerhub.extensions.observation_model import InvalidObservationError

    _reject(obs_h, "peer-observation", {**base, "state": "ABSENT", "payload": {"remaining_fraction": 0.0}}, "ABSENT", InvalidObservationError)
    _reject(obs_h, "peer-observation", {**base, "resource_pool_ref": "pool:ghost"}, "pool:ghost", UnknownResourcePoolError)
    assert obs_h.obs_rows() == []
    obs_h.persist_wire("peer-observation", base)  # control: the unmodified object persists
    assert len(obs_h.obs_rows()) == 1


@pytest.mark.m1_id("SCH-012")
def test_w5_wire_resource_pool_fail_closed(obs_h):
    b = _base()["resource-pool"]
    for field in ("schema_version", "resource_pool_id", "provider", "kind"):
        _reject(obs_h, "resource-pool", {k: v for k, v in b.items() if k != field}, f"'{field}' is a required property")
        _reject(obs_h, "resource-pool", {**b, field: None}, f"{field}:")
    _reject(obs_h, "resource-pool", {**b, "kind": "quota"}, "kind:")
    _reject(obs_h, "resource-pool", {**b, "kind": "OTHER"}, "kind:")
    _reject(obs_h, "resource-pool", {**b, "resource_pool_id": ""}, "resource_pool_id:")
    _reject(obs_h, "resource-pool", {**b, "metadata": []}, "metadata:")
    _reject(obs_h, "resource-pool", {**b, "unknown_field": 1}, "unknown_field")
    assert obs_h.pool_rows() == []
    for kind in ("QUOTA", "RATE_LIMIT", "ACCOUNT", "RUNTIME"):  # control: every vocabulary member persists
        obs_h.persist_wire("resource-pool", {**b, "resource_pool_id": f"p-{kind}", "kind": kind})
    assert len(obs_h.pool_rows()) == 4


@pytest.mark.m1_id("SCH-007")
def test_w5_extension_schema_copies_do_not_drift():
    from pathlib import Path

    import peerhub.extensions.observation_model as om
    from tests.m1.spec import SCHEMAS

    for name in ("peer-observation", "resource-pool"):
        assert (Path(om.__file__).parent / "schemas" / f"{name}.schema.json").read_bytes() == (SCHEMAS / f"{name}.schema.json").read_bytes(), name
