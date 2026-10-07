"""M2.0: boot discovery after a restart, exact dependency pins, fail-closed re-check of ENABLED extensions."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from peerhub.extensions.host import ExtensionHost, MissingDependencyError, RegistrationConflictError
from peerhub.extensions.manifest import SchemaValidationError, validate_manifest


def write_ext(root: Path, ext_id: str, version: str = "1.0.0", deps: list[str] | None = None) -> Path:
    d = root / ext_id
    d.mkdir(parents=True)
    (d / "main.py").write_text("")
    (d / "manifest.json").write_text(json.dumps({"id": ext_id, "version": version, "entrypoint": "main.py", "dependencies": deps or []}))
    return d


def test_boot_discovers_every_manifest_and_isolates_a_broken_one(tmp_path):
    exts = tmp_path / "exts"
    write_ext(exts, "ext_a")
    write_ext(exts, "ext_b")
    bad = exts / "ext_bad"
    bad.mkdir()
    (bad / "manifest.json").write_text("{not json")
    host = ExtensionHost(tmp_path / "host.db", extensions_dir=exts)
    report = host.boot()
    assert report["discovered"] == ["ext_a", "ext_b"] and list(report["errors"]) == ["ext_bad"]
    assert host.get_state("ext_a") == host.get_state("ext_b") == "DISCOVERED"  # the broken one did not stop the others


def test_boot_after_restart_restores_manifests_and_keeps_enabled_state(tmp_path):
    exts = tmp_path / "exts"
    write_ext(exts, "ext_a")
    h1 = ExtensionHost(tmp_path / "host.db", extensions_dir=exts)
    h1.boot()
    h1.enable("ext_a")
    h2 = ExtensionHost(tmp_path / "host.db", extensions_dir=exts)  # a new process: manifests are not in memory yet
    assert h2.manifests == {}
    assert h2.boot()["failed"] == {} and "ext_a" in h2.manifests and h2.get_state("ext_a") == "ENABLED"


def test_exact_version_pin_is_enforced_and_unpinned_accepts_any(tmp_path):
    exts = tmp_path / "exts"
    write_ext(exts, "ext_core", "1.0.1")
    write_ext(exts, "ext_pinned", deps=["ext_core==1.0.0"])
    write_ext(exts, "ext_free", deps=["ext_core"])
    host = ExtensionHost(tmp_path / "host.db", extensions_dir=exts)
    host.boot()
    host.enable("ext_core")
    host.enable("ext_free")  # no pin: any registered version
    with pytest.raises(MissingDependencyError, match="requires version 1.0.0"):
        host.enable("ext_pinned")
    assert host.get_state("ext_pinned") == "FAILED"


def test_exact_pin_matches_only_the_identical_version_string(tmp_path):
    for registered, ok in (("1.0.0", True), ("1.0.0-rc", False), ("1.0.10", False)):
        base = tmp_path / registered
        write_ext(base, "ext_core", registered)
        write_ext(base, "ext_user", deps=["ext_core==1.0.0"])
        host = ExtensionHost(base / "host.db", extensions_dir=base)
        host.boot()
        host.enable("ext_core")
        if ok:
            assert host.enable("ext_user") == "ENABLED"
        else:
            with pytest.raises(MissingDependencyError):
                host.enable("ext_user")


def test_boot_fails_closed_for_an_enabled_extension_whose_dependency_went_away(tmp_path):
    exts = tmp_path / "exts"
    write_ext(exts, "ext_core")
    write_ext(exts, "ext_user", deps=["ext_core==1.0.0"])
    h1 = ExtensionHost(tmp_path / "host.db", extensions_dir=exts)
    h1.boot()
    h1.enable("ext_core")
    h1.enable("ext_user")
    h1.disable("ext_core")  # the dependency is no longer enabled while ext_user stays ENABLED
    report = ExtensionHost(tmp_path / "host.db", extensions_dir=exts).boot()
    assert "ext_user" in report["failed"] and "ext_core" in report["failed"]["ext_user"]
    assert ExtensionHost(tmp_path / "host.db").get_state("ext_user") == "FAILED"


def test_a_changed_manifest_version_is_not_adopted_silently(tmp_path):
    exts = tmp_path / "exts"
    d = write_ext(exts, "ext_a", "1.0.0")
    host = ExtensionHost(tmp_path / "host.db", extensions_dir=exts)
    host.boot()
    (d / "manifest.json").write_text(json.dumps({"id": "ext_a", "version": "2.0.0", "entrypoint": "main.py"}))
    fresh = ExtensionHost(tmp_path / "host.db", extensions_dir=exts)
    with pytest.raises(RegistrationConflictError):
        fresh.discover(d)
    assert fresh.boot()["errors"]["ext_a"].startswith("RegistrationConflictError")


@pytest.mark.parametrize("bad", ["ext_core>=1.0", "ext_core==", "Core", "ext_core==1.0.0==2", "ext_core ==1.0.0"])
def test_dependency_syntax_is_exact_or_unpinned_only(bad):
    with pytest.raises(SchemaValidationError):
        validate_manifest({"id": "ext_x", "version": "1", "entrypoint": "m.py", "dependencies": [bad]})
    assert validate_manifest({"id": "ext_x", "version": "1", "entrypoint": "m.py", "dependencies": ["ext_core", "ext_core2==1.2.3"]})


def test_a_missing_entrypoint_fails_enablement_instead_of_leaving_metadata_enabled(tmp_path):
    from peerhub.extensions.host import ExtensionHookError

    exts = tmp_path / "exts"
    d = write_ext(exts, "ext_a")
    (d / "main.py").unlink()  # the manifest is valid but the module it names is gone
    host = ExtensionHost(tmp_path / "host.db", extensions_dir=exts)
    host.boot()
    with pytest.raises(ExtensionHookError, match="entrypoint"):
        host.enable("ext_a")
    assert host.get_state("ext_a") == "FAILED" and "ext_a" not in host.loaded_modules


def test_boot_cascades_a_dependency_failure_even_when_the_dependent_sorts_first(tmp_path):
    exts = tmp_path / "exts"
    write_ext(exts, "ext_a_user", deps=["ext_b_mid"])  # sorts BEFORE its dependency
    write_ext(exts, "ext_b_mid", deps=["ext_c_base"])
    write_ext(exts, "ext_c_base")
    h1 = ExtensionHost(tmp_path / "host.db", extensions_dir=exts)
    h1.boot()
    for ext in ("ext_c_base", "ext_b_mid", "ext_a_user"):
        h1.enable(ext)
    (exts / "ext_c_base" / "main.py").unlink()  # the base loses its entrypoint while the process is down
    report = ExtensionHost(tmp_path / "host.db", extensions_dir=exts).boot()
    assert set(report["failed"]) == {"ext_c_base", "ext_b_mid", "ext_a_user"}  # the whole chain, not just the first victim
    host = ExtensionHost(tmp_path / "host.db")
    assert {host.get_state(e) for e in report["failed"]} == {"FAILED"}


def test_boot_fails_an_enabled_extension_whose_manifest_became_unreadable(tmp_path):
    exts = tmp_path / "exts"
    d = write_ext(exts, "ext_a")
    write_ext(exts, "ext_user", deps=["ext_a"])
    h1 = ExtensionHost(tmp_path / "host.db", extensions_dir=exts)
    h1.boot()
    h1.enable("ext_a")
    h1.enable("ext_user")
    (d / "manifest.json").write_text("{corrupt")
    report = ExtensionHost(tmp_path / "host.db", extensions_dir=exts).boot()
    assert "ext_a" in report["errors"] and report["failed"]["ext_a"].startswith("manifest missing")
    assert "ext_user" in report["failed"]  # its dependency is gone, so it cannot stay enabled either


def test_ext_022_a_discovered_extension_that_fails_validation_goes_straight_to_failed(tmp_path):
    """EXT-022: DISCOVERED -> FAILED strictly (never through VALIDATED/ENABLED), and a broken sibling folder cannot stop the scan."""
    from peerhub.extensions.host import ExtensionHookError

    exts = tmp_path / "exts"
    ok = write_ext(exts, "ext_ok")
    broken = write_ext(exts, "ext_broken")
    (broken / "main.py").unlink()  # a valid manifest whose module is gone: it fails validation when enabled
    junk = exts / "ext_junk"
    junk.mkdir()
    (junk / "manifest.json").write_text("[]")  # not even an object
    host = ExtensionHost(tmp_path / "host.db", extensions_dir=exts)
    report = host.boot()
    assert sorted(report["discovered"]) == ["ext_broken", "ext_ok"] and "ext_junk" in report["errors"]
    assert host.get_state("ext_broken") == "DISCOVERED"
    states = []
    original = host.transition
    host.transition = lambda ext, target: (states.append(target), original(ext, target))[1]  # type: ignore[method-assign]
    with pytest.raises(ExtensionHookError):
        host.enable("ext_broken")
    assert states == ["FAILED"] and host.get_state("ext_broken") == "FAILED"  # no VALIDATED/ENABLED in between
    assert host.enable("ext_ok") == "ENABLED" and ok.is_dir()  # the healthy one is unaffected
