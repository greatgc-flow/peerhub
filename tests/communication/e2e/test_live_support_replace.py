"""The live-evidence writer must survive a transient Windows PermissionError on the atomic replace (and still fail if it persists)."""
import os

import pytest

from tests.communication.live import live_support as ls


def test_replace_with_retry_rides_out_a_transient_permission_error(tmp_path, monkeypatch):
    src, dst = tmp_path / "a.tmp", tmp_path / "a.json"
    src.write_text("new")
    dst.write_text("old")
    real, calls = os.replace, {"n": 0}

    def flaky(a, b):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise PermissionError("[WinError 5] target briefly locked")
        return real(a, b)

    monkeypatch.setattr(ls.os, "replace", flaky)
    monkeypatch.setattr(ls.time, "sleep", lambda s: None)
    ls.replace_with_retry(src, dst)
    assert dst.read_text() == "new" and calls["n"] == 4


def test_a_persistent_permission_error_is_still_raised(tmp_path, monkeypatch):
    src, dst = tmp_path / "a.tmp", tmp_path / "a.json"
    src.write_text("new")
    monkeypatch.setattr(ls.os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError("denied")))
    monkeypatch.setattr(ls.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        ls.replace_with_retry(src, dst, attempts=3)


def test_merge_evidence_uses_the_retrying_replace(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(ls, "replace_with_retry", lambda a, b: (seen.append((a.name, b.name)), os.replace(a, b))[1])
    ls.merge_evidence("cx", "discovery", {"available": True}, path=tmp_path / "ev.json")
    assert seen == [("ev.json.tmp", "ev.json")] and (tmp_path / "ev.json").is_file()
