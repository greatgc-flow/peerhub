"""The package fixture must test supplied release bytes without rebuilding them."""
from types import SimpleNamespace

import pytest

from tests.communication.harness import pkg_env
from tests.communication.package.conftest import dist

pytestmark = [pytest.mark.package, pytest.mark.release]


def _forbid_build(*args, **kwargs):
    pytest.fail("supplied distributions must not copy or rebuild the checkout")


def test_dist_fixture_consumes_supplied_directory(monkeypatch, tmp_path, tmp_path_factory):
    directory = tmp_path / "release dist"
    directory.mkdir()
    wheel = directory / "peerhub-1.0-py3-none-any.whl"
    sdist = directory / "peerhub-1.0.tar.gz"
    wheel.write_bytes(b"published wheel")
    sdist.write_bytes(b"published sdist")
    monkeypatch.setenv("PEERHUB_DIST_DIR", str(directory))
    monkeypatch.setattr(pkg_env, "copy_checkout", _forbid_build)
    monkeypatch.setattr(pkg_env, "build_dist", _forbid_build)
    supplied = dist.__wrapped__(tmp_path_factory)
    assert supplied.wheel == wheel and supplied.sdist == sdist
    assert supplied.wheel.read_bytes() == b"published wheel"
    assert supplied.sdist.read_bytes() == b"published sdist"


@pytest.mark.parametrize("contents", [[], ["peerhub-1.0-py3-none-any.whl"], ["peerhub-1.0.tar.gz"],
                                      ["one.whl", "two.whl", "peerhub-1.0.tar.gz"]])
def test_dist_fixture_rejects_incomplete_or_ambiguous_directory(monkeypatch, tmp_path, tmp_path_factory, contents):
    for name in contents:
        (tmp_path / name).write_bytes(b"artifact")
    monkeypatch.setenv("PEERHUB_DIST_DIR", str(tmp_path))
    monkeypatch.setattr(pkg_env, "build_dist", _forbid_build)
    with pytest.raises(pytest.UsageError, match=r"PEERHUB_DIST_DIR=.*exactly one wheel.*one sdist.*found"):
        dist.__wrapped__(tmp_path_factory)


def test_dist_fixture_builds_locally_when_directory_is_unset(monkeypatch, tmp_path, tmp_path_factory):
    monkeypatch.delenv("PEERHUB_DIST_DIR", raising=False)
    calls = []
    # The checkout lives at <session root>/pkg-build/checkout so every xdist worker finds the same source.
    source = tmp_path / "pkg-build" / "checkout"
    monkeypatch.setattr(pkg_env, "copy_checkout", lambda path: path)

    def build(src, out):
        calls.append((src, out))
        out.mkdir(parents=True, exist_ok=True)
        wheel, sdist = out / "local.whl", out / "local.tar.gz"
        wheel.touch()
        sdist.touch()
        return wheel, sdist

    monkeypatch.setattr(pkg_env, "build_dist", build)
    # The fixture builds once per pytest session root (shared by xdist workers) and reads the built files back.
    request = SimpleNamespace(getfixturevalue=lambda name: {"package_session_root": tmp_path}[name])
    local = dist.__wrapped__(request)
    out = calls[0][1]
    assert (local.src, local.wheel, local.sdist) == (source, out / "local.whl", out / "local.tar.gz")
    assert len(calls) == 1 and calls[0][0] == source
