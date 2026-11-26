"""#329: one ACCOUNTS line (compact), detail rows (--expand) and JSON fields from the same stored snapshot; read-only."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from peerhub.cli.app import main
from peerhub.cli.monitor_legend import QUOTA_NA
from peerhub.extensions.monitor_read_model import render_monitor_frame
from peerhub.extensions.observation_model import EvidenceState, Observation
from tests.communication.diag.test_monitor_328 import Store, iso

pytestmark = [pytest.mark.unit, pytest.mark.diag]

DAY = 86400
SNAPSHOT_COLUMNS = "120"


def account(store: Store, peer, *, age=30, state="MEASURED", **fields):
    """One normalized account observation, `age` seconds old (what refresh would have stored)."""
    store.n += 1
    payload = {"logged_in": True, "plan": None, "plan_status": "n/a", "login_token_expires_at": None, "login_expiry_status": "n/a",
               "auth_method": None, "endpoint_access": None, "evidence_ref": f"probe:{peer}:test"}
    payload.update(fields)
    if state in ("ERROR", "UNAVAILABLE"):
        payload = {"condition": "account_probe_failed", "reason": fields.get("reason", "timeout"), "evidence_ref": payload["evidence_ref"]}
    stamp = iso(store.now - age)
    store.obs.persist(Observation(observation_id=f"a{store.n}", subject_ref=peer, kind="account", resource_pool_ref=None, source=f"{peer}_probe",
                                  state=EvidenceState(state), payload=payload, observed_at=stamp, captured_at=stamp))


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


def populated(store: Store, *, expires_in=12 * DAY):
    """cc pro (token expiring), cx prolite (no expiry exposed), ag credits endpoint only."""
    store.measured("cc", "C-7D", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage")
    store.measured("cx", "X-7D", age=20, source="codex_app_server", evidence_ref="probe:cx:app-server")
    store.measured("ag", "G-7D", age=20, source="agy_cli_usage", evidence_ref="probe:ag:usage")
    expires = store.now + expires_in
    account(store, "cc", plan="pro", plan_status="reported", login_token_expires_at=int(expires), login_expiry_status="reported",
            auth_method="claude.ai")
    account(store, "cx", plan="prolite", plan_status="reported", auth_method="chatgpt")
    account(store, "ag", logged_in="UNKNOWN", plan_status="n/a", endpoint_access="ok")
    return int(expires)


def snapshot(store, capsys, *flags):
    # Account assertions inspect full rows. Captured snapshots still honor COLUMNS (#442).
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("COLUMNS", SNAPSHOT_COLUMNS)
        main(["--db", str(store.db), "monitor", "--snapshot", *flags])  # exit 4: no Bridge tables in this fixture; quota still renders
    return capsys.readouterr().out


def accounts_json(store, capsys):
    out = snapshot(store, capsys, "--json")
    return json.loads(out)["groups"]["capacity"]["data"]["accounts"]


def account_row(store, capsys, peer="cc"):
    """#374: the compact ACCOUNTS line left the compact frame; --expand keeps one row per peer."""
    out = snapshot(store, capsys, "--expand")
    return next(line for line in out.splitlines() if re.match(rf"^{re.escape(peer)} (login|account) ", line))


def verdict(store, capsys):
    """Line 1 of the compact frame: account problems reach it as alerts (#374). `--group capacity`: this fixture has no
    Bridge tables, so the default groups would name two store alerts first."""
    return snapshot(store, capsys, "--group", "capacity").splitlines()[0]


@pytest.mark.parametrize("quota_age,plan,peer,changed", [(20, "free", "cx", True), (40, "free", "cx", False),
                                                        (30, "free", "cx", False), (20, "prolite", "cx", False),
                                                        (20, "free", "cc", False), (20, "free", "cx-other", False),
                                                        (20, "unknown", "cx", False), (20, "n/a", "cx", False)])
@pytest.mark.parametrize("quota_first", [False, True])
def test_quota_plan_change_requires_newer_same_login_evidence(store, capsys, quota_age, plan, peer, changed, quota_first):
    def quota():
        store.add(peer, "X-5H", "ABSENT", age=quota_age, source="codex_app_server", reason=NOT_REPORTED + plan)
    if quota_first:
        quota()
    account(store, "cx", age=30, plan="prolite", plan_status="reported")
    if not quota_first:
        quota()
    item = accounts_json(store, capsys)[0]
    assert item["plan"] == ("free" if changed else "prolite")
    assert bool(item.get("plan_changed")) == changed
    if changed:
        assert item["plan_source"] == "quota" and item["state"] == "STALE"
    assert "plan changed" not in snapshot(store, capsys)  # #374: ACCOUNTS is --expand only
    assert ("plan changed" in snapshot(store, capsys, "--expand")) == changed


@pytest.mark.parametrize("converges", [False, True])
def test_plan_mismatch_reprobes_with_backoff(store, monkeypatch, capsys, converges):
    from dataclasses import replace
    from peerhub.extensions import quota_capture
    from peerhub.extensions.quota_capture import refresh_quota
    from peerhub.extensions.quota_probes.common import absent_pool
    from tests.communication.observation.test_account_probes import live_reading
    account(store, "cx", age=400, plan="prolite", plan_status="reported")
    store.add("cx", "X-5H", "ABSENT", age=20, reason=NOT_REPORTED + "free")
    clock = [int(store.now)]
    monkeypatch.setattr(quota_capture.time, "time", lambda: clock[0])
    calls = []
    def poll():
        calls.append(clock[0])
        # account/read finishes before the quota probe of this same collection run.
        return replace(live_reading("cx", plan="free" if converges else "prolite"), observed_at=clock[0] - 1)
    def quota(**kw):
        return (absent_pool(kw["ids"], "cx", "default", "cx", clock[0], 300, "X-5H", plan="free"),)
    for elapsed, count in ((0, 1), (60, 1), (299, 1 if converges else 2)):
        clock[0] = int(store.now) + elapsed
        refresh_quota(store.db, ["cx"], pollers={"cx": quota}, account_pollers={"cx": poll}, version_checker=lambda k: None)
        assert len(calls) == count
        latest = store.obs.latest("cx", "quota", read_at=clock[0])
        assert latest.stored_state == EvidenceState.ABSENT and latest.observation.payload["reason"] == NOT_REPORTED + "free"
        item = accounts_json(store, capsys)[0]
        assert item["plan"] == "free" and bool(item.get("plan_changed")) == (not converges)


def test_corrupt_quota_row_is_identified_as_quota(store, monkeypatch):
    from peerhub.extensions import monitor_quota
    from peerhub.extensions.observation_model import ObservationCorruptError
    account(store, "cx", plan="prolite", plan_status="reported")
    store.add("cx", "X-5H", "ABSENT", age=20, reason=NOT_REPORTED + "free")
    parse = monitor_quota.row_to_observation
    def corrupt_quota(row):
        if row["kind"] == "quota":
            raise ObservationCorruptError("invalid stored quota")
        return parse(row)
    monkeypatch.setattr(monitor_quota, "row_to_observation", corrupt_quota)
    with sqlite3.connect(store.db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM observations WHERE kind='account'").fetchall()
        with pytest.raises(sqlite3.DataError, match="invalid quota observation at capture_seq="):
            monitor_quota._read_accounts(conn, rows, store.now)


def test_compact_has_no_accounts_line_and_expand_has_one_row_per_peer(store, capsys):
    # #374: ACCOUNTS left the compact frame; the expanded rows carry plan, login and the token expiry date.
    expires = populated(store)
    out = snapshot(store, capsys)
    assert "ACCOUNTS" not in out and "token" not in out
    day = datetime.fromtimestamp(expires, timezone.utc).strftime("%Y-%m-%d")
    assert account_row(store, capsys, "cc").startswith(f"cc login ok | plan pro | login token expiry {day}")
    assert account_row(store, capsys, "cx").startswith("cx login ok | plan prolite")
    assert "credits endpoint ok" in account_row(store, capsys, "ag")


@pytest.mark.parametrize("days,alert", [(40, False), (12, False), (1, True)])
def test_token_expiry_reaches_line_1_only_within_48_hours_and_never_as_billing(store, capsys, days, alert):
    populated(store, expires_in=days * DAY)
    out = snapshot(store, capsys)
    assert ("token_expiring: cc" in verdict(store, capsys)) == alert
    assert "billing" not in out.lower().replace("not billing", "")


def test_expired_login_token_is_labelled_not_logged_out(store, capsys):
    populated(store, expires_in=-2 * DAY)
    line = verdict(store, capsys)
    assert "token_expired: cc" in line and "login: cc" not in line
    assert account_row(store, capsys, "cc").startswith("cc login ok | plan pro")


def test_expanded_rows_label_login_token_expiry_and_hide_identity(store, capsys):
    expires = populated(store)
    out = snapshot(store, capsys, "--expand")
    rows = [line for line in out.splitlines() if re.match(r"^(cc|cx|ag) .*login", line) or "credits endpoint" in line]
    text = "\n".join(rows)
    assert "login token expiry" in out and "not billing" in out.lower()
    assert datetime.fromtimestamp(expires, timezone.utc).strftime("%Y-%m-%d") in text
    assert "plan pro" in text and "plan prolite" in text and "credits endpoint ok" in text
    assert "@" not in out and "org" not in text.lower()


def test_json_parity_with_compact_and_expanded(store, capsys):
    expires = populated(store)
    items = {a["peer_id"]: a for a in accounts_json(store, capsys)}
    assert items["cc"]["logged_in"] is True and items["cc"]["plan"] == "pro" and items["cc"]["plan_status"] == "reported"
    assert items["cc"]["login_token_expires_at"] == expires and items["cc"]["login_expiry_status"] == "reported"
    assert items["cc"]["state"] == "MEASURED"
    assert items["cx"]["plan"] == "prolite" and items["cx"]["login_token_expires_at"] is None and items["cx"]["login_expiry_status"] == "n/a"
    assert items["ag"]["logged_in"] == "UNKNOWN" and items["ag"]["plan"] is None and items["ag"]["plan_status"] == "n/a"
    assert items["ag"]["endpoint_access"] == "ok"
    assert set(items["cc"]) == {"peer_id", "state", "stored_state", "age_seconds", "ttl_seconds", "logged_in", "plan", "plan_status",
                                "login_token_expires_at", "login_expiry_status", "auth_method", "endpoint_access", "probe_error"}
    for peer in items:  # #374: the expanded rows, not a compact line
        assert account_row(store, capsys, peer)
    assert "plan pro" in account_row(store, capsys, "cc") and "plan prolite" in account_row(store, capsys, "cx")


def test_unknown_and_not_applicable_are_distinct(store, capsys):
    store.measured("cc", "C-7D", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage")
    account(store, "cc", logged_in=True, plan=None, plan_status="UNKNOWN", login_expiry_status="UNKNOWN")
    account(store, "cx", logged_in=False, plan=None, plan_status="n/a")
    assert account_row(store, capsys, "cc").startswith("cc login ok | plan ?")
    assert account_row(store, capsys, "cx").startswith("cx login out | plan n/a")
    assert "login: cx" in verdict(store, capsys) and "login: cc" not in verdict(store, capsys)


def test_probe_failure_renders_unknown_never_logged_out(store, capsys):
    store.measured("cc", "C-7D", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage")
    account(store, "cc", state="ERROR", reason="timeout")
    out = snapshot(store, capsys)
    assert account_row(store, capsys, "cc").startswith("cc account ? | probe error: timeout")
    assert "login out" not in out and "login:" not in out.splitlines()[0]
    item = accounts_json(store, capsys)[0]
    assert item["logged_in"] == "UNKNOWN" and item["state"] == "ERROR" and item["probe_error"] == "timeout" and item["plan"] is None
    assert "timeout" in snapshot(store, capsys, "--expand")


def test_a_failed_probe_keeps_the_last_good_reading_as_aged_with_an_error_note(store, capsys):
    store.measured("cc", "C-7D", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage")
    expires = store.now + 5 * DAY
    account(store, "cc", age=3600, plan="pro", plan_status="reported", login_token_expires_at=int(expires), login_expiry_status="reported")
    account(store, "cc", age=10, state="ERROR", reason="timeout")
    row = account_row(store, capsys, "cc")
    assert row.startswith("cc login ok | plan pro | login token expiry ") and row.endswith("[STALE]")
    assert "probe error: timeout" in row
    item = accounts_json(store, capsys)[0]
    assert item["plan"] == "pro" and item["logged_in"] is True and item["state"] == "STALE" and item["stored_state"] == "MEASURED"
    assert item["probe_error"] == "timeout" and 3590 < item["age_seconds"] < 3700  # the age is the good reading's
    expanded = snapshot(store, capsys, "--expand")
    assert "plan pro" in expanded and "probe error: timeout" in expanded and "login ?" not in expanded


def test_failed_probe_account_row_is_independent_of_terminal_width(monkeypatch, capsys):
    from peerhub.extensions import monitor_read_model

    narrow_columns = "80"
    monkeypatch.setenv("COLUMNS", narrow_columns)
    expires = datetime(2026, 11, 26, tzinfo=timezone.utc)
    reading = {
        "peer_id": "cc", "logged_in": True, "plan": "pro", "plan_status": "reported",
        "login_token_expires_at": expires.timestamp(), "login_expiry_status": "reported",
        "state": "STALE", "stored_state": "MEASURED", "probe_error": "timeout", "age_seconds": 3600,
    }
    payload = {"status": "OK", "groups": {"capacity": {"status": "OK", "data": {"accounts": [reading]}}}}
    # Supply the read model directly so only the snapshot layout is under test.
    monkeypatch.setattr(monitor_read_model, "render_monitor_frame", lambda *args, **kwargs: payload)
    row = account_row(SimpleNamespace(db=__file__), capsys)
    assert row.endswith("[STALE]"), row
    assert os.environ["COLUMNS"] == narrow_columns


def test_a_later_good_probe_clears_the_error_note(store, capsys):
    store.measured("cc", "C-7D", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage")
    account(store, "cc", age=3600, plan="pro", plan_status="reported")
    account(store, "cc", age=600, state="ERROR", reason="timeout")
    account(store, "cc", age=10, plan="max", plan_status="reported")
    row = account_row(store, capsys, "cc")
    assert row.startswith("cc login ok | plan max") and "probe error" not in row
    assert accounts_json(store, capsys)[0]["probe_error"] is None


def test_an_invalid_stored_expiry_is_unknown_in_json_and_text(store, capsys):
    store.measured("cc", "C-7D", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage")
    account(store, "cc", plan="pro", plan_status="reported", login_token_expires_at="garbage", login_expiry_status="reported")
    item = accounts_json(store, capsys)[0]
    assert item["login_expiry_status"] == "UNKNOWN" and item["login_token_expires_at"] is None
    assert "token" not in snapshot(store, capsys)
    assert "login token expiry ?" in snapshot(store, capsys, "--expand")


@pytest.mark.parametrize("stored", [253402300800000, 253402300800, 1e300, -5, 0, "2026-11-26", True, [1], 253402300800.5, 10**309, -10**309])
def test_out_of_range_stored_expiry_is_unknown_in_every_view_and_never_crashes(store, capsys, stored):
    """Rows written by another version (or by hand) may hold any value: the read model re-validates, the renderers never see it."""
    store.measured("cc", "C-7D", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage")
    account(store, "cc", plan="pro", plan_status="reported", login_token_expires_at=stored, login_expiry_status="reported")
    item = accounts_json(store, capsys)[0]
    assert item["login_token_expires_at"] is None and item["login_expiry_status"] == "UNKNOWN"
    assert "token" not in snapshot(store, capsys)
    assert "login token expiry ?" in snapshot(store, capsys, "--expand")  # would raise OSError/OverflowError if the value reached strftime


def test_the_last_representable_second_is_rendered(store, capsys):
    store.measured("cc", "C-7D", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage")
    account(store, "cc", plan="pro", plan_status="reported", login_token_expires_at=253402300799, login_expiry_status="reported")
    assert accounts_json(store, capsys)[0]["login_token_expires_at"] == 253402300799
    assert "login token expiry 9999-12-31" in snapshot(store, capsys, "--expand")


def test_account_rows_never_consume_the_shared_quota_row_limit(store, capsys):
    for n in range(100):  # exactly the 100-row quota cap
        store.measured(f"p{n:03d}", "X-7D", age=20, source="codex_app_server", evidence_ref="probe:cx:app-server")
    account(store, "zz-last", plan="pro", plan_status="reported")  # sorts after every quota row
    data = json.loads(snapshot(store, capsys, "--json"))["groups"]["capacity"]["data"]
    assert data["truncated"] is False and data["sample_size"] == 100
    assert [a["peer_id"] for a in data["accounts"]] == ["zz-last"]


def test_account_rows_have_their_own_cap_and_say_when_it_cut_them_off(store, capsys):
    for n in range(51):
        account(store, f"peer{n:02d}", plan="pro", plan_status="reported")
    data = json.loads(snapshot(store, capsys, "--json"))["groups"]["capacity"]["data"]
    assert len(data["accounts"]) == 50 and data["accounts_truncated"] is True
    store2 = Store(store.db.parent.parent / "second")
    account(store2, "cc", plan="pro", plan_status="reported")
    one = json.loads(snapshot(store2, capsys, "--json"))["groups"]["capacity"]["data"]
    assert "accounts_truncated" not in one  # present only when it happened, like the accounts key itself


def test_privacy_no_identity_or_binding_reaches_storage_or_any_view(store, capsys):
    from tests.communication.observation.test_account_probes import CC_AUTH, CX_ACCOUNT, EMAIL, ORG_ID, ORG_NAME, ACCT_ID, SECRET
    from peerhub.extensions.quota_probes import parse_claude_auth_status, parse_codex_account
    store.measured("cc", "C-7D", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage")
    store.measured("cx", "X-7D", age=20, source="codex_app_server", evidence_ref="probe:cx:app-server")
    account(store, "cc", **parse_claude_auth_status(json.dumps(CC_AUTH)))
    account(store, "cx", **parse_codex_account(CX_ACCOUNT))
    with sqlite3.connect(store.db) as c:
        stored = " ".join(row[0] for row in c.execute("SELECT payload_json FROM observations WHERE kind='account'"))
    views = [stored, snapshot(store, capsys), snapshot(store, capsys, "--expand"), snapshot(store, capsys, "--json"),
             json.dumps(render_monitor_frame(str(store.db), ["capacity", "peers"], read_at=store.now))]
    for text in views:
        for secret in (EMAIL, ORG_ID, ORG_NAME, ACCT_ID, SECRET, "example.com"):
            assert secret not in text
        for forbidden in ("account_ref", "account_binding", "binding_hash", "\"hash\"", "email", "orgId", "chatgptAccountId"):
            assert forbidden not in text, forbidden
    diag_blob = json.dumps(store.obs.latest("cc", "account").observation.payload)
    assert "binding" not in diag_blob and "ref" not in diag_blob.replace("evidence_ref", "")


NOT_REPORTED = "not reported by provider; plan="


def cc_without_5h(store, plan_in_reason="unknown"):
    store.measured("cc", "C-7D", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage")
    store.add("cc", "C-5H", "ABSENT", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage", reason=NOT_REPORTED + plan_in_reason)


def test_5h_na_notes_use_the_fresh_account_plan_when_the_quota_probe_reports_none(store, capsys):
    store.h.create_peer({"peer_id": "cc", "display_name": "Claude", "adapter_ref": "claude"})  # the PEERS block needs a registered peer
    cc_without_5h(store)
    account(store, "cc", age=30, plan="pro", plan_status="reported")
    compact = snapshot(store, capsys)
    expanded = snapshot(store, capsys, "--expand")
    observations = json.loads(snapshot(store, capsys, "--json"))["groups"]["capacity"]["data"]["observations"]
    item = next(o for o in observations if o["pool_ref"] == "quota:cc:C-5H")
    assert item["reason"] == NOT_REPORTED + "unknown"  # the stored fact is not rewritten
    assert item["display_plan"] == "pro" and item["plan_source"] == "account"
    other = next(o for o in observations if o["pool_ref"] == "quota:cc:C-7D")
    assert "display_plan" not in other and "plan_source" not in other  # only the notes that needed it carry the fields
    assert "C-5H" not in compact
    pools = expanded.split("POOLS ", 1)[1].split("QUOTA [", 1)[0]
    assert f"(C-5H {QUOTA_NA})" in pools and "plan unknown" not in expanded
    assert "plan pro: no 5h window" in expanded


def test_unknown_plan_stays_unknown_without_a_fresh_account_plan(store, capsys):
    cc_without_5h(store)
    account(store, "cc", age=7 * 3600, plan="pro", plan_status="reported")  # aged: not a fresh reading
    assert "plan unknown" in snapshot(store, capsys, "--expand")
    store2 = Store(store.db.parent.parent / "second")
    cc_without_5h(store2)
    account(store2, "cc", age=30, plan=None, plan_status="UNKNOWN")  # reported nothing
    assert "plan unknown" in snapshot(store2, capsys, "--expand")


def test_a_quota_probe_plan_is_never_overridden_by_the_account_plan(store, capsys):
    store.measured("cx", "X-7D", age=20, source="codex_app_server", evidence_ref="probe:cx:app-server")
    store.add("cx", "X-5H", "ABSENT", age=20, source="codex_app_server", evidence_ref="probe:cx:app-server", reason=NOT_REPORTED + "prolite")
    account(store, "cx", age=30, plan="other", plan_status="reported")
    out = snapshot(store, capsys, "--expand")
    assert "(plan prolite)" in out and "plan other" not in out
    items = json.loads(snapshot(store, capsys, "--json"))["groups"]["capacity"]["data"]["observations"]
    assert all("display_plan" not in o for o in items)


def test_account_ttl_is_hours_so_a_two_hour_old_reading_is_not_aged(store, capsys):
    store.measured("cc", "C-7D", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage")
    account(store, "cc", age=2 * 3600, plan="pro", plan_status="reported")
    assert account_row(store, capsys, "cc").endswith("[MEASURED]")
    assert accounts_json(store, capsys)[0]["state"] == "MEASURED"


def test_stale_account_observation_is_marked_aged_in_every_view(store, capsys):
    store.measured("cc", "C-7D", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage")
    account(store, "cc", age=7 * 3600, plan="pro", plan_status="reported")
    assert account_row(store, capsys, "cc").endswith("[STALE]")
    item = accounts_json(store, capsys)[0]
    assert item["state"] == "STALE" and item["stored_state"] == "MEASURED" and item["age_seconds"] > 7 * 3600 - 5
    assert "STALE" in snapshot(store, capsys, "--expand")


def test_latest_observation_wins_and_history_is_kept(store, capsys):
    store.measured("cc", "C-7D", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage")
    account(store, "cc", age=7200, plan="pro", plan_status="reported")
    account(store, "cc", age=10, plan="max", plan_status="reported")
    assert account_row(store, capsys, "cc").startswith("cc login ok | plan max")
    with sqlite3.connect(store.db) as c:
        assert c.execute("SELECT COUNT(*) FROM observations WHERE kind='account'").fetchone()[0] == 2


def test_accounts_are_isolated_per_peer(store, capsys):
    account(store, "cc", plan="pro", plan_status="reported")
    account(store, "cc2", plan="max", plan_status="reported")
    by_peer = {a["peer_id"]: a for a in accounts_json(store, capsys)}
    assert by_peer["cc"]["plan"] == "pro" and by_peer["cc2"]["plan"] == "max"


def test_no_accounts_means_no_line_no_key_and_unchanged_quota_output(store, capsys):
    store.measured("cc", "C-7D", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage")
    out = snapshot(store, capsys)
    assert "ACCOUNTS" not in out and "ACCOUNTS" not in snapshot(store, capsys, "--expand")
    assert "accounts" not in json.loads(snapshot(store, capsys, "--json"))["groups"]["capacity"]["data"]


def test_account_rows_do_not_pollute_quota_or_probe_notes(store, capsys):
    populated(store)
    account(store, "cx", state="ERROR", reason="no_response")
    assert "PROBE" not in snapshot(store, capsys, "--expand")  # an account failure is not a quota probe note
    assert "probe error (" not in snapshot(store, capsys)
    items = render_monitor_frame(str(store.db), ["capacity"], read_at=store.now)["groups"]["capacity"]["data"]["observations"]
    assert all(o["kind"] == "quota" for o in items)


def test_untrusted_stored_values_are_sanitized_at_read_time(store, capsys):
    store.measured("cc", "C-7D", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage")
    account(store, "cc", plan="pro\x1b[31m; evil=1 " + "x" * 80, plan_status="reported", auth_method="a\nb")
    item = accounts_json(store, capsys)[0]
    assert len(item["plan"]) <= 24 and "\x1b" not in item["plan"] and ";" not in item["plan"]
    out = snapshot(store, capsys)
    assert "\x1b" not in out


def test_snapshot_is_read_only(store, capsys):
    populated(store)
    before = store.db.read_bytes()
    snapshot(store, capsys)
    snapshot(store, capsys, "--expand")
    snapshot(store, capsys, "--json")
    assert store.db.read_bytes() == before
