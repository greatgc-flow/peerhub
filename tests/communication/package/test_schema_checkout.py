"""Exercise real Git checkout filters, not merely this developer's LF worktree."""
import os
import subprocess

from tests.communication.harness.pkg_env import REPO


def checkout(tmp_path, attrs):
    source = tmp_path / "repository"
    source.mkdir()
    paths = ["peerhub/core/schemas/offset.schema.json", "docs/m1_spec/04_SCHEMAS/offset.schema.json"]
    for name in paths:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPO / name).read_bytes())
    (source / ".gitattributes").write_text(attrs, encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    def git(*args):
        result = subprocess.run(["git", *args], cwd=source, env=env, capture_output=True,
                                text=True, encoding="utf-8", errors="replace")
        assert result.returncode == 0, result.stderr
    git("init", "--quiet")
    git("config", "core.autocrlf", "true")
    git("add", ".")
    exported = tmp_path / "fresh-checkout"
    git("checkout-index", "--all", "--prefix=" + exported.as_posix() + "/")
    return [(exported / name).read_bytes() for name in paths]


def test_autocrlf_checkout_preserves_runtime_schema_bytes(tmp_path):
    attrs = (REPO / ".gitattributes").read_text(encoding="utf-8")
    runtime, frozen = checkout(tmp_path, attrs)
    assert runtime == frozen == (REPO / "docs/m1_spec/04_SCHEMAS/offset.schema.json").read_bytes()
    assert b"\r\n" not in runtime


def test_checkout_oracle_detects_missing_role_based_schema_rule(tmp_path):
    attrs = (REPO / ".gitattributes").read_text(encoding="utf-8")
    attrs = attrs.replace("peerhub/core/schemas/*.json -text", "")
    runtime, frozen = checkout(tmp_path, attrs)
    assert b"\r\n" in runtime and b"\r\n" not in frozen
    assert runtime != frozen
