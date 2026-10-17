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
                    ],
                    "defaults_revision": "2026-10-16.1",
                    "observed_at": "2026-10-16",
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
    assert re.search(r"cc\s+quota:\s*82%\s*\(5h\).*->\s*cc\.effort/claude-sonnet-5-5\s*\[workspace\]", rendered)
    assert re.search(r"->\s*latest session:\s*reused\s+gen\s+1\s*->\s*context:\s*unknown", rendered)

    # cx block: quota -> profile/model [glob] -> latest session -> context
    assert "cx" in rendered
    assert re.search(r"cx\s+quota:\s*65%\s*\(1d\).*->\s*cx\.effort/gpt-6.1-sol\s*\[global\]", rendered)
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


def test_pr2_peers_footer_shows_model_facts_with_revision_and_sources(peers_frame):
    """PR2.1: Peers footer line shows model facts with defaults_revision, observed_at, age, and sources."""
    from peerhub.cli.monitor_view import render_grouped_frame

    # Fixed clock: 2026-10-16 12:00:00 UTC (same day as observed_at 2026-10-16 => 0d ago)
    peers_frame["read_at"] = 1792152000.0
    rendered = render_grouped_frame(peers_frame, view="plain", width=140)

    expected = (
        "Model facts: rev 2026-10-16.1 · checked 2026-10-16 (0d ago) · "
        "sources: codex debug models | agy models | claude: known ids"
    )
    assert expected in rendered


def test_pr2_peers_footer_computes_age_with_fixed_clock(peers_frame):
    """PR2.2: Age in days is computed from snapshot read time (N days ago)."""
    from peerhub.cli.monitor_view import render_grouped_frame

    # Fixed clock: 2026-10-18 12:00:00 UTC (2 days after 2026-10-16 => 2d ago)
    peers_frame["read_at"] = 1792152000.0 + 86400.0 * 2
    rendered = render_grouped_frame(peers_frame, view="plain", width=140)

    assert "checked 2026-10-16 (2d ago)" in rendered


def test_pr2_peers_footer_clips_within_columns_minus_1(peers_frame):
    """PR2.3: Footer line is fitted to width by ordinary clipping and stays within columns-1."""
    from peerhub.cli.monitor_view import _width, render_grouped_frame

    # At width 40, entire line must stay within columns - 1 (<= 39)
    rendered = render_grouped_frame(peers_frame, view="plain", width=40)
    lines = rendered.splitlines()
    footer_lines = [line for line in lines if "Model facts:" in line]
    assert len(footer_lines) == 1
    assert _width(footer_lines[0]) <= 39
    assert footer_lines[0].endswith("…")

    # At standard width 80, entire line must stay within columns - 1 (<= 79)
    rendered_80 = render_grouped_frame(peers_frame, view="plain", width=80)
    footer_80 = [line for line in rendered_80.splitlines() if "Model facts:" in line]
    assert len(footer_80) == 1
    assert _width(footer_80[0]) <= 79

    # At standard width 120, entire line must stay within columns - 1 (<= 119)
    rendered_120 = render_grouped_frame(peers_frame, view="plain", width=120)
    footer_120 = [line for line in rendered_120.splitlines() if "Model facts:" in line]
    assert len(footer_120) == 1
    assert _width(footer_120[0]) <= 119


def test_pr2_peers_footer_future_observed_at_renders_future(peers_frame):
    """PR2.5: observed_at after read_at renders '(future)' not '0d ago'."""
    from peerhub.cli.monitor_view import render_grouped_frame

    # Fixed clock: 2026-10-10 UTC (observed_at 2026-10-16 is in the future)
    peers_frame["read_at"] = 1791633600.0  # 2026-10-10 12:00:00 UTC
    peers_frame["groups"]["peers"]["data"]["observed_at"] = "2026-10-16"
    rendered = render_grouped_frame(peers_frame, view="plain", width=140)

    assert "checked 2026-10-16 (future)" in rendered


def test_pr2_peers_footer_handles_missing_facts_as_unknown(peers_frame):
    """PR2.4: Missing facts render as 'unknown', never invent or raise."""
    from peerhub.cli.monitor_view import render_grouped_frame

    # Snapshot without read_at and with explicit None metadata
    peers_frame["read_at"] = None
    peers_frame["groups"]["peers"]["data"]["defaults_revision"] = None
    peers_frame["groups"]["peers"]["data"]["observed_at"] = None
    rendered = render_grouped_frame(peers_frame, view="plain", width=140)

    assert (
        "Model facts: rev unknown · checked unknown · "
        "sources: codex debug models | agy models | claude: known ids"
    ) in rendered


def test_read_model_peers_chooses_lowest_remaining_measured_quota_skipping_error(tmp_path):
    """DB-fixture test: old ERROR row sorted first + fresh MEASURED rows => expects lowest measured (17% (3P-5H)); stale-only => STALE."""
    from peerhub.extensions.monitor_read_model import render_monitor_frame
    from peerhub.cli.monitor_view import render_grouped_frame
    from peerhub.extensions.observation_model import epoch_to_iso
    from tests.communication.fakes.observation import FakeObservationSource, measured, unknown
    from tests.communication.harness.observation import T0, ObservationHarness

    harness = ObservationHarness(tmp_path / "workspace")
    harness.create_peer({"peer_id": "ag", "display_name": "Antigravity"})
    harness.create_peer({"peer_id": "cc", "display_name": "Claude"})

    def _pool(pool_id: str, provider: str) -> dict:
        return {"schema_version": "1.0", "resource_pool_id": pool_id,
                "provider": provider, "kind": "QUOTA", "metadata": {}}

    # Peer cc:
    # Stale-only observation (captured at T0, will be 5 days old when read)
    harness.register_pool(_pool("quota:cc:5h", "anthropic"))
    harness.capture("cc", "quota", FakeObservationSource(measured({"remaining_fraction": 0.50}, observed_at=epoch_to_iso(T0))), resource_pool_ref="quota:cc:5h")

    # Advance clock by 5 days (beyond quota TTL)
    harness.clock.advance(86400 * 5)
    t_fresh = harness.clock.now()

    # Peer ag:
    # 1. Old ERROR/UNKNOWN row with pool_key that sorts first alphabetically
    harness.register_pool(_pool("quota:ag:00-error", "google"))
    harness.capture("ag", "quota", FakeObservationSource(unknown({"error": "stale probe failure"}, observed_at=epoch_to_iso(t_fresh - 300))), resource_pool_ref="quota:ag:00-error")
    # 2. Fresh MEASURED row with 17% remaining
    harness.register_pool(_pool("quota:ag:3P-5H", "google"))
    harness.capture("ag", "quota", FakeObservationSource(measured({"remaining_fraction": 0.17}, observed_at=epoch_to_iso(t_fresh))), resource_pool_ref="quota:ag:3P-5H")
    # 3. Fresh MEASURED row with 85% remaining
    harness.register_pool(_pool("quota:ag:G-5H", "google"))
    harness.capture("ag", "quota", FakeObservationSource(measured({"remaining_fraction": 0.85}, observed_at=epoch_to_iso(t_fresh))), resource_pool_ref="quota:ag:G-5H")

    frame = render_monitor_frame(harness.db_path, ["peers"], read_at=t_fresh + 10, workspace=harness.workspace)
    assert frame["status"] in ("OK", "PARTIAL")
    peer_items = frame["groups"]["peers"]["data"]["peers"]

    ag_entry = next((p for p in peer_items if p["peer_id"] == "ag"), None)
    assert ag_entry is not None
    assert ag_entry["quota"] is not None
    assert ag_entry["quota"]["state"] == "MEASURED"
    assert ag_entry["quota"]["percent"] == 17
    assert ag_entry["quota"]["pool_ref"] == "quota:ag:3P-5H"

    cc_entry = next((p for p in peer_items if p["peer_id"] == "cc"), None)
    assert cc_entry is not None
    assert cc_entry["quota"] is not None
    assert cc_entry["quota"]["state"] == "STALE"

    rendered = render_grouped_frame(frame, view="plain", width=80)
    assert "ag  quota: 17% (3P-5H)" in rendered
    assert "cc  quota: STALE" in rendered


def test_peers_view_mapped_model_selects_minimum_remaining_and_labels_pool(tmp_path):
    """S2: Mapped model selects the minimum remaining across its fresh measured pools and labels it."""
    import sqlite3
    from tests.communication.bridge_helpers import bseed
    from tests.communication.harness.bridge import BridgeHarness
    from peerhub.extensions.observation import ObservationStore
    from peerhub.extensions.observation_model import ResourcePool, epoch_to_iso
    from tests.communication.fakes.observation import FakeObservationSource, measured
    from peerhub.extensions.monitor_read_model import render_monitor_frame
    from peerhub.cli.monitor_view import render_grouped_frame

    harness = BridgeHarness(tmp_path / "workspace")
    bseed(harness, peers=("a", "ag"), stream="s")
    obs = ObservationStore(harness.db_path, clock=harness.clock.now)

    with sqlite3.connect(harness.db_path) as conn:
        conn.execute(
            "INSERT INTO bridge_session_history("
            "session_handle,stream_id,peer_id,runtime_kind,external_session_id,session_generation,"
            "adapter_fingerprint,binding,resumable,state,last_seen,created_at"
            ") VALUES ('sess-ag', 's', 'ag', 'antigravity', 'ext-ag', 1, 'fp', "
            "'{\"profile\":\"ag.standard\",\"model\":\"gemini-3.8-flash-low\"}', 1, 'ACTIVE', 100.0, 100.0)"
        )
        conn.commit()

    def _pool(pool_id: str, provider: str) -> dict:
        return {"schema_version": "1.0", "resource_pool_id": pool_id,
                "provider": provider, "kind": "QUOTA", "metadata": {}}

    obs.register_resource_pool(ResourcePool.model_validate(_pool("quota:ag:G-5H", "google")))
    obs.register_resource_pool(ResourcePool.model_validate(_pool("quota:ag:G-7D", "google")))
    obs.register_resource_pool(ResourcePool.model_validate(_pool("quota:ag:3P-5H", "google")))

    now_iso = epoch_to_iso(harness.clock.now())
    obs.capture("ag", "quota", FakeObservationSource(measured({"remaining_fraction": 0.80}, observed_at=now_iso)), resource_pool_ref="quota:ag:G-5H")
    obs.capture("ag", "quota", FakeObservationSource(measured({"remaining_fraction": 0.45}, observed_at=now_iso)), resource_pool_ref="quota:ag:G-7D")
    # Even though 3P-5H is 10%, it is NOT a pool for ag.standard, so it must not be picked
    obs.capture("ag", "quota", FakeObservationSource(measured({"remaining_fraction": 0.10}, observed_at=now_iso)), resource_pool_ref="quota:ag:3P-5H")

    frame = render_monitor_frame(harness.db_path, ["peers"], read_at=harness.clock.now() + 5, workspace=harness.workspace)
    assert frame["status"] in ("OK", "PARTIAL")
    peer_items = frame["groups"]["peers"]["data"]["peers"]
    ag_entry = next((p for p in peer_items if p["peer_id"] == "ag"), None)
    assert ag_entry is not None
    assert ag_entry["quota"] is not None
    assert ag_entry["quota"]["state"] == "MEASURED"
    assert ag_entry["quota"]["percent"] == 45
    assert ag_entry["quota"]["pool_ref"] in ("quota:ag:G-7D", "G-7D")

    rendered = render_grouped_frame(frame, view="plain", width=80)
    assert "ag  quota: 45% (G-7D)" in rendered


def test_peers_view_mapped_model_missing_or_stale_pool_renders_incomplete(tmp_path):
    """S2: Mapped model renders incomplete if any required pool is missing or stale."""
    import sqlite3
    from tests.communication.bridge_helpers import bseed
    from tests.communication.harness.bridge import BridgeHarness
    from peerhub.extensions.observation import ObservationStore
    from peerhub.extensions.observation_model import ResourcePool, epoch_to_iso
    from tests.communication.fakes.observation import FakeObservationSource, measured
    from peerhub.extensions.monitor_read_model import render_monitor_frame
    from peerhub.cli.monitor_view import render_grouped_frame

    harness = BridgeHarness(tmp_path / "workspace")
    bseed(harness, peers=("a", "ag"), stream="s")
    obs = ObservationStore(harness.db_path, clock=harness.clock.now)

    with sqlite3.connect(harness.db_path) as conn:
        conn.execute(
            "INSERT INTO bridge_session_history("
            "session_handle,stream_id,peer_id,runtime_kind,external_session_id,session_generation,"
            "adapter_fingerprint,binding,resumable,state,last_seen,created_at"
            ") VALUES ('sess-ag', 's', 'ag', 'antigravity', 'ext-ag', 1, 'fp', "
            "'{\"profile\":\"ag.standard\",\"model\":\"gemini-3.8-flash-low\"}', 1, 'ACTIVE', 100.0, 100.0)"
        )
        conn.commit()

    def _pool(pool_id: str, provider: str) -> dict:
        return {"schema_version": "1.0", "resource_pool_id": pool_id,
                "provider": provider, "kind": "QUOTA", "metadata": {}}

    obs.register_resource_pool(ResourcePool.model_validate(_pool("quota:ag:G-5H", "google")))
    obs.register_resource_pool(ResourcePool.model_validate(_pool("quota:ag:G-7D", "google")))

    now_iso = epoch_to_iso(harness.clock.now())
    # Case A: G-7D is missing entirely
    obs.capture("ag", "quota", FakeObservationSource(measured({"remaining_fraction": 0.80}, observed_at=now_iso)), resource_pool_ref="quota:ag:G-5H")

    frame = render_monitor_frame(harness.db_path, ["peers"], read_at=harness.clock.now() + 5, workspace=harness.workspace)
    peer_items = frame["groups"]["peers"]["data"]["peers"]
    ag_entry = next((p for p in peer_items if p["peer_id"] == "ag"), None)
    assert ag_entry is not None
    assert ag_entry["quota"] is not None
    assert ag_entry["quota"]["state"] == "INCOMPLETE"
    assert ag_entry["quota"]["percent"] is None

    rendered = render_grouped_frame(frame, view="plain", width=80)
    assert "ag  quota: incomplete" in rendered

    # Case B: G-7D is captured, but becomes STALE after clock advance
    old_time = harness.clock.now()
    obs.capture("ag", "quota", FakeObservationSource(measured({"remaining_fraction": 0.50}, observed_at=epoch_to_iso(old_time))), resource_pool_ref="quota:ag:G-7D")
    harness.clock.advance(86400 * 5)
    t_fresh = harness.clock.now()
    # Refresh G-5H only
    obs.capture("ag", "quota", FakeObservationSource(measured({"remaining_fraction": 0.80}, observed_at=epoch_to_iso(t_fresh))), resource_pool_ref="quota:ag:G-5H")

    frame2 = render_monitor_frame(harness.db_path, ["peers"], read_at=t_fresh + 5, workspace=harness.workspace)
    peer_items2 = frame2["groups"]["peers"]["data"]["peers"]
    ag_entry2 = next((p for p in peer_items2 if p["peer_id"] == "ag"), None)
    assert ag_entry2 is not None
    assert ag_entry2["quota"] is not None
    assert ag_entry2["quota"]["state"] == "INCOMPLETE"
    assert ag_entry2["quota"]["percent"] is None

    rendered2 = render_grouped_frame(frame2, view="plain", width=80)
    assert "ag  quota: incomplete" in rendered2


def test_peers_view_unmapped_model_or_override_uses_lowest_pool_labelled_unmapped(tmp_path):
    """S2: Unknown model or override uses lowest pool across peer, labelled unmapped."""
    import sqlite3
    from tests.communication.bridge_helpers import bseed
    from tests.communication.harness.bridge import BridgeHarness
    from peerhub.extensions.observation import ObservationStore
    from peerhub.extensions.observation_model import ResourcePool, epoch_to_iso
    from tests.communication.fakes.observation import FakeObservationSource, measured
    from peerhub.extensions.monitor_read_model import render_monitor_frame
    from peerhub.cli.monitor_view import render_grouped_frame

    harness = BridgeHarness(tmp_path / "workspace")
    bseed(harness, peers=("a", "ag"), stream="s")
    obs = ObservationStore(harness.db_path, clock=harness.clock.now)

    with sqlite3.connect(harness.db_path) as conn:
        conn.execute(
            "INSERT INTO bridge_session_history("
            "session_handle,stream_id,peer_id,runtime_kind,external_session_id,session_generation,"
            "adapter_fingerprint,binding,resumable,state,last_seen,created_at"
            ") VALUES ('sess-ag', 's', 'ag', 'antigravity', 'ext-ag', 1, 'fp', "
            "'{\"profile\":\"ag.standard\",\"model\":\"gemini-override-custom\"}', 1, 'ACTIVE', 100.0, 100.0)"
        )
        conn.commit()

    def _pool(pool_id: str, provider: str) -> dict:
        return {"schema_version": "1.0", "resource_pool_id": pool_id,
                "provider": provider, "kind": "QUOTA", "metadata": {}}

    obs.register_resource_pool(ResourcePool.model_validate(_pool("quota:ag:3P-5H", "google")))
    obs.register_resource_pool(ResourcePool.model_validate(_pool("quota:ag:G-5H", "google")))

    now_iso = epoch_to_iso(harness.clock.now())
    obs.capture("ag", "quota", FakeObservationSource(measured({"remaining_fraction": 0.17}, observed_at=now_iso)), resource_pool_ref="quota:ag:3P-5H")
    obs.capture("ag", "quota", FakeObservationSource(measured({"remaining_fraction": 0.85}, observed_at=now_iso)), resource_pool_ref="quota:ag:G-5H")

    frame = render_monitor_frame(harness.db_path, ["peers"], read_at=harness.clock.now() + 5, workspace=harness.workspace)
    peer_items = frame["groups"]["peers"]["data"]["peers"]
    ag_entry = next((p for p in peer_items if p["peer_id"] == "ag"), None)
    assert ag_entry is not None
    assert ag_entry["quota"] is not None
    assert ag_entry["quota"]["state"] == "MEASURED"
    assert ag_entry["quota"]["percent"] == 17
    assert ag_entry["quota"].get("scope") in ("unmapped", "account-wide")

    rendered = render_grouped_frame(frame, view="plain", width=80)
    assert "ag  quota: 17% (3P-5H unmapped)" in rendered


def test_peers_view_footer_shows_facts_drift_line_and_json_detail(tmp_path):
    """S4: Monitor peers group consumes facts_report and renders clipped drift line + JSON detail."""
    import sqlite3
    from tests.communication.bridge_helpers import bseed
    from tests.communication.harness.bridge import BridgeHarness
    from peerhub.extensions.observation import ObservationStore
    from peerhub.extensions.observation_model import ResourcePool, epoch_to_iso
    from tests.communication.fakes.observation import FakeObservationSource, measured
    from peerhub.extensions.monitor_read_model import render_monitor_frame
    from peerhub.cli.monitor_view import render_grouped_frame

    harness = BridgeHarness(tmp_path / "workspace")
    bseed(harness, peers=("a", "ag"), stream="s")
    obs = ObservationStore(harness.db_path, clock=harness.clock.now)

    def _pool(pool_id: str, provider: str) -> dict:
        return {"schema_version": "1.0", "resource_pool_id": pool_id,
                "provider": provider, "kind": "QUOTA", "metadata": {}}

    obs.register_resource_pool(ResourcePool.model_validate(_pool("quota:ag:G-5H", "google")))
    now_iso = epoch_to_iso(harness.clock.now())
    facts = {
        "status": "needs_recheck",
        "reasons": ["ag: unknown_pool"],
        "checks": {"ag_pools": "drift", "ag_version": "ok", "cc_version": "ok", "cx_version": "ok"},
        "checked_at": now_iso,
    }
    obs.capture(
        "ag", "quota",
        FakeObservationSource(measured({"remaining_fraction": 0.85, "facts_report": facts}, observed_at=now_iso)),
        resource_pool_ref="quota:ag:G-5H",
    )

    frame = render_monitor_frame(harness.db_path, ["peers"], read_at=harness.clock.now() + 5, workspace=harness.workspace)
    data = frame["groups"]["peers"]["data"]
    assert data["facts_report"] is not None
    assert data["facts_report"]["status"] == "needs_recheck"
    assert data["facts_report"]["reasons"] == ["ag: unknown_pool"]

    rendered = render_grouped_frame(frame, view="plain", width=80)
    assert "Facts: needs_recheck (ag: unknown_pool) - refresh-model-profiles" in rendered


def test_peers_view_footer_shows_facts_ok_when_clean(tmp_path):
    """S4: Facts line displays 'Facts: ok' when facts_report status is ok."""
    import sqlite3
    from tests.communication.bridge_helpers import bseed
    from tests.communication.harness.bridge import BridgeHarness
    from peerhub.extensions.observation import ObservationStore
    from peerhub.extensions.observation_model import ResourcePool, epoch_to_iso
    from tests.communication.fakes.observation import FakeObservationSource, measured
    from peerhub.extensions.monitor_read_model import render_monitor_frame
    from peerhub.cli.monitor_view import render_grouped_frame

    harness = BridgeHarness(tmp_path / "workspace")
    bseed(harness, peers=("a", "ag"), stream="s")
    obs = ObservationStore(harness.db_path, clock=harness.clock.now)

    def _pool(pool_id: str, provider: str) -> dict:
        return {"schema_version": "1.0", "resource_pool_id": pool_id,
                "provider": provider, "kind": "QUOTA", "metadata": {}}

    obs.register_resource_pool(ResourcePool.model_validate(_pool("quota:ag:G-5H", "google")))
    now_iso = epoch_to_iso(harness.clock.now())
    facts = {
        "status": "ok",
        "reasons": [],
        "checks": {"ag_pools": "ok", "ag_version": "ok", "cc_version": "ok", "cx_version": "ok"},
        "checked_at": now_iso,
    }
    obs.capture(
        "ag", "quota",
        FakeObservationSource(measured({"remaining_fraction": 0.85, "facts_report": facts}, observed_at=now_iso)),
        resource_pool_ref="quota:ag:G-5H",
    )

    frame = render_monitor_frame(harness.db_path, ["peers"], read_at=harness.clock.now() + 5, workspace=harness.workspace)
    assert frame["groups"]["peers"]["data"]["facts_report"]["status"] == "ok"

    rendered = render_grouped_frame(frame, view="plain", width=80)
    assert f"Facts: ok (checked {now_iso[:10]})" in rendered


def test_peers_view_footer_shows_facts_unverified_and_unknown_pools(tmp_path):
    """S4/CC review: Monitor prints Facts: unverified when status unverified, and displays unknown pools."""
    from tests.communication.bridge_helpers import bseed
    from tests.communication.harness.bridge import BridgeHarness
    from peerhub.extensions.observation import ObservationStore
    from peerhub.extensions.observation_model import ResourcePool, epoch_to_iso
    from tests.communication.fakes.observation import FakeObservationSource, measured
    from peerhub.extensions.monitor_read_model import render_monitor_frame
    from peerhub.cli.monitor_view import render_grouped_frame

    harness = BridgeHarness(tmp_path / "workspace")
    bseed(harness, peers=("a", "ag"), stream="s")
    obs = ObservationStore(harness.db_path, clock=harness.clock.now)

    def _pool(pool_id: str, provider: str) -> dict:
        return {"schema_version": "1.0", "resource_pool_id": pool_id,
                "provider": provider, "kind": "QUOTA", "metadata": {}}

    obs.register_resource_pool(ResourcePool.model_validate(_pool("quota:ag:G-5H", "google")))
    now_iso = epoch_to_iso(harness.clock.now())
    # 1. Unverified facts
    facts_unverified = {
        "status": "unverified",
        "reasons": [],
        "checks": {"ag_pools": "unknown", "ag_version": "unknown"},
        "checked_at": None,
    }
    obs.capture(
        "ag", "quota",
        FakeObservationSource(measured({"remaining_fraction": 0.85, "facts_report": facts_unverified}, observed_at=now_iso)),
        resource_pool_ref="quota:ag:G-5H",
    )
    frame = render_monitor_frame(harness.db_path, ["peers"], read_at=harness.clock.now() + 5, workspace=harness.workspace)
    rendered = render_grouped_frame(frame, view="plain", width=80)
    assert "Facts: unverified" in rendered

    # 2. Unknown pools displayed in monitor line
    facts_with_pools = {
        "status": "needs_recheck",
        "reasons": ["ag: unknown_pool"],
        "unknown_pools": ["gemini-exp", "gemini-ultra"],
        "checks": {"ag_pools": "drift"},
        "checked_at": now_iso,
    }
    obs.capture(
        "ag", "quota",
        FakeObservationSource(measured({"remaining_fraction": 0.85, "facts_report": facts_with_pools}, observed_at=now_iso)),
        resource_pool_ref="quota:ag:G-5H",
    )
    frame2 = render_monitor_frame(harness.db_path, ["peers"], read_at=harness.clock.now() + 6, workspace=harness.workspace)
    rendered2 = render_grouped_frame(frame2, view="plain", width=120)
    assert "Facts: needs_recheck (ag: unknown_pool: gemini-exp, gemini-ultra) - refresh-model-profiles" in rendered2


def test_peers_view_footer_clips_facts_line_within_narrow_columns(tmp_path):
    """S4: Facts line clips cleanly within columns minus 1 on narrow widths."""
    from tests.communication.bridge_helpers import bseed
    from tests.communication.harness.bridge import BridgeHarness
    from peerhub.extensions.observation import ObservationStore
    from peerhub.extensions.observation_model import ResourcePool, epoch_to_iso
    from tests.communication.fakes.observation import FakeObservationSource, measured
    from peerhub.extensions.monitor_read_model import render_monitor_frame
    from peerhub.cli.monitor_view import render_grouped_frame

    harness = BridgeHarness(tmp_path / "workspace")
    bseed(harness, peers=("a", "ag"), stream="s")
    obs = ObservationStore(harness.db_path, clock=harness.clock.now)

    def _pool(pool_id: str, provider: str) -> dict:
        return {"schema_version": "1.0", "resource_pool_id": pool_id,
                "provider": provider, "kind": "QUOTA", "metadata": {}}

    obs.register_resource_pool(ResourcePool.model_validate(_pool("quota:ag:G-5H", "google")))
    now_iso = epoch_to_iso(harness.clock.now())
    facts = {
        "status": "needs_recheck",
        "reasons": ["ag: unknown_pool", "cx: version_drift"],
        "checks": {"ag_pools": "drift", "cx_version": "drift"},
        "checked_at": now_iso,
    }
    obs.capture(
        "ag", "quota",
        FakeObservationSource(measured({"remaining_fraction": 0.85, "facts_report": facts}, observed_at=now_iso)),
        resource_pool_ref="quota:ag:G-5H",
    )

    frame = render_monitor_frame(harness.db_path, ["peers"], read_at=harness.clock.now() + 5, workspace=harness.workspace)
    rendered = render_grouped_frame(frame, view="plain", width=38)
    facts_lines = [l for l in rendered.splitlines() if "Facts:" in l]
    assert len(facts_lines) == 1
    assert len(facts_lines[0]) <= 37




