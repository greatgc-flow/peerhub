"""#328: disabled agy bucket renders n/a (A); a superseded peer-level probe failure is ignored (B). Read-only, no providers."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest

from peerhub.cli.app import main
from peerhub.extensions.monitor_read_model import render_monitor_frame
from peerhub.extensions.observation import ObservationStore
from peerhub.extensions.observation_model import EvidenceState, Observation, ResourcePool
from tests.communication.harness.core import CoreHarness

pytestmark = [pytest.mark.unit, pytest.mark.diag]

DISABLED = "disabled by provider: You have hit your weekly limit, the 5-hour limit does not currently apply"


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self, tmp_path):
        self.h = CoreHarness(tmp_path / "ws")
        self.db = self.h.db_path
        self.obs = ObservationStore(self.db)
        self.n = 0
        self.now = time.time()

    def add(self, peer, pool, state, *, age, source="agy_cli_usage", evidence_ref=None, **payload):
        """One stored observation `age` seconds before now (pool=None is a peer-level probe row)."""
        self.n += 1
        ref = f"quota:{peer}:{pool}" if pool else None
        if ref:
            self.obs.register_resource_pool(ResourcePool(resource_pool_id=ref, provider=peer, kind="QUOTA"))
        if evidence_ref:
            payload["evidence_ref"] = evidence_ref
        if state == "ERROR":
            payload.setdefault("condition", "provider_probe_error")
        elif state == "UNKNOWN":
            payload.setdefault("condition", "provider_probe_unknown")
        stamp = iso(self.now - age)
        self.obs.persist(Observation(observation_id=f"o{self.n}", subject_ref=peer, kind="quota", resource_pool_ref=ref,
                                     source=source, state=EvidenceState(state), payload=payload, observed_at=stamp, captured_at=stamp))

    def measured(self, peer, pool, *, age, remaining=0.5, source, evidence_ref):
        self.add(peer, pool, "MEASURED", age=age, source=source, evidence_ref=evidence_ref, remaining_fraction=remaining,
                 window_started_at=self.now - age - 3600, resets_at=self.now - age + 7200)

    def capacity(self):
        frame = render_monitor_frame(str(self.db), ["capacity"], read_at=self.now)
        return frame["groups"]["capacity"]["data"]["observations"]


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


def peer_level(items, peer):
    return [o for o in items if o["peer_id"] == peer and o["pool_ref"] is None]


# ---- B: superseded peer-level failures ------------------------------------------------------

CX = dict(source="codex_app_server", evidence_ref="probe:cx:app-server")


def test_superseded_peer_failure_is_ignored(store):
    store.add("cx", None, "ERROR", age=7200, **CX)
    store.measured("cx", "X-7D", age=30, **CX)
    items = store.capacity()
    assert peer_level(items, "cx") == []
    assert [o["pool_ref"] for o in items] == ["quota:cx:X-7D"]  # the success itself is untouched


def test_newer_peer_failure_is_shown(store):
    store.measured("cx", "X-7D", age=7200, **CX)
    store.add("cx", None, "ERROR", age=30, **CX)
    assert [o["state"] for o in peer_level(store.capacity(), "cx")] == ["ERROR"]


def test_same_instant_failure_from_one_partial_run_is_shown(store):
    store.measured("cx", "X-7D", age=60, **CX)
    store.add("cx", None, "UNKNOWN", age=60, **CX)  # malformed part of the same probe run: not older than the success
    assert [o["state"] for o in peer_level(store.capacity(), "cx")] == ["UNKNOWN"]


def test_success_from_another_probe_source_does_not_hide_the_failure(store):
    store.add("cx", None, "ERROR", age=7200, **CX)
    store.measured("cx", "X-7D", age=30, source="other_probe", evidence_ref="probe:cx:other")
    assert [o["state"] for o in peer_level(store.capacity(), "cx")] == ["ERROR"]


def test_success_from_another_account_does_not_hide_the_failure(store):
    """The account key is the peer id (subject_ref): quota rows are stored per peer, and a bound account is its own peer."""
    store.add("cx", None, "ERROR", age=7200, **CX)
    store.measured("cx2", "X-7D", age=30, **CX)
    items = store.capacity()
    assert [o["state"] for o in peer_level(items, "cx")] == ["ERROR"]
    assert peer_level(items, "cx2") == []


def test_non_measured_pool_rows_do_not_supersede(store):
    store.add("cx", None, "ERROR", age=7200, **CX)
    store.add("cx", "X-7D", "UNKNOWN", age=30, **CX)  # a failed pool row is not a successful probe
    assert [o["state"] for o in peer_level(store.capacity(), "cx")] == ["ERROR"]


def test_failure_from_an_unknown_probe_is_never_hidden(store):
    store.add("cx", None, "ERROR", age=7200, source="mystery_probe", condition="probe_failed")  # no evidence_ref, not a known poller
    store.measured("cx", "X-7D", age=30, **CX)
    assert [o["state"] for o in peer_level(store.capacity(), "cx")] == ["ERROR"]


PROBES = {"cx": CX, "cc": dict(source="claude_cli_usage", evidence_ref="probe:cc:usage"),
          "ag": dict(source="agy_cli_usage", evidence_ref="probe:ag:usage")}
POOLS = {"cx": "X-7D", "cc": "C-7D", "ag": "G-7D"}


@pytest.mark.parametrize("peer", ["cx", "cc", "ag"])
def test_probe_exception_row_is_superseded_by_the_same_pollers_newer_success(store, peer):
    """refresh_quota stores a poller that raised as source `quota_probe:<kind>` without an evidence_ref."""
    store.add(peer, None, "ERROR", age=7200, source=f"quota_probe:{peer}", condition="probe_failed", error_type="ValueError")
    store.measured(peer, POOLS[peer], age=30, **PROBES[peer])
    assert peer_level(store.capacity(), peer) == []


@pytest.mark.parametrize("peer", ["cx", "cc", "ag"])
def test_exception_row_newer_than_the_success_is_shown(store, peer):
    store.measured(peer, POOLS[peer], age=7200, **PROBES[peer])
    store.add(peer, None, "ERROR", age=30, source=f"quota_probe:{peer}", condition="probe_failed")
    assert [o["state"] for o in peer_level(store.capacity(), peer)] == ["ERROR"]


@pytest.mark.parametrize("ref, source", [("agy_usage_cwd_refused", "agy_statusline"), ("agy_usage_consumed_tokens", "agy_statusline"),
                                         ("agy_usage_not_a_command", "agy_statusline"), ("probe:ag:conflict", "agy_statusline")])
def test_agy_refusal_and_token_use_rows_stay_visible_after_a_later_success(store, ref, source):
    """Decision: a refusal / token-use / conflict is a safety signal about that run, not a transient probe failure; a newer success does not retire it."""
    store.add("ag", None, "ERROR", age=7200, source=source, evidence_ref=ref, reason=ref)
    store.measured("ag", "G-7D", age=30, **PROBES["ag"])
    assert [o["state"] for o in peer_level(store.capacity(), "ag")] == ["ERROR"]


def test_ag_statusline_fallback_failure_is_superseded_only_by_the_same_pollers_success(store):
    store.add("ag", None, "UNKNOWN", age=7200, source="agy_statusline", evidence_ref="probe:ag:statusline")
    store.measured("ag", "G-7D", age=30, source="codex_app_server", evidence_ref="probe:cx:app-server")  # another poller, same peer id
    assert [o["state"] for o in peer_level(store.capacity(), "ag")] == ["UNKNOWN"]
    store.measured("ag", "G-7D", age=10, **PROBES["ag"])
    assert peer_level(store.capacity(), "ag") == []


def test_peers_group_frame_is_consistent_with_the_capacity_filter(store):
    from peerhub.extensions.monitor_read_model import render_monitor_frame
    store.add("cx", None, "ERROR", age=7200, **CX)
    store.measured("cx", "X-7D", age=30, **CX)
    from peerhub.cli.monitor_view import render_grouped_frame
    frame = render_monitor_frame(str(store.db), ["peers", "capacity"], read_at=store.now)
    assert peer_level(frame["groups"]["capacity"]["data"]["observations"], "cx") == []
    assert "peers" in frame["groups"]  # the peers group is read in the same frame; its per-peer status is rendered from the capacity rows
    payload = {**frame, "selected_groups": ["peers", "capacity"]}
    for expand in (False, True):
        text = render_grouped_frame(payload, view="plain", unicode=False, width=100, expand=expand)
        assert "probe error" not in text and "PROBE" not in text, text


def test_ag_usage_success_supersedes_its_statusline_fallback_failure(store):
    store.add("ag", None, "UNKNOWN", age=7200, source="agy_statusline", evidence_ref="probe:ag:statusline")
    store.measured("ag", "G-7D", age=30, source="agy_cli_usage", evidence_ref="probe:ag:usage")
    assert peer_level(store.capacity(), "ag") == []


def test_history_is_kept_in_the_store(store):
    store.add("cx", None, "ERROR", age=7200, **CX)
    store.measured("cx", "X-7D", age=30, **CX)
    store.capacity()
    import sqlite3
    with sqlite3.connect(store.db) as c:
        assert c.execute("SELECT COUNT(*) FROM observations WHERE resource_pool_ref IS NULL").fetchone()[0] == 1


def snapshot(store, capsys, *flags):
    main(["--db", str(store.db), "monitor", "--snapshot", *flags])  # exit 4: this fixture has no Bridge tables; quota still renders
    return capsys.readouterr().out


def test_superseded_failure_shows_no_probe_note_in_compact_or_expanded(store, capsys):
    store.add("cx", None, "ERROR", age=79200, **CX)
    store.measured("cx", "X-7D", age=20, **CX)
    for flags in ((), ("--expand",)):
        out = snapshot(store, capsys, *flags)
        assert "PROBE" not in out and "probe error" not in out, out


def test_unsuperseded_failure_still_shows_probe_note(store, capsys):
    store.measured("cx", "X-7D", age=7200, **CX)
    store.add("cx", None, "ERROR", age=79200 // 100, **CX)
    out = snapshot(store, capsys, "--expand")
    assert "PROBE" in out
    assert "probe error" in snapshot(store, capsys)


# ---- A: disabled agy bucket -----------------------------------------------------------------

def disabled_store(store, *, disabled_age=60):
    store.measured("ag", "3P-5H", age=10800, remaining=1.0, source="agy_cli_usage", evidence_ref="probe:ag:usage")
    store.measured("ag", "3P-7D", age=disabled_age, remaining=0.0, source="agy_cli_usage", evidence_ref="probe:ag:usage")
    store.add("ag", "3P-5H", "ABSENT", age=disabled_age, source="agy_cli_usage", evidence_ref="probe:ag:usage", reason=DISABLED)  # exactly what the /usage probe writes
    return store


def row_with(out, pool):
    return next(line for line in out.splitlines() if pool in line)


def test_disabled_bucket_is_na_with_no_capacity_or_reset_in_compact_expanded_and_json(store, capsys):
    disabled_store(store)
    compact = snapshot(store, capsys)
    line = row_with(compact, "3P-5H")
    assert "n/a" in line and "100%" not in line and "aged" not in line
    expanded = snapshot(store, capsys, "--expand")
    line = row_with(expanded, "3P-5H")
    assert "n/a" in line and "100%" not in line and "STALE" not in line
    assert "disabled by provider" in expanded
    main(["--db", str(store.db), "monitor", "--snapshot", "--json"])
    items = json.loads(capsys.readouterr().out)["groups"]["capacity"]["data"]["observations"]
    item = next(o for o in items if o["pool_ref"] == "quota:ag:3P-5H")
    assert item["state"] == "ABSENT" and item["measurements"] is None and item["reset_in_seconds"] is None
    assert item["reason"].startswith("disabled by provider")


def test_disabled_then_reenabled_is_measured_again(store, capsys):
    disabled_store(store, disabled_age=3600)
    store.measured("ag", "3P-5H", age=5, remaining=0.8, source="agy_cli_usage", evidence_ref="probe:ag:usage")
    out = snapshot(store, capsys, "--expand")
    line = row_with(out, "3P-5H")
    assert "80%" in line and "n/a" not in line


def test_old_disabled_fact_ages_to_stale_not_a_fresh_na(store, capsys):
    disabled_store(store, disabled_age=3 * 3600)
    items = store.capacity()
    item = next(o for o in items if o["pool_ref"] == "quota:ag:3P-5H")
    assert item["state"] == "STALE" and item["stored_state"] == "ABSENT" and item["measurements"] is None
    line = row_with(snapshot(store, capsys), "3P-5H")
    assert "n/a" not in line and "100%" not in line


def test_disabled_pool_is_never_limiting_in_mapped_quota():
    from peerhub.extensions.monitor_read_model import mapped_quota
    obs = [{"pool_ref": "quota:ag:3P-5H", "state": "ABSENT", "reason": DISABLED, "effective_at_us": 5},
           {"pool_ref": "quota:ag:3P-7D", "state": "MEASURED", "effective_at_us": 5,
            "measurements": {"remaining_fraction": 0.4}}]
    quota = mapped_quota(["3P-5H", "3P-7D"], obs)
    assert quota is not None and quota["state"] == "MEASURED" and quota["pool_ref"] == "quota:ag:3P-7D"
