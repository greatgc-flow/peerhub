"""#329: one ACCOUNTS line (compact), detail rows (--expand) and JSON fields from the same stored snapshot; read-only."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone

import pytest

from peerhub.cli.app import main
from peerhub.extensions.monitor_read_model import render_monitor_frame
from peerhub.extensions.observation_model import EvidenceState, Observation
from tests.communication.diag.test_monitor_328 import Store, iso

pytestmark = [pytest.mark.unit, pytest.mark.diag]

DAY = 86400


def md(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%m-%d")


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
    main(["--db", str(store.db), "monitor", "--snapshot", *flags])  # exit 4: no Bridge tables in this fixture; quota still renders
    return capsys.readouterr().out


def accounts_json(store, capsys):
    out = snapshot(store, capsys, "--json")
    return json.loads(out)["groups"]["capacity"]["data"]["accounts"]


def accounts_line(out):
    return next(line for line in out.splitlines() if line.startswith("ACCOUNTS"))


def test_compact_shows_one_accounts_line_with_plan_login_and_near_token_expiry(store, capsys):
    expires = populated(store)
    out = snapshot(store, capsys)
    assert sum(line.startswith("ACCOUNTS") for line in out.splitlines()) == 1
    assert accounts_line(out) == f"ACCOUNTS cc pro login ok (token exp {md(expires)}) | cx prolite login ok | ag plan n/a credits ok"


def test_token_expiry_only_appears_within_fourteen_days_and_never_as_billing(store, capsys):
    populated(store, expires_in=40 * DAY)
    out = snapshot(store, capsys)
    assert "token exp" not in accounts_line(out)
    assert "billing" not in out.lower().replace("not billing", "")


def test_expired_login_token_is_labelled_not_logged_out(store, capsys):
    expires = populated(store, expires_in=-2 * DAY)
    line = accounts_line(snapshot(store, capsys))
    assert f"cc pro login ok (token expired {md(expires)})" in line


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
    compact = snapshot(store, capsys)
    assert all(f"{peer} " in accounts_line(compact) for peer in items)
    assert "pro" in accounts_line(compact) and "prolite" in accounts_line(compact)


def test_unknown_and_not_applicable_are_distinct(store, capsys):
    store.measured("cc", "C-7D", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage")
    account(store, "cc", logged_in=True, plan=None, plan_status="UNKNOWN", login_expiry_status="UNKNOWN")
    account(store, "cx", logged_in=False, plan=None, plan_status="n/a")
    line = accounts_line(snapshot(store, capsys))
    assert "cc plan ? login ok" in line
    assert "cx plan n/a login out" in line


def test_probe_failure_renders_unknown_never_logged_out(store, capsys):
    store.measured("cc", "C-7D", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage")
    account(store, "cc", state="ERROR", reason="timeout")
    out = snapshot(store, capsys)
    assert "cc account ? (probe error)" in accounts_line(out)
    assert "login out" not in out
    item = accounts_json(store, capsys)[0]
    assert item["logged_in"] == "UNKNOWN" and item["state"] == "ERROR" and item["probe_error"] == "timeout" and item["plan"] is None
    assert "timeout" in snapshot(store, capsys, "--expand")


def test_a_failed_probe_keeps_the_last_good_reading_as_aged_with_an_error_note(store, capsys):
    store.measured("cc", "C-7D", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage")
    expires = store.now + 5 * DAY
    account(store, "cc", age=3600, plan="pro", plan_status="reported", login_token_expires_at=int(expires), login_expiry_status="reported")
    account(store, "cc", age=10, state="ERROR", reason="timeout")
    assert accounts_line(snapshot(store, capsys)) == f"ACCOUNTS cc pro login ok (token exp {md(expires)}) (aged, probe error)"
    item = accounts_json(store, capsys)[0]
    assert item["plan"] == "pro" and item["logged_in"] is True and item["state"] == "STALE" and item["stored_state"] == "MEASURED"
    assert item["probe_error"] == "timeout" and 3590 < item["age_seconds"] < 3700  # the age is the good reading's
    expanded = snapshot(store, capsys, "--expand")
    assert "plan pro" in expanded and "probe error: timeout" in expanded and "login ?" not in expanded


def test_a_later_good_probe_clears_the_error_note(store, capsys):
    store.measured("cc", "C-7D", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage")
    account(store, "cc", age=3600, plan="pro", plan_status="reported")
    account(store, "cc", age=600, state="ERROR", reason="timeout")
    account(store, "cc", age=10, plan="max", plan_status="reported")
    assert accounts_line(snapshot(store, capsys)) == "ACCOUNTS cc max login ok"
    assert accounts_json(store, capsys)[0]["probe_error"] is None


def test_an_invalid_stored_expiry_is_unknown_in_json_and_text(store, capsys):
    store.measured("cc", "C-7D", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage")
    account(store, "cc", plan="pro", plan_status="reported", login_token_expires_at="garbage", login_expiry_status="reported")
    item = accounts_json(store, capsys)[0]
    assert item["login_expiry_status"] == "UNKNOWN" and item["login_token_expires_at"] is None
    assert "token" not in accounts_line(snapshot(store, capsys))
    assert "login token expiry ?" in snapshot(store, capsys, "--expand")


@pytest.mark.parametrize("stored", [253402300800000, 253402300800, 1e300, -5, 0, "2026-11-26", True, [1], 253402300800.5, 10**309, -10**309])
def test_out_of_range_stored_expiry_is_unknown_in_every_view_and_never_crashes(store, capsys, stored):
    """Rows written by another version (or by hand) may hold any value: the read model re-validates, the renderers never see it."""
    store.measured("cc", "C-7D", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage")
    account(store, "cc", plan="pro", plan_status="reported", login_token_expires_at=stored, login_expiry_status="reported")
    item = accounts_json(store, capsys)[0]
    assert item["login_token_expires_at"] is None and item["login_expiry_status"] == "UNKNOWN"
    assert "token" not in accounts_line(snapshot(store, capsys))
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
    compact, expanded = snapshot(store, capsys), snapshot(store, capsys, "--expand")
    item = next(o for o in json.loads(snapshot(store, capsys, "--json"))["groups"]["capacity"]["data"]["observations"]
                if o["pool_ref"] == "quota:cc:C-5H")
    assert item["reason"] == NOT_REPORTED + "unknown"  # the stored fact is not rewritten
    assert item["display_plan"] == "pro" and item["plan_source"] == "account"
    other = next(o for o in json.loads(snapshot(store, capsys, "--json"))["groups"]["capacity"]["data"]["observations"]
                 if o["pool_ref"] == "quota:cc:C-7D")
    assert "display_plan" not in other and "plan_source" not in other  # only the notes that needed it carry the fields
    assert "C-5H n/a (plan pro)" in compact and "plan unknown" not in compact
    assert "C-5H n/a not reported (plan pro)" in expanded and "plan unknown" not in expanded
    assert "plan pro: no 5h window" in expanded and "plan unknown: no 5h window" not in expanded


def test_unknown_plan_stays_unknown_without_a_fresh_account_plan(store, capsys):
    cc_without_5h(store)
    account(store, "cc", age=7 * 3600, plan="pro", plan_status="reported")  # aged: not a fresh reading
    assert "plan unknown" in snapshot(store, capsys)
    store2 = Store(store.db.parent.parent / "second")
    cc_without_5h(store2)
    account(store2, "cc", age=30, plan=None, plan_status="UNKNOWN")  # reported nothing
    assert "plan unknown" in snapshot(store2, capsys)


def test_a_quota_probe_plan_is_never_overridden_by_the_account_plan(store, capsys):
    store.measured("cx", "X-7D", age=20, source="codex_app_server", evidence_ref="probe:cx:app-server")
    store.add("cx", "X-5H", "ABSENT", age=20, source="codex_app_server", evidence_ref="probe:cx:app-server", reason=NOT_REPORTED + "prolite")
    account(store, "cx", age=30, plan="other", plan_status="reported")
    out = snapshot(store, capsys)
    assert "plan prolite" in out and "plan other" not in out
    items = json.loads(snapshot(store, capsys, "--json"))["groups"]["capacity"]["data"]["observations"]
    assert all("display_plan" not in o for o in items)


def test_account_ttl_is_hours_so_a_two_hour_old_reading_is_not_aged(store, capsys):
    store.measured("cc", "C-7D", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage")
    account(store, "cc", age=2 * 3600, plan="pro", plan_status="reported")
    assert accounts_line(snapshot(store, capsys)) == "ACCOUNTS cc pro login ok"
    assert accounts_json(store, capsys)[0]["state"] == "MEASURED"


def test_stale_account_observation_is_marked_aged_in_every_view(store, capsys):
    store.measured("cc", "C-7D", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage")
    account(store, "cc", age=7 * 3600, plan="pro", plan_status="reported")
    assert accounts_line(snapshot(store, capsys)) == "ACCOUNTS cc pro login ok (aged)"
    item = accounts_json(store, capsys)[0]
    assert item["state"] == "STALE" and item["stored_state"] == "MEASURED" and item["age_seconds"] > 7 * 3600 - 5
    assert "STALE" in snapshot(store, capsys, "--expand")


def test_latest_observation_wins_and_history_is_kept(store, capsys):
    store.measured("cc", "C-7D", age=20, source="claude_cli_usage", evidence_ref="probe:cc:usage")
    account(store, "cc", age=7200, plan="pro", plan_status="reported")
    account(store, "cc", age=10, plan="max", plan_status="reported")
    assert accounts_line(snapshot(store, capsys)) == "ACCOUNTS cc max login ok"
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
