"""Codex JSON-RPC reader regressions without provider calls."""
import subprocess

import pytest

from peerhub.extensions.observation_model import EvidenceState
from peerhub.extensions.quota_probes import poll_codex_usage


class DummyIdSource:
    def new_id(self, prefix: str) -> str:
        return f"{prefix}-123"


@pytest.mark.parametrize("exited", [False, True])
@pytest.mark.parametrize("nonobjects", [False, True])
def test_codex_reader_keeps_buffered_responses_and_ignores_nonobjects(monkeypatch, exited, nonobjects):
    import io
    import json

    messages = [None, [], 42, "notification"] if nonobjects else []
    messages += [{"method": "notice"}, {"id": 0, "result": {}},
                 {"id": 1, "result": {"rateLimits": {"primary": {
                     "usedPercent": 20, "windowDurationMins": 300, "resetsAt": 1787200158,
                 }}}}]

    class FakeProc:
        stdin = io.StringIO()
        stdout = io.StringIO("malformed\n" + "\n".join(json.dumps(message) for message in messages) + "\n")
        pid = 9999

        def poll(self):
            return 0 if exited else None

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: FakeProc())
    monkeypatch.setattr("peerhub.extensions.quota_probes._cleanup_probe", lambda proc: None)
    monkeypatch.setattr("peerhub.extensions.quota_probes._real_command", lambda *a: ["dummy.exe"])
    readings = poll_codex_usage(DummyIdSource(), "inst-1", "prof-1", deadline_sec=1)
    usage = readings[0]
    assert usage.evidence.state == EvidenceState.MEASURED
    assert usage.evidence.value.remaining_fraction == 0.8
    assert usage.evidence.value.quota_pool_scope == "X-5H"


