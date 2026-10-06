"""Console encoding policy (REL-002/REL-011 follow-up): the CLIs never force an encoding, never crash on unencodable text."""
import json
import sys

import pytest

from tests.communication.harness import pkg_env
from tests.communication.harness.pkg_env import REPO

pytestmark = [pytest.mark.package, pytest.mark.release, pytest.mark.timeout(300)]

TEXT = "café — 한글 \U0001f642"
SNIPPET = "from peerhub._console import tolerant_streams; tolerant_streams(); import sys; sys.stdout.write(%r)" % TEXT


def _run(env_extra):
    env = pkg_env.clean_env(env_extra)
    env["PYTHONPATH"] = str(REPO)
    import subprocess

    cp = subprocess.run([sys.executable, "-c", SNIPPET], capture_output=True, env=env, cwd=str(REPO), stdin=subprocess.DEVNULL)
    return cp


@pytest.mark.parametrize("enc", ["cp1252", "ascii", "utf-8", "cp949"])
def test_explicit_pythonioencoding_is_respected_and_unencodable_characters_are_replaced(enc):
    cp = _run({"PYTHONIOENCODING": enc})
    assert cp.returncode == 0, cp.stderr.decode("utf-8", "replace")  # no UnicodeEncodeError
    expected = TEXT.encode(enc, "replace")  # independent oracle: the codec itself
    assert cp.stdout == expected
    if enc != "utf-8":
        assert b"?" in cp.stdout or enc == "cp949" and TEXT.encode(enc, "replace") != TEXT.encode("utf-8")  # something was replaced
    else:
        assert cp.stdout.decode("utf-8") == TEXT  # positive control: a capable encoding is lossless


def test_default_pipe_encoding_does_not_crash_and_is_the_locale_encoding():
    cp = _run({})
    assert cp.returncode == 0, cp.stderr.decode("utf-8", "replace")
    probe = pkg_env.run([sys.executable, "-c", "import sys; print(sys.stdout.encoding)"], cwd=REPO, env=pkg_env.clean_env())
    enc = probe.stdout.strip()  # what a piped child defaults to without PYTHONUTF8/PYTHONIOENCODING (asked of a plain child)
    assert cp.stdout == TEXT.encode(enc, "replace")


def test_explicit_error_handler_choice_is_not_overridden():
    cp = _run({"PYTHONIOENCODING": "ascii:backslashreplace"})
    assert cp.stdout == TEXT.encode("ascii", "backslashreplace")


def test_cli_json_output_is_lossless_under_any_console_encoding(tmp_path):
    db = tmp_path / "core.db"
    for enc in ("cp1252", "ascii", "utf-8"):
        cp = pkg_env.run([sys.executable, "-m", "peerhub.cli.app", "--db", str(db), "peer", "register", "--id", "p-" + enc, "--name", TEXT],
                         cwd=REPO, env={**pkg_env.clean_env({"PYTHONIOENCODING": enc}), "PYTHONPATH": str(REPO)})
        assert cp.returncode == 0, cp.stderr
        assert json.loads(cp.stdout)["display_name"] == TEXT  # ASCII-escaped JSON: exact round trip, no replacement
