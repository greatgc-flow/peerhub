"""Peers are read-only by default; write access is an explicit per-ask choice."""
import pytest

from peerhub.extensions.adapters.base import SPECS
from peerhub.extensions.ask import ask


def test_every_peer_is_read_only_by_default():
    assert SPECS["cx"].argv(None, None, "p")[SPECS["cx"].argv(None, None, "p").index("-s") + 1] == "read-only"
    assert "--permission-mode" not in SPECS["cc"].argv(None, None, "p")
    assert "--mode" not in SPECS["ag"].argv(None, None, "p")


def test_writable_opens_write_access_for_each_peer():
    cx = SPECS["cx"].argv(None, None, "p", True)
    assert cx[cx.index("-s") + 1] == "workspace-write"
    cc = SPECS["cc"].argv(None, None, "p", True)
    assert cc[cc.index("--permission-mode") + 1] == "acceptEdits"
    ag = SPECS["ag"].argv(None, None, "p", True)
    assert ag[ag.index("--mode") + 1] == "accept-edits"


def test_writable_cannot_be_combined_with_an_explicit_runtime(tmp_path):
    with pytest.raises(ValueError, match="writable"):
        ask(tmp_path / "core.db", "cx", "hi", runtime=object(), writable=True)  # type: ignore[arg-type]
