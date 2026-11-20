"""Only normal-screen frames need a margin."""
import pytest

from peerhub.cli.monitor import LIVE_ROW_MARGIN, _live_rows  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("height", [0, 1, 5, 10, 24, 37])
def test_frame_budgets_match_the_screen(height):
    assert _live_rows(height) == max(0, height - LIVE_ROW_MARGIN)
    assert _live_rows(height, alternate=True) == height
