"""Wave 8: LIVE-* real provider canary. Opt-in only (PEERHUB_LIVE=1 AND `-m live`); one minimal call per provider, no retries.

Oracles are structural (response Record + TERMINAL evidence, versions/capabilities reported, project tree unchanged); the model's
text is never compared beyond non-empty. Evidence retained under docs/m1_impl/live_evidence/<date>.json is sanitized (no prompts,
no response text, no secrets)."""
import json
from datetime import datetime, timezone

import pytest

from tests.communication.live import live_support as ls

pytestmark = [pytest.mark.live, pytest.mark.provider, pytest.mark.timeout(600)]
STAMP = datetime.now(timezone.utc)


def _require_available(kind: str) -> dict:
    d = ls.discovery(kind)
    ls.merge_evidence(kind, "discovery", {k: d[k] for k in ("observed_at", "available", "version", "version_line", "reason", "capabilities")})
    if not d["available"]:  # recorded as evidence; no retry loop
        pytest.skip(f"LIVE-PROVIDER-UNAVAILABLE[provider={kind};reason={d['reason']}]")
    return d


def _canary_oracle(kind: str) -> None:
    _require_available(kind)
    c = ls.canary(kind)
    ls.merge_evidence(kind, "canary", c)
    assert c["status"] == "delivered" and c["certainty"] == "TERMINAL", c
    assert c["response_record"] and c["response_chars"] > 0
    assert c["evidence_kinds"][-3:] == ["terminal", "response_appended", "offset_acked"]
    assert c["profile"] == f"{kind}.standard"


@pytest.mark.cc
@pytest.mark.catalog_id("LIVE-CC-001")
def test_live_cc_001_claude_discovery_and_minimal_canary():
    _canary_oracle("cc")


@pytest.mark.cx
@pytest.mark.catalog_id("LIVE-CX-001")
def test_live_cx_001_codex_discovery_and_minimal_canary():
    _canary_oracle("cx")


@pytest.mark.ag
@pytest.mark.catalog_id("LIVE-AG-001")
def test_live_ag_001_agy_discovery_and_minimal_canary():
    _canary_oracle("ag")


@pytest.mark.catalog_id("LIVE-004")
def test_live_004_capability_mismatch_is_reported_never_synthesized():
    checked = 0
    for kind in ls.PROVIDERS:
        d = ls.discovery(kind)
        caps = d["capabilities"]
        if d["available"]:
            checked += 1
            assert caps["resume"]["status"] == "unsupported" and caps["resume"]["fallback"]
            assert caps["resume"]["cli_status"] in ("supported", "unsupported", "unavailable")
            adapter, _ = ls.make_adapter(kind, ls.ROOT / "docs")  # never spawned: capability-aware path only
            assert adapter.resumable is False and adapter.resume_session("any-vendor-id") == "unsupported"
            assert adapter.supports_terminate and caps["terminate"]["status"] == "supported"
            assert not (adapter.supports_interrupt or adapter.supports_steer)
        else:
            assert {c["status"] for c in caps.values()} == {"unavailable"} and d["reason"]  # unavailable is reported, not faked
        assert all(caps[c]["status"] in ("unsupported", "unavailable") for c in ("interrupt", "steer"))
        ls.merge_evidence(kind, "capabilities", caps)
    assert checked >= 1, "no provider available: nothing could be checked"


@pytest.mark.catalog_id("LIVE-005")
def test_live_005_observed_cli_version_is_timestamped_evidence():
    seen = 0
    for kind in ls.PROVIDERS:
        d = ls.discovery(kind)
        ts = datetime.fromisoformat(d["observed_at"])
        assert ts.tzinfo is not None and STAMP.replace(microsecond=0) <= ts <= datetime.now(timezone.utc)
        if d["available"]:
            seen += 1
            assert d["version"] and d["version"] in d["version_line"]  # parsed from the observed output, not a constant
        ls.merge_evidence(kind, "discovery", {k: d[k] for k in ("observed_at", "available", "version", "version_line", "reason", "capabilities")})
    assert seen >= 1


@pytest.mark.isolation
@pytest.mark.catalog_id("LIVE-006")
def test_live_006_isolated_workspace_and_no_project_mutation():
    doc = ls.load_evidence()
    canaries = {k: v["canary"] for k, v in doc["providers"].items() if "canary" in v}
    assert canaries, "run the provider canaries first (they record their isolation facts)"
    for kind, c in canaries.items():
        assert c["workspace_outside_project"] is True, kind
        assert c["project_tree_changed"] == [], (kind, c["project_tree_changed"])
        assert c["provider_ws_files_after"] == [], (kind, c["provider_ws_files_after"])
    # control: the snapshot oracle does detect a change inside the project tree (not vacuous)
    probe = ls.ROOT / "docs" / "m1_impl" / "live_evidence" / ".nonexistent"
    before = ls.tree_snapshot()
    marker = ls.ROOT / "tests" / "communication" / "live" / "_probe.tmp"
    try:
        marker.write_text("x", encoding="utf-8")
        assert ls.diff_snapshots(before, ls.tree_snapshot()) == [str(marker.relative_to(ls.ROOT))]
    finally:
        marker.unlink(missing_ok=True)
    assert ls.diff_snapshots(before, ls.tree_snapshot()) == [] and not probe.exists()
    assert json.dumps(doc).find(ls.PROMPT) == -1  # the retained evidence carries no prompt text
