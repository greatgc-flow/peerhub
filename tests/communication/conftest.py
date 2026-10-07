from pathlib import Path

import pytest

from tests.communication.harness import CoreHarness


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
