"""POSIX launch gate: a supervised provider execs only after the launch is released (Linux and macOS only).

Kept out of the Windows platform-sensitive inventory, which rejects skipped tests.
"""
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from tempfile import NamedTemporaryFile

import psutil
import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX launch gate")
@pytest.mark.parametrize("approved", [False, True])
def test_supervised_process_only_execs_after_approval(approved):
    # Close the gate's writer by exiting the wrapper, exactly as a wrapper crash does.
    wrapper = textwrap.dedent("""
        import os
        import sys
        from peerhub.extensions.adapters.process import BoundedProcess

        provider = [sys.executable, "-c",
                    "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('executed')",
                    sys.argv[2]]
        process = BoundedProcess(provider, stdin=None, cwd=os.getcwd(), env=os.environ,
                                 timeout_s=10, max_bytes=1024)
        process.start(suspended=True)
        print(process.pid, flush=True)
        if sys.argv[1] == "True":
            process.release_launch()
            assert process.wait().returncode == 0
        else:
            os._exit(17)
    """)
    with NamedTemporaryFile() as file:
        result = subprocess.run([sys.executable, "-c", wrapper, str(approved), file.name],
                                capture_output=True, timeout=15)
        assert result.returncode == (0 if approved else 17), result.stderr
        if not approved:
            pid = int(result.stdout)
            deadline = time.monotonic() + 5
            while True:
                try:
                    if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                        break
                except psutil.NoSuchProcess:
                    break
                assert time.monotonic() < deadline, "launch process did not exit after losing its gate"
                time.sleep(0.01)
        assert Path(file.name).read_text() == ("executed" if approved else "")
