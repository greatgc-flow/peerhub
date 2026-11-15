"""Wiring of the API peers ca and oa (#378): kinds, profiles, guide, batch, quota and usage contracts. No provider is called."""
import json
import tomllib
from pathlib import Path

import pytest

from peerhub.extensions.adapters import api
from peerhub.extensions.adapters.api import ApiRuntimeTarget, CaRuntimeTarget, OaRuntimeTarget

REPO = Path(__file__).resolve().parents[3]
TOML = tomllib.loads((REPO / "peerhub" / "config_data" / "model-defaults.toml").read_text(encoding="utf-8"))
API_PROFILES = {pid: p for pid, p in TOML["profiles"].items() if pid.split(".", 1)[0] in ("ca", "oa")}


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "config"))


def test_ca_and_oa_are_builtin_peer_kinds_without_other_aliases():
    from peerhub.extensions.peer_kinds import ALIASES

    assert ALIASES["ca"] == "ca" and ALIASES["oa"] == "oa"
    assert sorted(alias for alias, kind in ALIASES.items() if kind in ("ca", "oa")) == ["ca", "oa"]
    assert api.API_TARGETS == {"ca": CaRuntimeTarget, "oa": OaRuntimeTarget}


@pytest.mark.parametrize("kind,cls", [("ca", CaRuntimeTarget), ("oa", OaRuntimeTarget)])
def test_targets_share_one_base_and_are_stateless(tmp_path, kind, cls):
    target = cls(tmp_path, profile=f"{kind}.standard")
    assert isinstance(target, ApiRuntimeTarget) and target.runtime_kind == kind
    assert target.stateless is True and target.resumable is False
    assert target.resume_session("any") == "unsupported"
    assert target.fingerprint() == f"adapter:{kind}:v1"
    assert target.create_session().startswith(f"{kind}-local-")
    assert target.session_binding() == {"profile": f"{kind}.standard", "model": API_PROFILES[f"{kind}.standard"]["model"],
                                        "effort": None, "writable": False}
    assert not (target.supports_interrupt or target.supports_terminate or target.supports_steer)


def test_packaged_api_profiles_have_three_tiers_a_card_and_no_pool():
    assert sorted(API_PROFILES) == ["ca.deepthink", "ca.effort", "ca.standard", "oa.deepthink", "oa.effort", "oa.standard"]
    for pid, profile in API_PROFILES.items():
        kind = pid.split(".", 1)[0]
        assert profile["selection_mode"] == "pinned" and "reasoning_effort" not in profile, pid
        assert (api.CA_MODEL if kind == "ca" else api.OA_MODEL).fullmatch(profile["model"]), pid
        assert "pools" not in profile and "pools_source" not in profile, pid
        assert f"inline the text; {kind} reads no file" in profile["use_for"], pid
        assert profile["evidence"] == ["#378"], pid
    assert not any(kind in TOML["observed_versions"] for kind in ("ca", "oa"))  # no CLI version drift check


def test_profile_list_and_explain_show_the_api_profiles(tmp_path, monkeypatch, capsys):
    from peerhub.cli.app import main
    from peerhub.cli.recovery import run_explain_cli

    assert main(["profile", "list", "--workspace", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "inline the text; ca reads no file" in out and "inline the text; oa reads no file" in out
    monkeypatch.chdir(tmp_path)
    for pid in ("ca.standard", "oa.deepthink"):
        assert run_explain_cli(pid) == 0
        assert capsys.readouterr().out.startswith(f"Profile: {pid}")


def test_guide_names_the_api_peers_their_rule_and_their_cap(capsys):
    from peerhub.cli.app import main

    assert main(["guide"]) == 0
    out = capsys.readouterr().out
    assert "inline the text; ca reads no file" in out
    assert "cc 3/cx 3/ag 1/ca 3/oa 3" in out
    assert len(out.splitlines()) <= 60 and len(out.replace("\r\n", "\n").encode("utf-8")) <= 5600


def test_suggest_never_names_an_api_peer(tmp_path):
    from peerhub.cli.monitor_view import _compact_suggestions  # pyright: ignore[reportPrivateUsage]
    from peerhub.extensions.adapters.profiles import list_profiles

    profiles = list_profiles(tmp_path)
    assert {"ca.standard", "oa.standard"} <= {p["profile"] for p in profiles}
    rows = [{"peer": peer, "pool": pool, "aged": False, "state": "ok", "remaining": 0.9, "reset_seconds": 3600.0}
            for p in profiles for peer in [p["profile"].split(".", 1)[0]] for pool in (p["fields"]["pools"]["value"] or [])]
    text = _compact_suggestions(profiles, rows)
    assert text.startswith("suggest: eco=") and "ca." not in text and "oa." not in text


def test_cached_readiness_lists_no_api_profile(tmp_path):
    from peerhub.extensions.diag_readiness import configured_profiles

    ids = {profile["id"] for profile in configured_profiles(tmp_path)}
    assert "cx.standard" in ids and not any(pid.split(".", 1)[0] in ("ca", "oa") for pid in ids)  # readiness is pool evidence


def test_quota_refresh_has_no_probe_for_an_api_peer(tmp_path):
    from peerhub.core.store import CoreStore
    from peerhub.extensions.quota_capture import refresh_quota

    db = tmp_path / "core.db"
    CoreStore(db)
    for peer in ("ca", "oa"):
        with pytest.raises(ValueError, match="quota providers: cx/codex, cc/claude, ag/agy"):
            refresh_quota(db, [peer], pollers={})


def test_batch_accepts_api_peers_refuses_writable_and_classifies_incomplete_replies(tmp_path):
    from peerhub.extensions import batch
    from peerhub.extensions.peer_kinds import ALIASES

    task = batch.Task("t1", "ca", "question", None, "ca.standard", False, 60.0)
    batch.validate_tasks([task, batch.Task("t2", "oa", "question", None, None, False, 60.0)], kind_of=ALIASES.get)
    with pytest.raises(batch.ManifestError, match="t3: ca has no workspace access"):
        batch.validate_tasks([batch.Task("t3", "ca", "question", None, None, True, 60.0)], kind_of=ALIASES.get)
    for status in ("truncated", "incomplete"):
        out = json.dumps({"status": status, "certainty": "STARTED", "response": None, "error": {"code": status, "message": "m"}})
        assert batch.classify_worker(1, out) == ("FAILED", status)
    rejected = json.dumps({"status": "error", "response": None, "error": {"code": "request_rejected", "message": "m"}})
    assert batch.classify_worker(2, rejected) == ("FAILED", "request_rejected")


def test_usage_contract_knows_the_api_peers():
    from peerhub.extensions.session_receipts import validate_provider_usage
    from peerhub.extensions.usage_normalization import normalize_provider_usage

    assert normalize_provider_usage("ca", {"input_tokens": 5, "output_tokens": 2}) == {"provider_input_tokens": 5, "output_tokens": 2}
    assert normalize_provider_usage("oa", {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}) == {
        "provider_input_tokens": 5, "output_tokens": 2, "provider_total_tokens": 7}
    for kind, source in (("ca", "ca.message.usage"), ("oa", "oa.response.usage")):
        reported = {"input_tokens": 5, "output_tokens": 2}
        candidate = {"schema_version": 1, "scope": "delivery", "source": source, "reported": reported,
                     "normalized": normalize_provider_usage(kind, reported), "coverage": {"state": "MEASURED", "reason": None}}
        checked = validate_provider_usage(candidate, kind)
        assert checked["coverage"]["state"] == "MEASURED" and checked["attribution"] == "delivery"
        assert validate_provider_usage(dict(candidate, source="cc.result.usage"), kind)["coverage"]["reason"] == "provider_scope_mismatch"


def test_messages_name_the_api_peers(tmp_path, capsys):
    from peerhub.cli.app import build_parser
    from peerhub.extensions import batch
    from peerhub.extensions.ask import ask

    with pytest.raises(ValueError, match="--adapter cx, cc, ag, ca or oa"):
        ask(tmp_path / "core.db", "nobody", "question")
    with pytest.raises(batch.ManifestError, match="--adapter cx, cc, ag, ca or oa"):
        batch.validate_tasks([batch.Task("t1", "nobody", "question", None, None, False, 60.0)], kind_of=lambda peer: None)
    with pytest.raises(SystemExit):
        build_parser().parse_args(["ask", "--help"])
    assert "cx/codex, cc/claude, ag/agy, ca, oa" in " ".join(capsys.readouterr().out.split())


def test_live_gate_can_select_the_api_peers():
    from tools import live_gate

    canary = (REPO / "tests" / "communication" / "live" / "test_api_peer_canary.py").read_text(encoding="utf-8")
    assert "pytest.mark.canary" in canary and "pytest.mark.live" not in canary  # outside the release catalog and the G3 stages
    assert "OPENAI_API_KEY" in canary and "LIVE-OA-KEY-MISSING" in canary
    parser_source = Path(live_gate.__file__).read_text(encoding="utf-8")
    assert '"cc", "cx", "ag", "ca", "oa"' in parser_source
    markers = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]["markers"]
    assert "ca" in markers and "oa" in markers
