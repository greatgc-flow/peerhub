"""#329: account / login / plan / token-expiry probes. Only normalized fields are ever persisted; no model turn, bounded."""
from __future__ import annotations

import io
import json
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from peerhub.extensions.observation import ObservationStore
from peerhub.extensions.observation_model import EvidenceState
from peerhub.extensions.quota_capture import refresh_quota
from peerhub.extensions.quota_probes import (
    AccountObserved,
    parse_claude_auth_status,
    parse_codex_account,
    poll_agy_account,
    poll_claude_account,
    poll_codex_account,
)
from tests.communication.harness.core import CoreHarness

pytestmark = [pytest.mark.unit]

EMAIL, ORG_ID, ORG_NAME, ACCT_ID, SECRET = "jane.doe@example.com", "org-1234-secret", "Acme Corp Private", "acct-9876-secret", "sk-live-TOKEN-xyz"
EXPIRES = int(datetime(2026, 11, 26, 5, 32, 50, tzinfo=timezone.utc).timestamp())
NOW = 1_790_000_000

# Real outputs captured 2026-11-10, identity values replaced by placeholders (shape and field names unchanged).
CC_AUTH = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "analyticsDisabled": False,
           "projectsDirectory": "<path>", "configDirectory": "<path>", "loginExpiresAt": "2026-11-26T05:32:50.405Z",
           "email": EMAIL, "orgId": ORG_ID, "orgName": ORG_NAME, "subscriptionType": "pro", "storedClaudeAiLogin": True,
           "planTier": "pro", "accessToken": SECRET}
CX_ACCOUNT = {"account": {"type": "chatgpt", "email": EMAIL, "planType": "prolite", "workspaceName": None}, "requiresOpenaiAuth": True,
              "workspaceRouting": {"chatgptAccountId": ACCT_ID, "backendOrigin": "https://chatgpt.com", "accountRoutingOverride": "NO_CONSTRAINT"}}
IDENTITY_FIELDS = {"email", "org", "orgid", "orgname", "account_id", "chatgptaccountid", "account_binding", "account_ref", "binding", "hash",
                   "token", "accesstoken", "workspacename"}


def assert_clean(payload: dict, *, extra=()):
    blob = json.dumps(payload)
    for secret in (EMAIL, ORG_ID, ORG_NAME, ACCT_ID, SECRET, "example.com", "chatgpt.com", *extra):
        assert secret not in blob, secret
    assert not IDENTITY_FIELDS & {key.lower() for key in payload}, "no identity-derived field may be stored or shown"


# ---- normalization / redaction ------------------------------------------------------------------

def test_claude_auth_status_is_normalized_and_redacted():
    payload = parse_claude_auth_status(json.dumps(CC_AUTH))
    assert payload is not None
    assert payload["logged_in"] is True and payload["plan"] == "pro" and payload["plan_status"] == "reported"
    assert payload["login_token_expires_at"] == EXPIRES and payload["login_expiry_status"] == "reported"
    assert payload["auth_method"] == "claude.ai"
    assert set(payload) == {"logged_in", "plan", "plan_status", "login_token_expires_at", "login_expiry_status", "auth_method",
                            "endpoint_access", "evidence_ref"}
    assert_clean(payload)
    assert payload["evidence_ref"] == "probe:cc:auth-status"


@pytest.mark.parametrize("extra", [{"authMethod": "api_key", "apiProvider": "firstParty"}, {"authMethod": "claude.ai", "apiProvider": "bedrock"},
                                   {"authMethod": "claude.ai", "apiProvider": "vertex"}, {"authMethod": "oauth_token", "apiProvider": "firstParty"}])
def test_non_subscription_logins_have_no_plan_or_token_expiry(extra):
    """API-key / Bedrock / Vertex logins: the provider has no plan or login-token expiry to expose, so n/a (not the 'could not determine' ?)."""
    auth = {k: v for k, v in CC_AUTH.items() if k not in {"subscriptionType", "planTier", "loginExpiresAt"}} | extra
    payload = parse_claude_auth_status(json.dumps(auth))
    assert payload["logged_in"] is True and payload["plan"] is None and payload["plan_status"] == "n/a"
    assert payload["login_token_expires_at"] is None and payload["login_expiry_status"] == "n/a"


@pytest.mark.parametrize("extra", [{"authMethod": "api_key", "apiProvider": "firstParty"}, {"authMethod": "claude.ai", "apiProvider": "bedrock"},
                                   {"authMethod": "claude.ai", "apiProvider": "vertex"}, {"authMethod": "oauth_token", "apiProvider": "firstParty"}])
def test_residual_subscription_fields_never_become_a_plan_for_non_subscription_logins(extra):
    """A leftover `subscriptionType`/`planTier`/`loginExpiresAt` from an earlier claude.ai login must not be reported for an API-key login."""
    auth = {**CC_AUTH, **extra}  # CC_AUTH still carries subscriptionType=pro, planTier=pro and a loginExpiresAt
    payload = parse_claude_auth_status(json.dumps(auth))
    assert payload["plan"] is None and payload["plan_status"] == "n/a"
    assert payload["login_token_expires_at"] is None and payload["login_expiry_status"] == "n/a"


def test_claude_ai_login_without_plan_or_expiry_stays_unknown():
    auth = {k: v for k, v in CC_AUTH.items() if k not in {"subscriptionType", "planTier", "loginExpiresAt"}}
    payload = parse_claude_auth_status(json.dumps(auth))
    assert payload["plan_status"] == "UNKNOWN" and payload["login_expiry_status"] == "UNKNOWN"


def test_real_claude_sample_reports_the_iso_login_expiry():
    payload = parse_claude_auth_status(json.dumps(CC_AUTH))
    assert payload["login_token_expires_at"] == EXPIRES and payload["plan"] == "pro" and payload["auth_method"] == "claude.ai"


@pytest.mark.parametrize("value, expected", [(EXPIRES, EXPIRES), (EXPIRES * 1000, EXPIRES), (float(EXPIRES), EXPIRES),
                                              ("2026-11-17T00:00:00Z", 1_794_873_600), ("tomorrow", None), (True, None), (-5, None)])
def test_login_expiry_formats(value, expected):
    payload = parse_claude_auth_status(json.dumps({**CC_AUTH, "loginExpiresAt": value}))
    assert payload["login_token_expires_at"] == expected
    assert payload["login_expiry_status"] == ("reported" if expected else "UNKNOWN")


@pytest.mark.parametrize("value", [253402300800000, 253402300800, 253402300800.0, 1e300, float("inf"), "253402300800000", "9999-12-31T23:59:60Z",
                                   "10000-01-01T00:00:00Z", "0001-01-01T00:00:00+14:00", "1969-12-31T23:59:59Z", 0, -1, 1e-9, 10**309, -10**309, 10**19, "1" + "0" * 309])
def test_unrepresentable_or_pre_epoch_expiry_is_unknown(value):
    """A token expiry must be a UTC instant between 1970 and 9999; anything else is 'could not be determined', never a crash later."""
    text = json.dumps({**CC_AUTH, "loginExpiresAt": value}, allow_nan=True)
    payload = parse_claude_auth_status(text)
    assert payload["login_token_expires_at"] is None and payload["login_expiry_status"] == "UNKNOWN", value


@pytest.mark.parametrize("value, expected", [(253402300799, 253402300799), ("9999-12-31T23:59:59Z", 253402300799), (1, 1), (2, 2),
                                             ("1970-01-01T00:00:01Z", 1)])
def test_the_representable_range_edges_are_accepted(value, expected):
    payload = parse_claude_auth_status(json.dumps({**CC_AUTH, "loginExpiresAt": value}))
    assert payload["login_token_expires_at"] == expected and payload["login_expiry_status"] == "reported"


def test_missing_expiry_is_unknown_and_does_not_mean_logged_out():
    auth = {k: v for k, v in CC_AUTH.items() if k != "loginExpiresAt"}
    payload = parse_claude_auth_status(json.dumps(auth))
    assert payload["logged_in"] is True and payload["login_token_expires_at"] is None and payload["login_expiry_status"] == "UNKNOWN"


def test_logged_out_has_no_applicable_plan():
    payload = parse_claude_auth_status(json.dumps({"loggedIn": False}))
    assert payload["logged_in"] is False and payload["plan"] is None and payload["plan_status"] == "n/a"


def test_logged_in_without_plan_is_unknown_not_guessed():
    auth = {k: v for k, v in CC_AUTH.items() if k not in {"subscriptionType", "planTier"}}
    payload = parse_claude_auth_status(json.dumps(auth))
    assert payload["plan"] is None and payload["plan_status"] == "UNKNOWN"


def test_plan_tier_fallback_and_plan_text_is_sanitized():
    auth = {k: v for k, v in CC_AUTH.items() if k != "subscriptionType"} | {"planTier": "max 5x\x1b[31m; org=evil" + "x" * 80}
    payload = parse_claude_auth_status(json.dumps(auth))
    assert len(payload["plan"]) <= 24 and all(32 <= ord(c) < 127 for c in payload["plan"]) and ";" not in payload["plan"]


@pytest.mark.parametrize("text", ["", "not json", "[1]", '"x"', "{"])
def test_unparseable_auth_status_is_none(text):
    assert parse_claude_auth_status(text) is None


def test_non_boolean_logged_in_is_unknown():
    payload = parse_claude_auth_status(json.dumps({**CC_AUTH, "loggedIn": "yes"}))
    assert payload["logged_in"] == "UNKNOWN"


def test_codex_account_is_normalized_and_redacted():
    payload = parse_codex_account(CX_ACCOUNT)
    assert payload["logged_in"] is True and payload["plan"] == "prolite" and payload["plan_status"] == "reported"
    assert payload["login_token_expires_at"] is None and payload["login_expiry_status"] == "n/a"  # provider does not expose it
    assert payload["auth_method"] == "chatgpt"
    assert_clean(payload)


def test_codex_logged_out_and_unknown_shapes():
    assert parse_codex_account({"account": None, "requiresOpenaiAuth": True})["logged_in"] is False
    assert parse_codex_account({"requiresOpenaiAuth": True})["logged_in"] == "UNKNOWN"
    assert parse_codex_account({"account": "weird"})["logged_in"] == "UNKNOWN"
    assert parse_codex_account([]) is None
    api = parse_codex_account({"account": {"type": "apiKey"}})
    assert api["logged_in"] is True and api["plan"] is None and api["plan_status"] == "n/a"
    no_plan = parse_codex_account({"account": {"type": "chatgpt", "email": EMAIL}})
    assert no_plan["plan_status"] == "UNKNOWN"


def test_no_identity_derived_value_exists_anywhere(monkeypatch):
    """Nothing identity-derived is stored: no hash, binding or reference. The same account in two installs yields identical payloads."""
    import peerhub.extensions.quota_probes as probes
    assert not hasattr(probes, "account_binding")
    other = {**CC_AUTH, "email": "someone.else@example.org", "orgId": "org-other", "orgName": "Other Org"}
    assert parse_claude_auth_status(json.dumps(CC_AUTH)) == parse_claude_auth_status(json.dumps(other))
    other_cx = {**CX_ACCOUNT, "account": {**CX_ACCOUNT["account"], "email": "x@y.example"},
                "workspaceRouting": {**CX_ACCOUNT["workspaceRouting"], "chatgptAccountId": "different"}}
    assert parse_codex_account(CX_ACCOUNT) == parse_codex_account(other_cx)


# ---- probes: spawn shape, account root, failures ------------------------------------------------

class Proc:
    pid = 4242

    def __init__(self, stdout="", returncode=0, raises=None):
        self._out, self.returncode, self._raises = stdout, returncode, raises

    def communicate(self, timeout=None):
        if self._raises:
            raise self._raises
        return self._out, "stderr leaks " + EMAIL


def patch_cc(monkeypatch, proc):
    seen = {}

    def popen(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        if isinstance(proc, Exception):
            raise proc
        return proc

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr("peerhub.extensions.quota_probes._real_command", lambda peer, _s=None: ["claude.exe"] if peer == "cc" else None)
    monkeypatch.setattr("peerhub.extensions.quota_probes._cleanup_probe", lambda p: None)
    return seen


def test_cc_probe_runs_auth_status_without_a_model_turn(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("PEERHUB_CLAUDE_CONFIG_DIR", raising=False)
    seen = patch_cc(monkeypatch, Proc(json.dumps(CC_AUTH)))
    reading = poll_claude_account(clock=lambda: NOW, sys_dir=tmp_path / "_sys", deadline_sec=3)
    assert isinstance(reading, AccountObserved) and reading.state == EvidenceState.MEASURED
    assert seen["argv"] == ["claude.exe", "auth", "status"] and seen["kw"]["stdin"] == subprocess.DEVNULL
    assert reading.observed_at == NOW and reading.evidence_ref == "probe:cc:auth-status"
    assert_clean(reading.payload)


def test_cc_probe_uses_the_quota_probes_account_root_and_never_another_login(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "ambient"))
    seen = patch_cc(monkeypatch, Proc(json.dumps(CC_AUTH)))
    poll_claude_account(clock=lambda: NOW, sys_dir=tmp_path / "_sys")
    assert seen["kw"]["env"]["CLAUDE_CONFIG_DIR"] == str(tmp_path / "ambient")  # the existing quota-probe handling
    monkeypatch.setenv("PEERHUB_CLAUDE_CONFIG_DIR", str(tmp_path / "peerhub"))
    poll_claude_account(clock=lambda: NOW, sys_dir=tmp_path / "_sys")
    assert seen["kw"]["env"]["CLAUDE_CONFIG_DIR"] == str(tmp_path / "peerhub")


def test_cc_logged_out_exit_code_one_still_reports_logged_out(monkeypatch, tmp_path):
    patch_cc(monkeypatch, Proc(json.dumps({"loggedIn": False}), returncode=1))
    reading = poll_claude_account(clock=lambda: NOW, sys_dir=tmp_path / "_sys")
    assert reading.state == EvidenceState.MEASURED and reading.payload["logged_in"] is False


@pytest.mark.parametrize("proc, reason", [
    (Proc(raises=subprocess.TimeoutExpired("claude", 3)), "timeout"),
    (OSError("boom " + EMAIL), "spawn_failed"),
    (Proc("garbage " + EMAIL), "unparseable"),
])
def test_cc_probe_failures_are_unknown_with_a_fixed_reason_and_leak_nothing(monkeypatch, tmp_path, proc, reason):
    patch_cc(monkeypatch, proc)
    reading = poll_claude_account(clock=lambda: NOW, sys_dir=tmp_path / "_sys")
    assert reading.state == EvidenceState.ERROR
    assert reading.payload == {"condition": "account_probe_failed", "reason": reason, "evidence_ref": "probe:cc:auth-status"}


def test_cc_missing_cli_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr("peerhub.extensions.quota_probes._real_command", lambda peer, _s=None: None)
    reading = poll_claude_account(clock=lambda: NOW, sys_dir=tmp_path / "_sys")
    assert reading.state == EvidenceState.UNAVAILABLE and reading.payload["reason"] == "cli_not_found"


def fake_app_server(monkeypatch, *messages, spawn_raises=None):
    stdin = io.StringIO()
    seen = {}

    class FakeProc:
        pid = 777
        stdout = io.StringIO("".join(json.dumps(m) + "\n" for m in messages))

        def poll(self):
            return None

    FakeProc.stdin = stdin

    def popen(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        if spawn_raises:
            raise spawn_raises
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr("peerhub.extensions.quota_probes._real_command", lambda peer, _s=None: ["codex.exe"] if peer == "cx" else None)
    monkeypatch.setattr("peerhub.extensions.quota_probes._cleanup_probe", lambda p: None)
    return seen, stdin


def test_cx_probe_reads_account_without_refreshing_tokens_or_running_a_turn(monkeypatch, tmp_path):
    seen, stdin = fake_app_server(monkeypatch, {"id": 0, "result": {}}, {"id": 1, "result": CX_ACCOUNT})
    reading = poll_codex_account(clock=lambda: NOW, sys_dir=tmp_path / "_sys", deadline_sec=2)
    assert reading.state == EvidenceState.MEASURED and reading.evidence_ref == "probe:cx:account-read"
    assert reading.payload["plan"] == "prolite" and reading.payload["logged_in"] is True
    assert_clean(reading.payload)
    sent = [json.loads(line) for line in stdin.getvalue().splitlines()]
    assert [m.get("method") for m in sent] == ["initialize", "initialized", "account/read"]
    assert sent[2]["params"] == {"refreshToken": False}
    assert seen["argv"] == ["codex.exe", "app-server"]


def test_cx_probe_uses_the_quota_probes_account_root(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ambient"))
    seen, _ = fake_app_server(monkeypatch, {"id": 0, "result": {}}, {"id": 1, "result": CX_ACCOUNT})
    poll_codex_account(clock=lambda: NOW, sys_dir=tmp_path / "_sys", deadline_sec=2)
    assert seen["kw"]["env"]["CODEX_HOME"] == str(tmp_path / "ambient")


@pytest.mark.parametrize("messages, reason", [
    ([], "no_response"),
    ([{"id": 0, "result": {}}], "no_response"),
    ([{"id": 0, "result": {}}, {"id": 1, "error": {"message": "nope " + EMAIL}}], "no_response"),
])
def test_cx_probe_failures_are_unknown(monkeypatch, tmp_path, messages, reason):
    fake_app_server(monkeypatch, *messages)
    reading = poll_codex_account(clock=lambda: NOW, sys_dir=tmp_path / "_sys", deadline_sec=0.6)
    assert reading.state == EvidenceState.ERROR and reading.payload["reason"] == reason and reading.payload["condition"] == "account_probe_failed"


def test_cx_spawn_failure(monkeypatch, tmp_path):
    fake_app_server(monkeypatch, spawn_raises=OSError("no " + EMAIL))
    reading = poll_codex_account(clock=lambda: NOW, sys_dir=tmp_path / "_sys")
    assert reading.state == EvidenceState.ERROR and reading.payload["reason"] == "spawn_failed"
    assert EMAIL not in json.dumps(reading.payload)


AGY_CREDITS = json.dumps({"status": "SUCCESS", "usage": {"total_tokens": 0}, "num_turns": 0,
                          "command": {"name": "credits", "data": {"balance": 0, "email": EMAIL}}})


def patch_agy(monkeypatch, stdout, returncode=0, raises=None):
    seen = {}

    def run(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        if raises:
            raise raises
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")

    monkeypatch.delenv("PEERHUB_AG_STATUSLINE_LOG", raising=False)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr("peerhub.extensions.quota_probes._real_command", lambda peer, _s=None: ["agy.exe"] if peer == "ag" else None)
    return seen


def test_ag_credits_success_shows_endpoint_access_only(monkeypatch, tmp_path):
    seen = patch_agy(monkeypatch, AGY_CREDITS)
    reading = poll_agy_account(clock=lambda: NOW, sys_dir=tmp_path / "_sys")
    assert seen["argv"][1:5] == ["-p", "/credits", "--output-format", "json"]
    assert reading.state == EvidenceState.MEASURED and reading.evidence_ref == "probe:ag:credits"
    assert reading.payload["endpoint_access"] == "ok"
    assert reading.payload["plan"] is None and reading.payload["plan_status"] == "n/a"
    assert reading.payload["logged_in"] == "UNKNOWN"
    assert reading.payload["login_expiry_status"] == "n/a"
    assert_clean(reading.payload)


@pytest.mark.parametrize("stdout", [
    json.dumps({"usage": {"total_tokens": 5}, "command": {"name": "credits"}}),   # tokens were spent
    json.dumps({"usage": {"total_tokens": 0}, "num_turns": 1, "command": {"name": "credits"}}),
    json.dumps({"usage": {"total_tokens": 0}, "command": {"name": "usage"}}),     # a different command
    json.dumps({"usage": {"total_tokens": 0}}),                                    # not a slash command (possible model turn)
])
def test_ag_credits_envelope_violations_are_not_trusted(monkeypatch, tmp_path, stdout):
    patch_agy(monkeypatch, stdout)
    reading = poll_agy_account(clock=lambda: NOW, sys_dir=tmp_path / "_sys")
    assert reading.state == EvidenceState.ERROR and reading.payload["condition"] == "account_probe_failed"
    assert reading.payload.get("endpoint_access") is None


def test_ag_token_use_is_flagged_for_the_existing_monitor_stop(monkeypatch, tmp_path):
    patch_agy(monkeypatch, json.dumps({"usage": {"total_tokens": 5}, "command": {"name": "credits"}}))
    reading = poll_agy_account(clock=lambda: NOW, sys_dir=tmp_path / "_sys")
    assert reading.payload["evidence_ref"] == "agy_usage_consumed_tokens" and reading.warning


@pytest.mark.parametrize("stdout, reason", [
    (json.dumps({"status": "SUCCESS", "command": {"name": "credits"}}), "unverified_usage"),                                   # no counters at all
    (json.dumps({"status": "SUCCESS", "usage": {"input_tokens": 0}, "command": {"name": "credits"}}), "unverified_usage"),    # no total
    (json.dumps({"status": "SUCCESS", "usage": {"total_tokens": 0}, "command": {"name": "credits"}}), "unverified_usage"),    # no num_turns
    (json.dumps({"status": "ERROR", "usage": {"total_tokens": 0}, "num_turns": 0, "command": {"name": "credits"}}), "command_failed"),
    (json.dumps({"usage": {"total_tokens": 0}, "num_turns": 0, "command": {"name": "credits"}}), "command_failed"),           # no status
])
def test_ag_endpoint_access_needs_a_successful_envelope_with_explicit_zero_usage(monkeypatch, tmp_path, stdout, reason):
    patch_agy(monkeypatch, stdout)
    reading = poll_agy_account(clock=lambda: NOW, sys_dir=tmp_path / "_sys")
    assert reading.state == EvidenceState.ERROR and reading.payload["reason"] == reason
    assert reading.payload.get("endpoint_access") is None


@pytest.mark.parametrize("kw", [dict(stdout="", returncode=1), dict(stdout="nonsense"), dict(stdout="", raises=subprocess.TimeoutExpired("agy", 3))])
def test_ag_failures_are_unknown_not_credits_ok(monkeypatch, tmp_path, kw):
    patch_agy(monkeypatch, **kw)
    reading = poll_agy_account(clock=lambda: NOW, sys_dir=tmp_path / "_sys")
    assert reading.state == EvidenceState.ERROR and reading.payload.get("endpoint_access") is None


def test_ag_does_not_read_the_gemini_accounts_file(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / ".gemini").mkdir(parents=True)
    (home / ".gemini" / "google_accounts.json").write_text(json.dumps({"active": EMAIL}), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    patch_agy(monkeypatch, AGY_CREDITS)
    reading = poll_agy_account(clock=lambda: NOW, sys_dir=tmp_path / "_sys")
    assert_clean(reading.payload)


def test_ag_real_credits_sample_is_accepted_and_its_prose_is_not_stored(monkeypatch, tmp_path):
    sample = json.dumps({"conversation_id": "<id>", "status": "SUCCESS", "response": "Remaining credits\t0\nUpgrade\thttps://antigravity.google/g1-upgrade\n",
                         "duration_seconds": 0.4, "num_turns": 0,
                         "usage": {"input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "cache_read_tokens": 0, "total_tokens": 0},
                         "command": {"name": "credits", "data": {}}})
    patch_agy(monkeypatch, sample)
    reading = poll_agy_account(clock=lambda: NOW, sys_dir=tmp_path / "_sys")
    assert reading.state == EvidenceState.MEASURED and reading.payload["endpoint_access"] == "ok"
    assert "Remaining" not in json.dumps(reading.payload) and "antigravity" not in json.dumps(reading.payload)


def test_ag_subprocess_is_bounded_by_the_shared_deadline_without_extra_slack(monkeypatch, tmp_path):
    seen = patch_agy(monkeypatch, AGY_CREDITS)
    poll_agy_account(clock=lambda: NOW, sys_dir=tmp_path / "_sys", deadline_sec=7)
    assert seen["kw"]["timeout"] == 7


def test_ag_statusline_override_disables_the_spawning_probe(monkeypatch, tmp_path):
    seen = patch_agy(monkeypatch, AGY_CREDITS)
    monkeypatch.setenv("PEERHUB_AG_STATUSLINE_LOG", str(tmp_path / "log.json"))
    assert poll_agy_account(clock=lambda: NOW, sys_dir=tmp_path / "_sys") is None
    assert "argv" not in seen


# ---- collection wiring (refresh) ----------------------------------------------------------------

def refresh_env(tmp_path):
    h = CoreHarness(tmp_path / "ws")
    ObservationStore(h.db_path)
    return h.db_path


def account_rows(db):
    with sqlite3.connect(db) as c:
        return c.execute("SELECT subject_ref, state, resource_pool_ref, payload_json, source FROM observations "
                         "WHERE kind='account' ORDER BY capture_seq").fetchall()


def test_refresh_persists_only_normalized_account_fields(tmp_path):
    db = refresh_env(tmp_path)
    pollers = {}
    readings = {"cc": AccountObserved("probe:cc:auth-status", EvidenceState.MEASURED, "claude_auth_status", NOW,
                                      parse_claude_auth_status(json.dumps(CC_AUTH)))}
    result = refresh_quota(db, ["cc"], pollers=pollers, account_pollers={"cc": lambda **kw: readings["cc"]}, version_checker=lambda k: None)
    rows = account_rows(db)
    assert [(r[0], r[1], r[2]) for r in rows] == [("cc", "MEASURED", None)]
    stored = json.loads(rows[0][3])
    assert_clean(stored)
    assert stored["plan"] == "pro" and rows[0][4] == "claude_auth_status"
    assert any(o["kind"] == "account" for o in result["observations"])
    assert EMAIL not in json.dumps(result) and ORG_NAME not in json.dumps(result)


def test_refresh_account_failure_is_stored_as_unknown_and_never_blocks_quota(tmp_path):
    db = refresh_env(tmp_path)

    def boom(**kw):
        raise RuntimeError("secret " + EMAIL)

    result = refresh_quota(db, ["cc"], pollers={}, account_pollers={"cc": boom}, version_checker=lambda k: None)
    rows = account_rows(db)
    assert len(rows) == 1 and rows[0][1] == "ERROR"
    assert json.loads(rows[0][3]) == {"condition": "account_probe_failed", "reason": "probe_exception", "evidence_ref": "probe:cc:auth-status",
                                      "error_type": "RuntimeError"}
    assert EMAIL not in json.dumps(result)


def test_refresh_with_injected_quota_pollers_never_runs_real_account_probes(tmp_path, monkeypatch):
    db = refresh_env(tmp_path)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("spawned")))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("spawned")))
    refresh_quota(db, ["cc"], pollers={}, version_checker=lambda k: None)
    assert account_rows(db) == []


def test_refresh_isolates_accounts_per_peer(tmp_path):
    db = refresh_env(tmp_path)
    cc = AccountObserved("probe:cc:auth-status", EvidenceState.MEASURED, "claude_auth_status", NOW, parse_claude_auth_status(json.dumps(CC_AUTH)))
    cx = AccountObserved("probe:cx:account-read", EvidenceState.MEASURED, "codex_app_server", NOW, parse_codex_account(CX_ACCOUNT))
    refresh_quota(db, ["cc", "cx"], pollers={}, account_pollers={"cc": lambda **kw: cc, "cx": lambda **kw: cx}, version_checker=lambda k: None)
    by_peer = {r[0]: json.loads(r[3]) for r in account_rows(db)}
    assert by_peer["cc"]["plan"] == "pro" and by_peer["cx"]["plan"] == "prolite"
    assert by_peer["cc"]["evidence_ref"] != by_peer["cx"]["evidence_ref"]
    for stored in by_peer.values():
        assert_clean(stored)


def live_reading(kind="cc", **payload):
    ref = {"cc": "probe:cc:auth-status", "cx": "probe:cx:account-read", "ag": "probe:ag:credits"}[kind]
    base = parse_claude_auth_status(json.dumps(CC_AUTH)) if kind == "cc" else parse_codex_account(CX_ACCOUNT)
    return AccountObserved(ref, EvidenceState.MEASURED, f"{kind}_test", int(time.time()), {**base, **payload})


def test_account_probes_run_in_parallel_inside_one_shared_budget(tmp_path):
    db = refresh_env(tmp_path)
    starts = []

    def slow(kind):
        def poll():
            starts.append(time.monotonic())
            time.sleep(0.6)
            return live_reading(kind)
        return poll

    began = time.monotonic()
    refresh_quota(db, ["cc", "cx", "ag"], pollers={}, deadline_sec=3, version_checker=lambda k: None,
                  account_pollers={"cc": slow("cc"), "cx": slow("cx"), "ag": slow("cc")})
    assert time.monotonic() - began < 1.6  # sequential would take >= 1.8s
    assert max(starts) - min(starts) < 0.4  # all started together
    assert sorted(r[0] for r in account_rows(db)) == ["ag", "cc", "cx"]


def test_account_probes_overlap_the_serial_quota_probes_instead_of_following_them(tmp_path):
    """The account budget runs alongside the quota loop: three 0.5 s quota probes plus 0.9 s account probes finish in ~1.5 s, not ~2.4 s."""
    db = refresh_env(tmp_path)

    def slow_quota(**kw):
        time.sleep(0.5)
        return ()

    def slow_account(kind):
        def poll():
            time.sleep(0.9)
            return live_reading(kind)
        return poll

    began = time.monotonic()
    refresh_quota(db, ["cc", "cx", "ag"], pollers={"cc": slow_quota, "cx": slow_quota, "ag": slow_quota}, deadline_sec=3,
                  version_checker=lambda k: None, account_pollers={"cc": slow_account("cc"), "cx": slow_account("cx"), "ag": slow_account("cc")})
    assert time.monotonic() - began < 2.1
    assert sorted(r[0] for r in account_rows(db)) == ["ag", "cc", "cx"]


def test_a_hung_account_probe_cannot_exceed_the_shared_deadline(tmp_path):
    db = refresh_env(tmp_path)
    began = time.monotonic()
    refresh_quota(db, ["cc", "cx"], pollers={}, deadline_sec=0.4, version_checker=lambda k: None,
                  account_pollers={"cc": lambda: (time.sleep(4), live_reading("cc"))[1], "cx": lambda: live_reading("cx")})
    assert time.monotonic() - began < 2.5
    rows = {r[0]: (r[1], json.loads(r[3])) for r in account_rows(db)}
    assert rows["cx"][0] == "MEASURED"
    assert rows["cc"][0] == "ERROR" and rows["cc"][1]["reason"] == "timeout" and rows["cc"][1]["condition"] == "account_probe_failed"


def probe_twice(tmp_path, name, first, second):
    """Two refreshes against one store; returns which readings were actually probed."""
    db = refresh_env(tmp_path / name)
    calls = []

    def poller(reading):
        def poll():
            calls.append(reading.state)
            return reading
        return poll

    for reading in (first, second):
        refresh_quota(db, ["cc"], pollers={}, version_checker=lambda k: None, account_pollers={"cc": poller(reading)})
    return calls, db


def test_a_fresh_account_observation_skips_its_probe(tmp_path):
    good = live_reading("cc")
    calls, db = probe_twice(tmp_path, "fresh", good, good)
    assert calls == [EvidenceState.MEASURED] and len(account_rows(db)) == 1  # second refresh: still fresh within the account TTL


def test_a_failed_account_probe_is_retried_after_a_short_backoff_not_every_cycle(tmp_path):
    good = live_reading("cc")
    payload = {"condition": "account_probe_failed", "reason": "timeout"}
    just_failed = AccountObserved(good.evidence_ref, EvidenceState.ERROR, "x", int(time.time()), payload)
    calls, db = probe_twice(tmp_path, "backoff", just_failed, good)
    assert calls == [EvidenceState.ERROR] and len(account_rows(db)) == 1  # a monitor cycle a minute later does not re-spawn or add rows
    earlier = AccountObserved(good.evidence_ref, EvidenceState.ERROR, "x", int(time.time()) - 400, payload)
    calls, _ = probe_twice(tmp_path, "retry", earlier, good)
    assert calls == [EvidenceState.ERROR, EvidenceState.MEASURED]


def test_an_account_observation_older_than_the_account_ttl_is_probed_again(tmp_path):
    good = live_reading("cc")
    old = AccountObserved(good.evidence_ref, EvidenceState.MEASURED, "x", int(time.time()) - 7 * 3600, good.payload)
    calls, _ = probe_twice(tmp_path, "old", old, good)
    assert calls == [EvidenceState.MEASURED, EvidenceState.MEASURED]


def test_account_freshness_policy_defaults_to_hours_not_minutes():
    from peerhub.extensions.observation_model import FreshnessPolicy
    policy = FreshnessPolicy()
    assert policy.ttl_for("account") == 6 * 3600 and policy.ttl_for("quota") == 300


def test_aliases_of_one_provider_are_probed_once_and_each_peer_gets_its_row(tmp_path):
    db = refresh_env(tmp_path)
    calls = []

    def poll():
        calls.append(1)
        return live_reading("cc")

    refresh_quota(db, ["cc", "claude"], pollers={}, version_checker=lambda k: None, account_pollers={"cc": poll})
    assert len(calls) == 1
    assert sorted(r[0] for r in account_rows(db)) == ["cc", "claude"]


def test_refresh_none_reading_writes_nothing(tmp_path):
    db = refresh_env(tmp_path)
    refresh_quota(db, ["ag"], pollers={}, account_pollers={"ag": lambda **kw: None}, version_checker=lambda k: None)
    assert account_rows(db) == []
