"""Pure renderer and CLI tests for monitor compact 'peers' dependency view.

Failing RED phase suite for PR1 (docs/design/monitor-peers-view-2026-10-16.md).
Tests pure renderer fixtures and preference catalog contracts without provider calls.
"""
from __future__ import annotations

import json
import re
from typing import Any

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.diag]


@pytest.fixture
def peers_frame() -> dict[str, Any]:
    """Pure renderer fixture mimicking schema 2.0 snapshot with cc, cx, ag facts."""
    return {
        "status": "OK",
        "read_at": 1000.0,
        "selected_groups": ["peers"],
        "groups": {
            "overview": {
                "status": "OK",
                "data": {
                    "peers": [
                        {"peer_id": "cc", "display_name": "Claude", "adapter_ref": "claude"},
                        {"peer_id": "cx", "display_name": "Codex", "adapter_ref": "codex"},
                        {"peer_id": "ag", "display_name": "Antigravity", "adapter_ref": "antigravity"},
                    ],
                    "sample_size": 3,
                    "truncated": False,
                },
            },
            "execution": {
                "status": "OK",
                "data": {"scopes": [], "sample_size": 0, "truncated": False},
            },
            "peers": {
                "status": "OK",
                "data": {
                    "peers": [
                        {
                            "peer_id": "cc",
                            "quota": {
                                "pool_ref": "5h",
                                "percent": 82,
                                "state": "MEASURED",
                                "age_seconds": 720.0,
                            },
                            "profile": {
                                "profile": "cc.effort",
                                "model": "claude-sonnet-5-5",
                                "source": "workspace",
                            },
                            "session": {
                                "generation": 1,
                                "execution_mode": "reuse",
                            },
                            "context": {
                                "state": "UNKNOWN",
                                "percent": None,
                            },
                        },
                        {
                            "peer_id": "cx",
                            "quota": {
                                "pool_ref": "1d",
                                "percent": 65,
                                "state": "MEASURED",
                                "age_seconds": 240.0,
                            },
                            "profile": {
                                "profile": "cx.effort",
                                "model": "gpt-6.1-sol",
                                "source": "global",
                            },
                            "session": {
                                "generation": 0,
                                "execution_mode": "fresh",
                            },
                            "context": {
                                "state": "MEASURED",
                                "percent": 22.0,
                                "occupancy_tokens": 45000,
                            },
                        },
                        {
                            "peer_id": "ag",
                            "quota": {
                                "pool_ref": "unlim",
                                "source": "doc",
                                "state": "MEASURED",
                            },
                            "profile": {
                                "profile": "ag.effort",
                                "model": "gemini-3.8-flash-med",
                                "source": "packaged",
                            },
                            "session": None,
                            "context": {
                                "state": "UNKNOWN",
                                "percent": None,
                            },
                        },
                    ]
                },
            },
            "sessions": {
                "status": "OK",
                "data": {
                    "items": [
                        {
                            "peer_id": "cc",
                            "generation": 1,
                            "execution_mode": "reuse",
                            "resumable": True,
                            "configured_binding": {"profile": "cc.effort", "model": "claude-sonnet-5-5"},
                        },
                        {
                            "peer_id": "cx",
                            "generation": 0,
                            "execution_mode": "fresh",
                            "resumable": False,
                            "configured_binding": {"profile": "cx.effort", "model": "gpt-6.1-sol"},
                        },
                    ],
                    "sample_size": 2,
                    "truncated": False,
                },
            },
            "runtime": {
                "status": "OK",
                "data": {
                    "sessions": [
                        {"peer_id": "cc", "configured_binding": {"profile": "cc.effort"}, "current_profile": {"source": "workspace"}},
                        {"peer_id": "cx", "configured_binding": {"profile": "cx.effort"}, "current_profile": {"source": "global"}},
                        {"peer_id": "ag", "configured_binding": {"profile": "ag.effort"}, "current_profile": {"source": "packaged"}},
                    ]
                },
            },
            "capacity": {
                "status": "OK",
                "data": {
                    "observations": [
                        {"peer_id": "cc", "pool_ref": "quota:cc:5h", "state": "MEASURED", "measurements": {"remaining_fraction": 0.82}, "age_seconds": 720.0},
                        {"peer_id": "cx", "pool_ref": "quota:cx:1d", "state": "MEASURED", "measurements": {"remaining_fraction": 0.65}, "age_seconds": 240.0},
                    ]
                },
            },
            "context": {
                "status": "OK",
                "data": {
                    "sessions": [
                        {"peer_id": "cx", "context": {"percent": 22.0, "state": "MEASURED", "occupancy_tokens": 45000}},
                    ]
                },
            },
        },
    }


def test_1_catalog_lists_peers_with_description_and_default_yes(tmp_path, monkeypatch, capsys):
    """1. Catalog lists 'peers' with description + default yes (groups text + JSON shape unchanged except new id)."""
    from peerhub.cli.app import main
    from peerhub.cli.monitor_preferences import DEFAULT_GROUPS, GROUP_IDS, GROUPS

    # Catalog tuple and default groups must include 'peers'
    assert "peers" in GROUP_IDS
    assert "peers" in DEFAULT_GROUPS

    peers_spec = next((item for item in GROUPS if item["id"] == "peers"), None)
    assert peers_spec is not None
    assert peers_spec.get("description")
    assert peers_spec.get("collection_effect") == "read_only"

    # JSON shape unchanged except new id
    monkeypatch.chdir(tmp_path)
    assert main(["monitor", "groups", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "2.0"
    assert payload["selection_scope"] == "grouped_views"
    catalog_ids = [group["id"] for group in payload["groups"]]
    assert "peers" in catalog_ids

    entry = next(g for g in payload["groups"] if g["id"] == "peers")
    assert entry["default_selected"] is True
    assert entry["description"] == peers_spec["description"]

    # Human output lists 'peers', default 'yes', and description
    assert main(["monitor", "groups"]) == 0
    text_out = capsys.readouterr().out
    assert re.search(r"^peers\s+yes\s+.+", text_out, re.MULTILINE)


def test_2_renderer_prints_one_block_per_peer_with_4_links(peers_frame):
    """2. Renderer prints one block per peer with the 4 links:
    quota -> profile/model [config layer] -> latest session [fresh|reused, gen] -> context [measured%|unknown]."""
    from peerhub.cli.monitor_view import render_grouped_frame

    rendered = render_grouped_frame(peers_frame, view="plain", width=80)
    assert "PEERS [OK]" in rendered

    # cc block: quota -> profile/model [ws] -> latest session -> context
    assert "cc" in rendered
    assert re.search(r"cc\s+quota:\s*5h:\s*82%.*->\s*cc\.effort/claude-sonnet-5-5\s*\[workspace\]", rendered)
    assert re.search(r"->\s*latest session:\s*reused\s+gen\s+1\s*->\s*context:\s*unknown", rendered)

    # cx block: quota -> profile/model [glob] -> latest session -> context
    assert "cx" in rendered
    assert re.search(r"cx\s+quota:\s*1d:\s*65%.*->\s*cx\.effort/gpt-6.1-sol\s*\[global\]", rendered)
    assert re.search(r"->\s*latest session:\s*fresh\s+gen\s+0\s*->\s*context:\s*45k\s*\(22%\)", rendered)

    # ag block: quota unknown (doc does not establish unlim) -> profile/model [pkg] -> latest session none -> context unknown
    assert "ag" in rendered
    assert re.search(r"ag\s+quota:\s*unknown.*->\s*ag\.effort/gemini-3\.8-flash-med\s*\[packaged\]", rendered)
    assert re.search(r"->\s*latest session:\s*none\s*->\s*context:\s*unknown", rendered)

    # Only peers present in snapshot are rendered: when filtering to only cx
    cx_only_frame = dict(peers_frame)
    cx_only_frame["groups"] = dict(peers_frame["groups"])
    cx_only_frame["groups"]["peers"] = {
        "status": "OK",
        "data": {"peers": [p for p in peers_frame["groups"]["peers"]["data"]["peers"] if p["peer_id"] == "cx"]},
    }
    rendered_cx = render_grouped_frame(cx_only_frame, view="plain", width=80)
    assert "cx" in rendered_cx
    assert "cc  quota:" not in rendered_cx
    assert "ag  quota:" not in rendered_cx


def test_3_missing_facts_render_as_unknown_never_blank_or_zero(peers_frame):
    """3. Missing facts => 'unknown' (or 'none' for absent session), never blank/zero,
    and 'resumable' is never used as proof of session reuse."""
    from peerhub.cli.monitor_view import render_grouped_frame

    # Absent facts: no quota, no profile, session with resumable=True but no recorded mode, no context
    peers_frame["groups"]["peers"]["data"]["peers"] = [
        {
            "peer_id": "cx",
            "quota": None,
            "profile": None,
            "session": {"resumable": True, "execution_mode": None, "generation": None},
            "context": None,
        }
    ]
    rendered = render_grouped_frame(peers_frame, view="plain", width=80)

    # Missing facts render as unknown
    assert "quota: unknown" in rendered
    assert re.search(r"->\s*unknown\s*\[unknown\]", rendered) or "profile unknown" in rendered
    assert "latest session: unknown" in rendered
    assert "context: unknown" in rendered

    # Never false zero or blank
    assert "0%" not in rendered
    assert "resumable" not in rendered
    assert "reused" not in rendered
    assert "fresh" not in rendered


def test_4_stale_or_absent_quota_shown_as_stale_or_unknown_not_current(peers_frame):
    """4. Stale/absent quota shown as STALE/unknown, not as current."""
    from peerhub.cli.monitor_view import render_grouped_frame

    peers_frame["groups"]["peers"]["data"]["peers"] = [
        {
            "peer_id": "cc",
            "quota": {"pool_ref": "5h", "percent": 82, "state": "STALE", "age_seconds": 3600.0},
            "profile": {"profile": "cc.effort", "model": "claude-sonnet-5-5", "source": "workspace"},
            "session": {"generation": 1, "execution_mode": "reuse"},
            "context": {"state": "UNKNOWN", "percent": None},
        },
        {
            "peer_id": "cx",
            "quota": {"pool_ref": "1d", "percent": None, "state": "ABSENT"},
            "profile": {"profile": "cx.effort", "model": "gpt-6.1-sol", "source": "global"},
            "session": {"generation": 0, "execution_mode": "fresh"},
            "context": {"state": "UNKNOWN", "percent": None},
        },
    ]
    rendered = render_grouped_frame(peers_frame, view="plain", width=80)

    # cc has stale quota: must show STALE, never bare 'quota: 5h: 82%' as current
    assert "STALE" in rendered
    assert "quota: 5h: 82% (12m ago)" not in rendered

    # cx has absent quota: must show unknown/ABSENT, never 0% or active pool
    assert "quota: unknown" in rendered or "quota: ABSENT" in rendered


def test_5_never_claims_model_specific_quota_pool_membership(peers_frame):
    """5. Quota pools remain peer/account-level; never claims model-specific quota pool membership."""
    from peerhub.cli.monitor_view import render_grouped_frame

    rendered = render_grouped_frame(peers_frame, view="plain", width=80)

    # Every peer block must strictly decouple quota from profile/model via arrow '->'
    lines = [line.strip() for line in rendered.splitlines() if "quota:" in line and "->" in line]
    assert len(lines) >= 3

    for line in lines:
        quota_part, model_part = line.split("->", 1)
        # Quota part must only mention peer pool (5h, 1d, unlim), never model names
        assert "claude" not in quota_part.lower()
        assert "sonnet" not in quota_part.lower()
        assert "gpt" not in quota_part.lower()
        assert "gemini" not in quota_part.lower()
        # Model part has the profile/model
        assert any(m in model_part.lower() for m in ("claude", "gpt", "gemini"))


def test_6_narrow_width_degrades_to_one_line_per_peer_and_stays_within_cols_minus_1(peers_frame):
    """6. Narrow width (e.g. 40) degrades to one line per peer via ordinary clipping and stays within columns-1."""
    from peerhub.cli.monitor_view import _width, render_grouped_frame

    rendered = render_grouped_frame(peers_frame, view="plain", width=40)
    lines = rendered.splitlines()

    # Stays within columns - 1 (<= 39)
    assert all(_width(line) <= 39 for line in lines)

    # Degrades to one line per peer (e.g. 'cc: quota 82% | cc.effort... | reused gen 1 | ctx: unk')
    peer_lines = [
        line for line in lines
        if any(line.lstrip().startswith(f"{peer}:") or line.lstrip().startswith(f"{peer} ") for peer in ("cc", "cx", "ag"))
    ]
    assert len(peer_lines) == 3
    for line in peer_lines:
        assert "|" in line
        assert _width(line) <= 39


def test_7_height_limited_tty_keeps_severity_based_omission(peers_frame):
    """7. Height-limited TTY keeps severity-based omission for 'peers' group."""
    from peerhub.cli.monitor_view import render_grouped_frame

    # When peers group is ERROR, it must be prioritized over OK groups in tight viewport
    peers_frame["selected_groups"] = ["overview", "peers", "execution"]
    peers_frame["groups"]["overview"]["status"] = "OK"
    peers_frame["groups"]["execution"]["status"] = "OK"
    peers_frame["groups"]["peers"]["status"] = "ERROR"

    rendered_err = render_grouped_frame(peers_frame, view="plain", width=80, height=6)
    assert "PEERS [ERROR]" in rendered_err
    assert "[unrecognized group]" not in rendered_err
    assert "2 groups omitted" in rendered_err.lower() or "omitted" in rendered_err.lower()

    # When peers group is OK and another group is ERROR, the ERROR group is prioritized and peers omitted
    peers_frame["groups"]["peers"]["status"] = "OK"
    peers_frame["groups"]["overview"]["status"] = "ERROR"
    rendered_ok = render_grouped_frame(peers_frame, view="plain", width=80, height=5)
    assert "OVERVIEW [ERROR]" in rendered_ok
    assert "PEERS [" not in rendered_ok
    assert "[unrecognized group]" not in rendered_ok


def test_8_group_peers_selects_only_it_and_redirected_snapshot_prints_all_peers(peers_frame):
    """8. --group peers selects only it; redirected snapshot prints all peers."""
    from peerhub.cli.monitor_preferences import GROUP_IDS
    from peerhub.cli.monitor_view import render_grouped_frame

    # --group peers is recognized as a valid CLI choice
    assert "peers" in GROUP_IDS

    # When only 'peers' is selected, no other group headers appear
    peers_frame["selected_groups"] = ["peers"]
    rendered = render_grouped_frame(peers_frame, view="plain", width=80, height=None)

    assert "PEERS [OK]" in rendered
    assert "OVERVIEW [" not in rendered
    assert "EXECUTION [" not in rendered
    assert "SESSIONS [" not in rendered

    # Redirected snapshot (height=None, non-TTY / pipe) prints all present peers without omission
    assert "cc" in rendered
    assert "cx" in rendered
    assert "ag" in rendered
    assert "omitted" not in rendered.lower()


def test_rework_1_read_model_peers_resolves_profile_provenance_or_unknown_never_hardcodes_workspace(tmp_path):
    """1. Read model resolves effective profile provenance only when recorded model matches current effective profile; otherwise 'unknown'."""
    from tests.communication.bridge.test_retained_sessions import _ProviderTelemetryRuntime
    from tests.communication.bridge_helpers import bseed
    from tests.communication.harness.bridge import BridgeHarness
    from peerhub.extensions.monitor_read_model import render_monitor_frame

    class UnequalModelRuntime(_ProviderTelemetryRuntime):
        def session_binding(self):
            # cc.standard is packaged with claude-haiku-5-5, so claude-sonnet-4-6 is unequal
            return {"profile": "cc.standard", "model": "claude-sonnet-4-6", "effort": "medium", "writable": False}

    harness = BridgeHarness(tmp_path / "workspace")
    bseed(harness, peers=("a", "cc"), stream="s", n=2)
    harness.delivery_cycle("cc", "s", UnequalModelRuntime())

    frame = render_monitor_frame(harness.db_path, ["peers"], workspace=harness.workspace)
    assert frame["status"] in ("OK", "PARTIAL")
    peer_items = frame["groups"]["peers"]["data"]["peers"]
    cc_entry = next((p for p in peer_items if p["peer_id"] == "cc"), None)
    assert cc_entry is not None
    assert cc_entry["profile"] is not None
    # Model differs from current effective profile's model => must be 'unknown' (no misattribution)
    assert cc_entry["profile"]["source"] == "unknown"

    # Case for equal model: recorded model matches effective profile model => layer shown
    class EqualModelRuntime(_ProviderTelemetryRuntime):
        def session_binding(self):
            # cc.standard effective model is claude-haiku-5-5
            return {"profile": "cc.standard", "model": "claude-haiku-5-5", "effort": "medium", "writable": False}

    harness_eq = BridgeHarness(tmp_path / "workspace_eq")
    bseed(harness_eq, peers=("a", "cc"), stream="s")
    harness_eq.delivery_cycle("cc", "s", EqualModelRuntime())
    frame_eq = render_monitor_frame(harness_eq.db_path, ["peers"], workspace=harness_eq.workspace)
    cc_entry_eq = next((p for p in frame_eq["groups"]["peers"]["data"]["peers"] if p["peer_id"] == "cc"), None)
    assert cc_entry_eq is not None
    assert cc_entry_eq["profile"] is not None
    assert cc_entry_eq["profile"]["source"] == "packaged"


def test_rework_2_read_model_peers_chooses_latest_session_by_recorded_activity_not_rowid(tmp_path):
    """2. Read model picks latest session by greatest recorded activity (last_seen), not first insertion rowid."""
    import sqlite3
    from tests.communication.bridge_helpers import bseed
    from tests.communication.harness.bridge import BridgeHarness
    from peerhub.extensions.monitor_read_model import render_monitor_frame

    harness = BridgeHarness(tmp_path / "workspace")
    bseed(harness, peers=("a", "cx"), stream="s")

    # Insert two sessions for 'cx': sess-1 (older rowid, reused later) and sess-2 (newer rowid)
    with sqlite3.connect(harness.db_path) as conn:
        conn.execute(
            "INSERT INTO bridge_session_history("
            "session_handle,stream_id,peer_id,runtime_kind,external_session_id,session_generation,"
            "adapter_fingerprint,binding,resumable,state,last_seen,created_at"
            ") VALUES ('sess-1', 's', 'cx', 'codex', 'ext-1', 1, 'fp', '{\"profile\":\"cx.default\"}', 1, 'ACTIVE', 100.0, 100.0)"
        )
        conn.execute(
            "INSERT INTO bridge_session_history("
            "session_handle,stream_id,peer_id,runtime_kind,external_session_id,session_generation,"
            "adapter_fingerprint,binding,resumable,state,last_seen,created_at"
            ") VALUES ('sess-2', 's', 'cx', 'codex', 'ext-2', 2, 'fp', '{\"profile\":\"cx.default\"}', 1, 'ACTIVE', 200.0, 200.0)"
        )
        # Now simulate reusing sess-1 later (updating its last_seen to 300.0)
        conn.execute("UPDATE bridge_session_history SET last_seen=300.0 WHERE session_handle='sess-1'")
        conn.commit()

    frame = render_monitor_frame(harness.db_path, ["peers"], workspace=harness.workspace)
    peer_items = frame["groups"]["peers"]["data"]["peers"]
    cx_entry = next((p for p in peer_items if p["peer_id"] == "cx"), None)
    assert cx_entry is not None
    assert cx_entry["session"] is not None
    # Must pick sess-1 because last_seen=300.0 > 200.0, not sess-2 (higher rowid)
    assert cx_entry["session"]["generation"] == 1


def test_rework_3_stale_or_doc_quota_renders_stale_or_unknown_never_unlim_doc(peers_frame):
    """3. Any non-MEASURED quota renders as STALE/unknown before source handling; no 'unlim (doc)'."""
    from peerhub.cli.monitor_view import render_grouped_frame

    # Case A: STALE with source='doc' or pool='unlim' must render STALE, not unlim
    peers_frame["groups"]["peers"]["data"]["peers"] = [
        {
            "peer_id": "ag",
            "quota": {"pool_ref": "unlim", "source": "doc", "state": "STALE", "age_seconds": 3600.0},
            "profile": {"name": "ag.effort/gemini-3.8-flash-med", "source": "packaged"},
            "session": None,
            "context": None,
        }
    ]
    rendered_stale = render_grouped_frame(peers_frame, view="plain", width=80)
    assert "STALE" in rendered_stale
    assert "unlim" not in rendered_stale

    # Case B: source='doc' does NOT establish unlimited capacity; renders unknown
    peers_frame["groups"]["peers"]["data"]["peers"] = [
        {
            "peer_id": "ag",
            "quota": {"pool_ref": "doc-cap", "source": "doc", "state": "MEASURED", "percent": None},
            "profile": {"name": "ag.effort/gemini-3.8-flash-med", "source": "packaged"},
            "session": None,
            "context": None,
        }
    ]
    rendered_doc = render_grouped_frame(peers_frame, view="plain", width=80)
    assert "quota: unknown" in rendered_doc
    assert "unlim" not in rendered_doc


def test_rework_4_peers_discloses_truncation_when_sessions_capped(tmp_path):
    """4. Real capped fixture (>4 peers, >100 sessions): read model emits truncated, renderer shows 'sample truncated'."""
    import sqlite3
    from tests.communication.bridge_helpers import bseed
    from tests.communication.harness.bridge import BridgeHarness
    from peerhub.extensions.monitor_read_model import render_monitor_frame
    from peerhub.cli.monitor_view import render_grouped_frame

    harness = BridgeHarness(tmp_path / "workspace")
    # Seed 6 peers: 'a', 'p1', 'p2', 'p3', 'p4', 'p5' (>4 peers)
    bseed(harness, peers=("a", "p1", "p2", "p3", "p4", "p5"), stream="s")

    # Insert 105 session rows for peer 'a' (>100 session rows), leaving p1 and p2 with no session in sample
    with sqlite3.connect(harness.db_path) as conn:
        history = [
            (
                f"sess-{i:03d}", "s", "a", "codex", f"ext-{i}", i + 1,
                "fp", '{"profile":"a.default"}', 1, "ACTIVE", 100.0 + i, 100.0 + i,
            )
            for i in range(105)
        ]
        conn.executemany(
            "INSERT INTO bridge_session_history("
            "session_handle,stream_id,peer_id,runtime_kind,external_session_id,session_generation,"
            "adapter_fingerprint,binding,resumable,state,last_seen,created_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            history,
        )
        conn.commit()

    frame = render_monitor_frame(harness.db_path, ["peers"], workspace=harness.workspace)
    assert frame["status"] in ("OK", "PARTIAL")
    data = frame["groups"]["peers"]["data"]

    # Assert read model emits truncated for both peer sample and session sample
    assert data["peer_sample_truncated"] is True
    assert data["session_sample_truncated"] is True
    assert data["truncated"] is True

    # Assert renderer shows 'sample truncated' instead of 'none' for peers with missing sessions
    rendered = render_grouped_frame(frame, view="plain", width=80)
    assert "latest session: sample truncated" in rendered
    assert "latest session: none" not in rendered


def test_rework_5_narrow_view_uses_available_width_without_fixed_37_cap(peers_frame):
    """5. Drop fixed 37-column cap; use ordinary clipping for the available width."""
    from peerhub.cli.monitor_view import _width, render_grouped_frame

    # At width 46, content width is 43 (columns-1 = 45). Lines should use columns beyond 37.
    rendered = render_grouped_frame(peers_frame, view="plain", width=46, height=10)
    lines = rendered.splitlines()
    peer_lines = [line for line in lines if any(line.lstrip().startswith(f"{p}:") for p in ("cc", "cx", "ag"))]
    assert len(peer_lines) == 3
    # With ordinary clipping on width 46, fitted lines should exceed the old 37 cap (indented lines > 39 chars)
    assert any(_width(line) > 39 for line in peer_lines)
    # And must stay within columns - 1 (<= 45)
    assert all(_width(line) <= 45 for line in peer_lines)

