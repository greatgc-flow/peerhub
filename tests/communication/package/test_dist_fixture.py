"""The package fixture must test supplied release bytes without rebuilding them."""
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
    source = tmp_path / "checkout"
    wheel, sdist = tmp_path / "local.whl", tmp_path / "local.tar.gz"
    monkeypatch.setattr(pkg_env, "copy_checkout", lambda path: source)

    def build(src, out):
        calls.append((src, out))
        return wheel, sdist

    monkeypatch.setattr(pkg_env, "build_dist", build)
    local = dist.__wrapped__(tmp_path_factory)
    assert (local.src, local.wheel, local.sdist) == (source, wheel, sdist)
    assert len(calls) == 1 and calls[0][0] == source
