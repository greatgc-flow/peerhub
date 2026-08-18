"""Unit tests for PeerHub Telemetry Presenter.

Tests verify:
(a) Real fresh poll data changes CC/CX numbers.
(b) Genuinely stale/absent data renders honestly, not as hardcoded literals.
(c) No hard-coded P:/D:/_sys Engram-specific path literals in package source.
"""

import re
import pytest
from pathlib import Path
from peerhub.telemetry.presenter import (
    TelemetryPresenter,
    _dw,
    _pad,
    _build_pool_pair_from_projections,
    _calculate_pacing,
)
from peerhub.telemetry.contract import UsageProjectionSnapshot
from datetime import datetime, timezone


def test_dw_and_pad_formatting():
    assert _dw("hello") == 5
    assert _dw("한글") == 4
    padded = _pad("test", 10, align="left")
    assert len(padded) == 10
    assert padded == "test      "


def _make_projection(
    instance_id: str,
    scope: str,
    used: float,
    remaining: float,
    window_started_at: int = 1000000,
    resets_at: int = 1100000,
) -> UsageProjectionSnapshot:
    """Helper to create a test UsageProjectionSnapshot."""
    return UsageProjectionSnapshot(
        projection_id=f"proj-{instance_id}-{scope}",
        instance_id=instance_id,
        profile_id=f"{instance_id}.standard",
        quota_pool_scope=scope,
        used_fraction=used,
        remaining_fraction=remaining,
        window_started_at=window_started_at,
        resets_at=resets_at,
        revision=1,
        updated_at=1000050,
    )


class TestCCRealData:
    """Test that CC block uses real polled projection data."""

    def test_cc_pool_uses_real_projections(self, tmp_path: Path):
        """When real CC projections are provided, the CC pool shows real data."""
        projections = [
            _make_projection("cc", "C-5H", used=0.42, remaining=0.58),
            _make_projection("cc", "C-7D", used=0.73, remaining=0.27),
        ]
        presenter = TelemetryPresenter(
            use_color=False,
            workspace_root=tmp_path,
            usage_projections=projections,
        )
        # Create minimal _sys dir so _find_sys_dir works
        (tmp_path / "_sys").mkdir(parents=True, exist_ok=True)

        snapshot = presenter.collect_live_snapshot()
        cc_pools = snapshot["peers"]["cc"]["pools"]
        assert len(cc_pools) == 1
        pool = cc_pools[0]
        assert pool["name"] == "C-pool"
        # Verify real data is used, not hardcoded "1.00x" or "0.00x"
        assert "42%" in pool["five_h"]
        assert "73%" in pool["seven_d"]
        # Pacing should be computed, not hardcoded
        assert pool["exh_str"] not in {"1.00x", "0.00x"}

    def test_cc_pool_data_changes_when_projections_change(self, tmp_path: Path):
        """When CC projection data changes, the rendered output changes."""
        (tmp_path / "_sys").mkdir(parents=True, exist_ok=True)

        proj_low = [
            _make_projection("cc", "C-5H", used=0.10, remaining=0.90),
            _make_projection("cc", "C-7D", used=0.20, remaining=0.80),
        ]
        presenter_low = TelemetryPresenter(
            use_color=False, workspace_root=tmp_path, usage_projections=proj_low,
        )
        snap_low = presenter_low.collect_live_snapshot()

        proj_high = [
            _make_projection("cc", "C-5H", used=0.85, remaining=0.15),
            _make_projection("cc", "C-7D", used=0.95, remaining=0.05),
        ]
        presenter_high = TelemetryPresenter(
            use_color=False, workspace_root=tmp_path, usage_projections=proj_high,
        )
        snap_high = presenter_high.collect_live_snapshot()

        low_pool = snap_low["peers"]["cc"]["pools"][0]
        high_pool = snap_high["peers"]["cc"]["pools"][0]

        # Values must differ when underlying data differs
        assert low_pool["five_h"] != high_pool["five_h"]
        assert low_pool["seven_d"] != high_pool["seven_d"]
        assert low_pool["remaining_fraction"] != high_pool["remaining_fraction"]
        # High usage should be marked critical
        assert high_pool["is_crit"] is True
        assert low_pool["is_crit"] is False


class TestCXRealData:
    """Test that CX block uses real polled projection data."""

    def test_cx_pool_uses_real_projections(self, tmp_path: Path):
        """When real CX projections are provided, the CX pool shows real data."""
        projections = [
            _make_projection("cx", "X-5H", used=0.55, remaining=0.45),
            _make_projection("cx", "X-7D", used=0.60, remaining=0.40),
        ]
        presenter = TelemetryPresenter(
            use_color=False,
            workspace_root=tmp_path,
            usage_projections=projections,
        )
        (tmp_path / "_sys").mkdir(parents=True, exist_ok=True)

        snapshot = presenter.collect_live_snapshot()
        cx_pools = snapshot["peers"]["cx"]["pools"]
        assert len(cx_pools) == 1
        pool = cx_pools[0]
        assert pool["name"] == "X-pool"
        # Must NOT be the old hardcoded "93% (1.29x)" or "1.29x"
        assert pool["exh_str"] != "1.29x"
        assert pool["seven_d"] != "93% (1.29x)"
        assert pool["reset_in"] != "in 1d 22h"
        # Must reflect actual data
        assert "55%" in pool["five_h"]
        assert "60%" in pool["seven_d"]

    def test_cx_pool_not_hardcoded_when_data_changes(self, tmp_path: Path):
        """CX pool values must change when underlying projection data changes."""
        (tmp_path / "_sys").mkdir(parents=True, exist_ok=True)

        proj_a = [_make_projection("cx", "X-7D", used=0.30, remaining=0.70)]
        proj_b = [_make_projection("cx", "X-7D", used=0.91, remaining=0.09)]

        snap_a = TelemetryPresenter(
            use_color=False, workspace_root=tmp_path, usage_projections=proj_a,
        ).collect_live_snapshot()
        snap_b = TelemetryPresenter(
            use_color=False, workspace_root=tmp_path, usage_projections=proj_b,
        ).collect_live_snapshot()

        pool_a = snap_a["peers"]["cx"]["pools"][0]
        pool_b = snap_b["peers"]["cx"]["pools"][0]
        assert pool_a["seven_d"] != pool_b["seven_d"]
        assert pool_a["remaining_fraction"] != pool_b["remaining_fraction"]
        assert pool_b["is_crit"] is True


class TestAbsentStaleDataRendering:
    """Test that absent/stale data renders honestly, not as hardcoded defaults."""

    def test_absent_cc_quota_renders_no_pool(self, tmp_path: Path):
        """When no CC projections exist, CC should have empty pools (no fabricated data)."""
        (tmp_path / "_sys").mkdir(parents=True, exist_ok=True)
        presenter = TelemetryPresenter(
            use_color=False,
            workspace_root=tmp_path,
            usage_projections=[],  # No projections at all
        )
        snapshot = presenter.collect_live_snapshot()
        cc_pools = snapshot["peers"]["cc"]["pools"]
        # No fake pool data should be rendered
        assert len(cc_pools) == 0

    def test_absent_cx_quota_renders_no_pool(self, tmp_path: Path):
        """When no CX projections exist, CX should have empty pools (no fabricated data)."""
        (tmp_path / "_sys").mkdir(parents=True, exist_ok=True)
        presenter = TelemetryPresenter(
            use_color=False,
            workspace_root=tmp_path,
            usage_projections=[],
        )
        snapshot = presenter.collect_live_snapshot()
        cx_pools = snapshot["peers"]["cx"]["pools"]
        assert len(cx_pools) == 0

    def test_absent_data_renders_dashes_not_100pct(self, tmp_path: Path):
        """When no data exists, the render output should show '--' not '100%'."""
        (tmp_path / "_sys").mkdir(parents=True, exist_ok=True)
        presenter = TelemetryPresenter(
            use_color=False,
            workspace_root=tmp_path,
            usage_projections=[],
        )
        snapshot = presenter.collect_live_snapshot()
        rendered = presenter.render(snapshot)
        # CC line should not show "100%" anywhere for quota
        # It should show "--" for absent pool data
        lines = rendered.split("\n")
        cc_line = [l for l in lines if l.startswith("CC")]
        assert len(cc_line) == 1
        # In the pool column, it should show "--" not a hardcoded percentage
        assert "100%" not in cc_line[0]


class TestNoHardcodedPaths:
    """Test that the peerhub package contains no hard-coded Engram-specific path literals."""

    def test_no_hardcoded_drive_letters_in_package_source(self):
        """Grep-based test: no P:, D:\\, or _sys Engram-specific paths in package code."""
        package_root = Path(__file__).resolve().parent.parent.parent.parent / "peerhub"
        assert package_root.is_dir(), f"Package root not found: {package_root}"

        # Patterns that indicate hardcoded Engram paths
        forbidden_patterns = [
            re.compile(r'Path\(\s*["\']P:'),         # Path("P:...
            re.compile(r'Path\(\s*["\']D:/'),         # Path("D:/...
            re.compile(r'["\']P:/_sys'),              # "P:/_sys" or 'P:/_sys'
            re.compile(r'["\']D:\\\\'),               # "D:\\"
            re.compile(r'["\']D:/Engram'),            # "D:/Engram..."
            re.compile(r'PORTABLE_ROOT\s*=\s*Path'),  # Module-level PORTABLE_ROOT
        ]

        violations = []
        for py_file in package_root.rglob("*.py"):
            # Skip __pycache__
            if "__pycache__" in str(py_file):
                continue
            content = py_file.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(content.splitlines(), 1):
                # Skip comments
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                for pattern in forbidden_patterns:
                    if pattern.search(line):
                        rel = py_file.relative_to(package_root)
                        violations.append(f"  {rel}:{i}: {stripped}")

        assert violations == [], (
            "Package source contains hardcoded Engram-specific paths:\n"
            + "\n".join(violations)
        )

    def test_no_hardcoded_room_id_in_package_source(self):
        """Grep-based test: no fabricated 'room-efde' room-ID literal anywhere in package code.

        peerhub has no leader-election/room concept of its own (that was an
        Engram-specific governance layer, intentionally not ported -- see
        engram_peerhub_separation_proposal.md row 6.5). A hardcoded room ID
        would be a fabricated value, not measured state.
        """
        package_root = Path(__file__).resolve().parent.parent.parent.parent / "peerhub"
        assert package_root.is_dir(), f"Package root not found: {package_root}"

        violations = []
        for py_file in package_root.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue
            content = py_file.read_text(encoding="utf-8", errors="replace")
            if "room-efde" in content:
                for i, line in enumerate(content.splitlines(), 1):
                    if "room-efde" in line:
                        rel = py_file.relative_to(package_root)
                        violations.append(f"  {rel}:{i}: {line.strip()}")

        assert violations == [], (
            "Package source contains a hardcoded room-ID literal:\n"
            + "\n".join(violations)
        )


class TestFindSysDir:
    """Test that _find_sys_dir uses no hardcoded paths."""

    def test_find_sys_dir_uses_workspace_root(self, tmp_path: Path):
        """_find_sys_dir should resolve to workspace_root/_sys."""
        sys_dir = tmp_path / "_sys"
        sys_dir.mkdir()
        presenter = TelemetryPresenter(use_color=False, workspace_root=tmp_path)
        assert presenter._find_sys_dir() == sys_dir

    def test_find_sys_dir_env_fallback(self, tmp_path: Path, monkeypatch):
        """_find_sys_dir should use PEERHUB_SYS_DIR env var when workspace _sys doesn't exist."""
        env_sys = tmp_path / "custom_sys"
        env_sys.mkdir()
        monkeypatch.setenv("PEERHUB_SYS_DIR", str(env_sys))
        # workspace without _sys
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        presenter = TelemetryPresenter(use_color=False, workspace_root=workspace)
        assert presenter._find_sys_dir() == env_sys

    def test_find_sys_dir_returns_workspace_default_when_nothing_exists(self, tmp_path: Path, monkeypatch):
        """_find_sys_dir should return workspace_root/_sys even if it doesn't exist."""
        monkeypatch.delenv("PEERHUB_SYS_DIR", raising=False)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        presenter = TelemetryPresenter(use_color=False, workspace_root=workspace)
        result = presenter._find_sys_dir()
        assert result == workspace / "_sys"


class TestBuildPoolPairFromProjections:
    """Test the projection-to-pool-data conversion."""

    def test_no_projections_returns_none(self):
        """Empty projections should return None (honestly absent)."""
        result = _build_pool_pair_from_projections(
            [], "C", "C-5H", "C-7D", "C-pool",
            datetime.now(timezone.utc),
        )
        assert result is None

    def test_only_5h_projection(self):
        """Single 5H projection should still produce pool data."""
        proj = _make_projection("cc", "C-5H", used=0.50, remaining=0.50)
        result = _build_pool_pair_from_projections(
            [proj], "C", "C-5H", "C-7D", "C-pool",
            datetime.now(timezone.utc),
        )
        assert result is not None
        assert result["name"] == "C-pool"
        assert "50%" in result["five_h"]
        assert result["seven_d"] == "--"  # No 7D data

    def test_full_pair(self):
        """Both 5H and 7D projections should produce complete pool data."""
        now = datetime.now(timezone.utc)
        proj_5h = _make_projection("cc", "C-5H", used=0.30, remaining=0.70,
                                    resets_at=int(now.timestamp()) + 3600)
        proj_7d = _make_projection("cc", "C-7D", used=0.65, remaining=0.35,
                                    resets_at=int(now.timestamp()) + 86400)
        result = _build_pool_pair_from_projections(
            [proj_5h, proj_7d], "C", "C-5H", "C-7D", "C-pool", now,
        )
        assert result is not None
        assert "30%" in result["five_h"]
        assert "65%" in result["seven_d"]
        assert result["remaining_fraction"] == 0.35  # min of both


class TestPresenterCollectLiveSnapshot:
    """Integration tests for collect_live_snapshot with real projection data."""

    def test_snapshot_structure(self, tmp_path: Path):
        (tmp_path / "_sys").mkdir(parents=True)
        presenter = TelemetryPresenter(use_color=False, workspace_root=tmp_path)
        snapshot = presenter.collect_live_snapshot()
        assert "peers" in snapshot
        assert "ag" in snapshot["peers"]
        assert "cc" in snapshot["peers"]
        assert "cx" in snapshot["peers"]

    def test_render_with_real_projections_produces_valid_output(self, tmp_path: Path):
        """Full render with real projections should produce valid dashboard output."""
        (tmp_path / "_sys").mkdir(parents=True)
        projections = [
            _make_projection("cc", "C-5H", used=0.25, remaining=0.75),
            _make_projection("cc", "C-7D", used=0.60, remaining=0.40),
            _make_projection("cx", "X-5H", used=0.10, remaining=0.90),
            _make_projection("cx", "X-7D", used=0.45, remaining=0.55),
        ]
        presenter = TelemetryPresenter(
            use_color=False, workspace_root=tmp_path,
            usage_projections=projections,
        )
        snapshot = presenter.collect_live_snapshot()
        rendered = presenter.render(snapshot)
        assert "PeerHub Multi-Peer Dashboard" in rendered
        assert "C-pool" in rendered
        assert "X-pool" in rendered
