"""#343: the live evidence file carries the version so a second run or the next release does not collide."""
import re

import peerhub
from tests.communication.live import live_support as ls


def test_evidence_path_includes_date_and_version(monkeypatch, tmp_path):
    monkeypatch.setenv("PEERHUB_LIVE_EVIDENCE_DIR", str(tmp_path))
    p = ls.evidence_path()
    assert p.parent == tmp_path
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-v" + re.escape(peerhub.__version__) + r"\.json", p.name)
