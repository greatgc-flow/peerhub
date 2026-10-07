import os
import sys

import pytest

from peerhub.extensions.adapters.process import BoundedProcess
from peerhub.extensions.adapters import CliRuntimeTarget
from tests.communication.adapters.conftest import FAKE
from types import SimpleNamespace


def test_stdout_is_presented_while_process_is_running(tmp_path):
    seen = []
    runner = BoundedProcess([sys.executable, "-u", "-c",
                             "import time; print('early',flush=True); time.sleep(0.5); print('late',flush=True)"],
                            stdin=None, cwd=str(tmp_path), env=os.environ, timeout_s=5,
                            on_stdout=lambda b: seen.append((b, runner._proc.poll())))
    result = runner.start().wait()
    assert result.returncode == 0 and b"early" in result.stdout
    assert any(b"early" in b and exit_code is None for b, exit_code in seen)


@pytest.mark.parametrize("kind", ["cc", "cx", "ag"])
def test_vendor_output_is_text_and_secret_redacted(tmp_path, kind):
    seen = []
    runtime = CliRuntimeTarget(kind, tmp_path, command=[sys.executable, FAKE],
                               env_extra={"FAKE_KIND": kind, "FAKE_MODE": "secret_echo", "FAKE_SECRET": "test-secret-abcdef"},
                               extra_secrets=["test-secret-abcdef"], on_output=seen.append)
    events = list(runtime.deliver(runtime.create_session(), SimpleNamespace(body="question"), []))
    assert [e[0] for e in events] == ["started", "terminal"]
    assert seen and all("test-secret-abcdef" not in s for s in seen)
    assert all(not s.startswith("{") for s in seen)
