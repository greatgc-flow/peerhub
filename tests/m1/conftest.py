from pathlib import Path

import pytest

from tests.m1.harness import CoreHarness


@pytest.fixture
def harness(tmp_path: Path) -> CoreHarness:
    return CoreHarness(tmp_path / "ws")
