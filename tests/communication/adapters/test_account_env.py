"""P1 RED adapter contracts: account identity before claims, guarded env after.

accounts.AccountRef(slot_id), AccountSnapshot(slot_id, provider, root_realpath)
are immutable dataclasses. CliRuntimeTarget.bind_account_env(snapshot) attaches
the validated environment; env_scrubbed exposes original-case names only.
"""
import hashlib
import json
import os
import re
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from peerhub.extensions import accounts
from peerhub.extensions.adapters import CliRuntimeTarget
from peerhub.extensions.adapters import base
from peerhub.extensions.bridge import PrespawnError
from peerhub.extensions.diag_readiness import _matches_binding
from tests.communication.adapters.conftest import calls
from tests.communication.adapters.test_t1_adapters import drain, rec

SLOT_A = "0123456789abcdef"
SLOT_B = "fedcba9876543210"
PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_", "OPENAI_", "CODEX_")


@pytest.fixture
def parent_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    # A mapping models the original spelling on POSIX and case-insensitive
    # matching on Windows, where os.environ normally uppercases inserted keys.
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(PREFIXES) and k.upper() != "CLAUDE_CONFIG_DIR"}
    env.update({"CLAUDE_CONFIG_DIR": str(tmp_path / "ambient-cc"),
                "CODEX_HOME": str(tmp_path / "ambient-cx"),
                "AnThRoPiC_ApI_KeY": "parent-anthropic-credential",
                "claude_code_auth_token": "parent-claude-credential",
                "OpenAI_API_KEY": "parent-openai-credential",
                "cOdEx_ApI_KeY": "parent-codex-credential",
                "ANTHROPIC_BASE_URL": "https://parent.invalid",
                "CLAUDE_CODE_SWITCH": "parent-switch",
                "OPENAI_MODEL": "parent-model",
                "CODEX_PROFILE": "parent-profile",
                "UNRELATED_SETTING": "keep-me"})
    monkeypatch.setattr(base.os, "environ", env)
    return env


@pytest.mark.parametrize("kind", ["cc", "cx"])
@pytest.mark.parametrize("writable", [False, True])
def test_unbound_binding_and_fingerprint_golden(tmp_path, kind, writable):
    # Frozen pre-P1 formula; account support must not change this scope input.
    target = CliRuntimeTarget(kind, tmp_path, model="m-test", effort="low", writable=writable)
    scope = os.path.normcase(str(tmp_path.resolve())) + ("|w" if writable else "|r")
    golden = "m-test/low#" + hashlib.sha256(scope.encode("utf-8")).hexdigest()[:12]
    assert target.binding() == golden
    assert target.fingerprint() == f"adapter:{kind}:v2"


@pytest.mark.parametrize("kind", ["cc", "cx"])
@pytest.mark.parametrize("writable", [False, True])
def test_account_scope_hash_and_readiness_format(tmp_path, monkeypatch, kind, writable):
    monkeypatch.setattr("peerhub.extensions.adapters.profiles.resolve_profile", lambda *args: ("m-test", "low"))
    profile = f"{kind}.standard"
    options = dict(model="m-test", effort="low", profile=profile, writable=writable)
    plain = CliRuntimeTarget(kind, tmp_path, **options)
    a = CliRuntimeTarget(kind, tmp_path, account=accounts.AccountRef(SLOT_A), **options)
    b = CliRuntimeTarget(kind, tmp_path, account=accounts.AccountRef(SLOT_B), **options)
    prefix = f"{profile}/m-test/low"
    scope = f"{os.path.normcase(str(tmp_path.resolve()))}|{'w' if writable else 'r'}|acct:{SLOT_A}"
    assert a.binding() == prefix + "#" + hashlib.sha256(scope.encode("utf-8")).hexdigest()[:12]
    assert re.fullmatch(re.escape(prefix) + r"#[0-9a-f]{12}", a.binding())
    assert _matches_binding(a.binding(), prefix)
    assert len({plain.binding(), a.binding(), b.binding()}) == 3
    assert plain.fingerprint() == a.fingerprint() == b.fingerprint() == f"adapter:{kind}:v2"


def test_reference_and_validated_snapshot_are_immutable(tmp_path):
    ref = accounts.AccountRef(SLOT_A)
    snapshot = accounts.AccountSnapshot(slot_id=SLOT_A, provider="cc", root_realpath=tmp_path)
    with pytest.raises(FrozenInstanceError):
        ref.slot_id = SLOT_B
    with pytest.raises(FrozenInstanceError):
        snapshot.root_realpath = tmp_path / "elsewhere"


@pytest.mark.parametrize("kind", ["cc", "cx"])
@pytest.mark.parametrize("operation", ["env", "discover", "deliver"])
def test_bound_target_fails_closed_until_env_attached(mk, monkeypatch, kind, operation):
    target = mk(kind, account=accounts.AccountRef(SLOT_A))

    def forbidden(*args, **kwargs):
        raise AssertionError("unbound account environment reached subprocess")

    monkeypatch.setattr(base, "run_bounded", forbidden)
    monkeypatch.setattr(base, "BoundedProcess", forbidden)
    with pytest.raises(PrespawnError, match="(?i)account|environment|bound"):
        if operation == "env":
            _ = target._env
        elif operation == "discover":
            target.discover()
        else:
            drain(target.deliver(target.create_session(), rec(), []))
    assert calls(mk.log) == []


@pytest.mark.parametrize("kind,root_var", [("cc", "CLAUDE_CONFIG_DIR"), ("cx", "CODEX_HOME")])
def test_scrubbed_env_disclosure_parent_unchanged_and_sanitized(tmp_path, parent_env, kind, root_var):
    root = tmp_path / "private account root"
    root.mkdir()
    before = dict(parent_env)
    target = CliRuntimeTarget(kind, tmp_path, account=accounts.AccountRef(SLOT_A), env_extra={"FAKE_MODE": "ok"})
    target.bind_account_env(accounts.AccountSnapshot(slot_id=SLOT_A, provider=kind, root_realpath=root))
    child = target._env
    assert child[root_var] == str(root)
    removed = {name for name in before if name.upper().startswith(PREFIXES) and name.upper() != root_var}
    expected = {name: value for name, value in before.items() if name not in removed}
    expected[root_var] = str(root)
    expected["FAKE_MODE"] = "ok"
    assert child == expected  # all unrelated variables retain their original values
    assert set(target.env_scrubbed) == removed
    assert len(target.env_scrubbed) == len(removed)
    assert parent_env == before and os.environ == before
    for name in removed:
        assert name not in child
        assert before[name] not in target.env_scrubbed
    # Parent credentials remain sanitizer inputs after they disappear from env.
    secrets = [before[name] for name in before if "credential" in before[name]]
    evidence = target._san.clean(" | ".join([*secrets, str(root)]))
    assert all(value not in evidence for value in [*secrets, str(root)])
    assert "[REDACTED]" in evidence


@pytest.mark.parametrize("kind", ["cc", "cx"])
def test_unbound_environment_golden(tmp_path, parent_env, kind):
    before = dict(parent_env)
    target = CliRuntimeTarget(kind, tmp_path, env_extra={"OPENAI_API_KEY": "explicit-override", "NEW": "extra"})
    assert target._env == {**before, "OPENAI_API_KEY": "explicit-override", "NEW": "extra"}
    assert parent_env == before


def test_mismatched_snapshot_never_attaches_another_account(tmp_path):
    target = CliRuntimeTarget("cc", tmp_path, account=accounts.AccountRef(SLOT_A))
    with pytest.raises(PrespawnError):
        target.bind_account_env(accounts.AccountSnapshot(slot_id=SLOT_B, provider="cc", root_realpath=tmp_path))
    with pytest.raises(PrespawnError):
        _ = target._env


@pytest.mark.parametrize("root", [r"C:\Private\RootCase\계정", "/Private/RootCase/계정"])
def test_account_root_representations_are_redacted(tmp_path, root):
    target = CliRuntimeTarget("cc", tmp_path, account=accounts.AccountRef(SLOT_A))
    snapshot = accounts.AccountSnapshot(slot_id=SLOT_A, provider="cc", root_realpath=Path(root))
    target.bind_account_env(snapshot)
    raw = str(snapshot.root_realpath)
    spellings = {raw, raw.replace("\\", "/"), raw.lower(), raw.casefold(), raw.replace("\\", "/").casefold()}
    spellings |= {json.dumps(value)[1:-1] for value in list(spellings)}
    for spelling in spellings:
        cleaned = target._san.clean(f"failed to open {spelling}")
        assert spelling not in cleaned and "[REDACTED]" in cleaned
