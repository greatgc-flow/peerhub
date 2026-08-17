"""Unit tests for PeerHub Telemetry Presenter."""

import pytest
from pathlib import Path
from peerhub.telemetry.presenter import TelemetryPresenter, _dw, _pad


def test_dw_and_pad_formatting():
    assert _dw("hello") == 5
    assert _dw("한글") == 4
    padded = _pad("test", 10, align="left")
    assert len(padded) == 10
    assert padded == "test      "


def test_presenter_rendering_clean():
    presenter = TelemetryPresenter(use_color=False)
    snapshot = {
        "room": {"room": "room-test", "leader": "ag", "coordinator": "absent"},
        "alerts": [
            {"level": "WARN", "message": "ag: QUOTA_WARN quota 83% used"},
        ],
        "failover_target": "ag.deepthink",
        "failover_headroom": "17%",
        "peers": {
            "ag": {
                "state": "OPEN",
                "context_str": "200k/1M 20%",
                "cost_str": "absent",
                "src": "STAT",
                "pools": [
                    {
                        "name": "G-pool",
                        "status_icon": "🟢",
                        "exh_str": "1.00x",
                        "five_h": "10% Pace 1.00x",
                        "seven_d": "80% Pace 1.00x",
                        "reset_in": "resets in 4h",
                        "is_crit": False,
                    }
                ]
            }
        },
        "routing_rows": [
            {"profile": "ag.deepthink", "state": "eligible", "headroom": "17%", "quota": "17%", "ctx": "80%", "effort": "high", "source": "c:STAT q:STAT"}
        ]
    }
    rendered = presenter.render(snapshot)
    assert "PeerHub Multi-Peer Diagnostics" in rendered
    assert "ROOM room=room-test" in rendered
    assert "ag: QUOTA_WARN" in rendered
    assert "NEXT FAILOVER TARGET: ag.deepthink" in rendered
    assert "AG" in rendered
    assert "G-pool" in rendered


def test_presenter_collect_live_snapshot(tmp_path: Path):
    sys_dir = tmp_path / "_sys"
    sys_dir.mkdir(parents=True)
    presenter = TelemetryPresenter(use_color=False, workspace_root=tmp_path)
    snapshot = presenter.collect_live_snapshot()
    assert "peers" in snapshot
    assert "ag" in snapshot["peers"]
    assert "cc" in snapshot["peers"]
    assert "cx" in snapshot["peers"]
