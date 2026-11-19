"""The live monitor leaves two rows free so the first line (ATTN) never scrolls off the screen."""
from peerhub.cli.monitor import LIVE_ROW_MARGIN, _live_rows  # pyright: ignore[reportPrivateUsage]


def test_live_rows_leave_a_margin():
    assert LIVE_ROW_MARGIN == 2
    assert _live_rows(37) == 35 and _live_rows(24) == 22


def test_live_rows_never_below_a_usable_minimum():
    assert _live_rows(5) == 8 and _live_rows(10) == 8 and _live_rows(11) == 9
