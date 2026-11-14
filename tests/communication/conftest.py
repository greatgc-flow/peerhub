from pathlib import Path

import pytest

from tests.communication.harness import CoreHarness


@pytest.fixture(autouse=True)
def _ag_transport_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing ag tests keep the argv transport; test_ag_stdin_stream.py restores real detection (#366 B1)."""
    from peerhub.extensions.adapters import base

    monkeypatch.setattr(base, "_detect_ag_transport", lambda *args, **kwargs: base.AgTransport("argv", "help", None))


@pytest.fixture
def harness(tmp_path: Path) -> CoreHarness:
    return CoreHarness(tmp_path / "ws")


@pytest.fixture
def bridge_h(tmp_path: Path):
    from tests.communication.harness.bridge import BridgeHarness

    return BridgeHarness(tmp_path / "ws")


@pytest.fixture
def obs_h(tmp_path: Path):
    from tests.communication.harness.observation import ObservationHarness

    return ObservationHarness(tmp_path / "ws")
