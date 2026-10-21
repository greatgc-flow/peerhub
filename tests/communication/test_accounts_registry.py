"""P1 RED contracts. accounts API: bind_account/get_binding/list_bindings.

bind_account(db, *, peer_id, provider, alias, root) returns a binding with
peer_id/provider/alias/slot_id/config_root/created_at attributes. AccountError
is a ValueError. Reads return a binding (or None) and a list of bindings.
"""
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from peerhub.cli.app import main
from peerhub.core.models import Peer
from peerhub.core.store import CoreStore
from peerhub.extensions import accounts
from peerhub.extensions.ask import ask
from peerhub.extensions.bridge import PrespawnError
from peerhub.extensions.peer_kinds import ALIASES


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "core.db"
    CoreStore(db)
    return db, home


def bind(world, tmp_path, peer="worker", provider="cc", root=None):
    if root is None:
        root = tmp_path / f"root-{peer}"
        root.mkdir()
    return accounts.bind_account(world[0], peer_id=peer, provider=provider, alias=f"alias-{peer}", root=root)


def link_dir(link, target):
    if sys.platform == "win32":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, timeout=10)
        if result.returncode:
            pytest.skip("OS refused directory junction: " + result.stderr.decode(errors="replace"))
    else:
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"OS refused directory symlink: {exc}")


@pytest.mark.parametrize("provider", ["cc", "cx"])
def test_lazy_registry_and_core_valid_peer(world, tmp_path, monkeypatch, provider):
    db, _ = world
    with sqlite3.connect(db) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='account_bindings'").fetchone()
    validated = []
    original = Peer.model_validate

    def validate(cls, value, *args, **kwargs):
        result = original(value, *args, **kwargs)
        validated.append(result)
        return result

    monkeypatch.setattr(Peer, "model_validate", classmethod(validate))
    binding = bind(world, tmp_path, provider=provider)
    assert any(p.peer_id == "worker" and p.metadata == {"account_bound": True} for p in validated)
    peer = CoreStore(db).get_peer("worker")
    assert peer.adapter_ref == provider and peer.metadata == {"account_bound": True}
    assert binding.peer_id == "worker" and binding.provider == provider
    assert binding.alias == "alias-worker" and re.fullmatch(r"[0-9a-f]{16}", binding.slot_id)
    assert Path(binding.config_root) == tmp_path / "root-worker"
    assert accounts.get_binding(db, "worker") == binding
    assert accounts.get_binding(db, "absent") is None
    assert accounts.list_bindings(db) == [binding]
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == version
        columns = {r[1] for r in conn.execute("PRAGMA table_info(account_bindings)")}
        assert columns == {"peer_id", "provider", "alias", "slot_id", "config_root", "created_at"}


def test_single_immediate_transaction_and_rollback_between_inserts(world, tmp_path, monkeypatch):
    db, _ = world
    bind(world, tmp_path, peer="seed")  # materialize lazy DDL before injecting failure
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TRIGGER fail_binding BEFORE INSERT ON account_bindings
                        WHEN NEW.peer_id='victim' BEGIN
                        SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM peers WHERE peer_id='victim')
                        THEN RAISE(ABORT, 'peer insert missing') END;
                        SELECT RAISE(ABORT, 'between inserts'); END""")
    statements = []
    connect = sqlite3.connect

    def traced(*args, **kwargs):
        conn = connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(accounts.sqlite3, "connect", traced)
    with pytest.raises((accounts.AccountError, sqlite3.IntegrityError), match="between inserts"):
        bind(world, tmp_path, peer="victim")
    with connect(db) as conn:
        assert conn.execute("SELECT 1 FROM peers WHERE peer_id='victim'").fetchone() is None
        assert conn.execute("SELECT 1 FROM account_bindings WHERE peer_id='victim'").fetchone() is None
    normalized = [s.strip().upper() for s in statements]
    peer_insert = next(i for i, s in enumerate(normalized) if s.startswith("INSERT INTO PEERS"))
    assert any(s == "BEGIN IMMEDIATE" for s in normalized[:peer_insert])
    assert not any(s.startswith("COMMIT") for s in normalized[peer_insert:])


@pytest.mark.parametrize("column,value", [("slot_id", None), ("config_root", None), ("provider", "ag"), ("provider", "alien")])
def test_database_uniqueness_and_provider_check(world, tmp_path, column, value):
    db, _ = world
    binding = bind(world, tmp_path)
    CoreStore(db).register_peer(Peer(peer_id="other"))
    values = dict(peer_id="other", provider="cx", alias="other", slot_id="f" * 16,
                  config_root=str(tmp_path / "other-root"), created_at=binding.created_at)
    values[column] = getattr(binding, column) if value is None else value
    with sqlite3.connect(db) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO account_bindings (peer_id,provider,alias,slot_id,config_root,created_at) VALUES (?,?,?,?,?,?)",
                     tuple(values.values()))


@pytest.mark.parametrize("peer", sorted(ALIASES))
def test_all_builtin_aliases_reserved(world, tmp_path, peer):
    with pytest.raises(accounts.AccountError, match="(?i)reserved|alias|builtin|built-in"):
        bind(world, tmp_path, peer=peer)
    assert CoreStore(world[0]).get_peer(peer) is None


@pytest.mark.parametrize("provider", ["ag", "unknown"])
def test_unsupported_provider_explains_refusal(world, tmp_path, provider):
    with pytest.raises(accounts.AccountError, match="(?i)unsupported|only.*cc.*cx") as exc:
        bind(world, tmp_path, provider=provider)
    assert provider in str(exc.value)
    assert CoreStore(world[0]).get_peer("worker") is None


def test_existing_peer_is_never_overwritten(world, tmp_path):
    store = CoreStore(world[0])
    peer = Peer(peer_id="worker", adapter_ref="cx", metadata={"keep": "original"})
    store.register_peer(peer)
    with pytest.raises(accounts.AccountError, match="(?i)exist|registered"):
        bind(world, tmp_path)
    assert store.get_peer("worker") == peer


@pytest.mark.parametrize("peer", ["", None, 42])
def test_core_peer_validation_precedes_insert(world, tmp_path, peer):
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ValueError):
        accounts.bind_account(world[0], peer_id=peer, provider="cc", alias="work", root=root)
    assert CoreStore(world[0]).list_peers_page(after_peer_id=None, limit=10) == []


@pytest.mark.parametrize("shape", ["relative", "missing", "file"])
def test_invalid_root_shapes(world, tmp_path, shape):
    root = tmp_path / "root"
    if shape == "relative":
        root.mkdir()
        root = Path("root")
    elif shape == "file":
        root.write_text("not a directory", encoding="utf-8")
    with pytest.raises(accounts.AccountError):
        bind(world, tmp_path, root=root)
    assert CoreStore(world[0]).get_peer("worker") is None


@pytest.mark.parametrize("nested", [False, True])
def test_reparse_point_anywhere_in_root_chain(world, tmp_path, nested):
    actual = tmp_path / "actual"
    (actual / "child").mkdir(parents=True)
    link = tmp_path / "link"
    link_dir(link, actual)
    with pytest.raises(accounts.AccountError, match="(?i)symlink|junction|reparse|realpath"):
        bind(world, tmp_path, root=link / "child" if nested else link)


@pytest.mark.parametrize("relation", ["equal", "ancestor", "descendant", "casefold"])
def test_overlap_with_other_binding(world, tmp_path, relation):
    root = tmp_path / "RootCase"
    root.mkdir()
    bind(world, tmp_path, peer="first", root=root)
    candidate = {"equal": root, "ancestor": tmp_path, "descendant": root / "child", "casefold": tmp_path / "rootcase"}[relation]
    candidate.mkdir(exist_ok=True)
    with pytest.raises(accounts.AccountError, match="(?i)overlap|root|bound"):
        bind(world, tmp_path, peer="second", root=candidate)


@pytest.mark.parametrize("source", ["CLAUDE_CONFIG_DIR", "CODEX_HOME", ".claude", ".codex"])
@pytest.mark.parametrize("relation", ["equal", "ancestor", "descendant"])
def test_ambient_and_default_root_overlap(world, tmp_path, monkeypatch, source, relation):
    _, home = world
    protected = home / source if source.startswith(".") else tmp_path / source
    protected.mkdir()
    if not source.startswith("."):
        monkeypatch.setenv(source, str(protected))
    candidate = {"equal": protected, "ancestor": protected.parent, "descendant": protected / "child"}[relation]
    candidate.mkdir(exist_ok=True)
    with pytest.raises(accounts.AccountError):
        bind(world, tmp_path, root=candidate)


def test_root_with_unicode_and_spaces_never_reads_credentials(world, tmp_path, monkeypatch):
    root = tmp_path / "계정 root 한글"
    root.mkdir()
    secret = root / "auth.json"
    secret.write_text('{"token":"sentinel-credential-value"}', encoding="utf-8")
    import builtins
    import io

    original_builtin, original_io = builtins.open, io.open

    def guard(original):
        def guarded(path, *args, **kwargs):
            if isinstance(path, (str, Path)) and Path(path) == secret:
                raise AssertionError("PeerHub must not open credential files")
            return original(path, *args, **kwargs)
        return guarded

    monkeypatch.setattr(builtins, "open", guard(original_builtin))
    monkeypatch.setattr(io, "open", guard(original_io))
    assert bind(world, tmp_path, root=root).slot_id


@pytest.mark.parametrize("action", ["unbind", "rebind"])
def test_cli_has_no_account_mutation_commands(world, action):
    with pytest.raises(SystemExit) as exc:
        main(["--db", str(world[0]), "account", action, "--peer", "worker"])
    assert exc.value.code == 2


@pytest.mark.parametrize("command", ["list", "show"])
@pytest.mark.parametrize("json_output", [False, True])
def test_cli_bind_and_inspection_root_visibility(world, tmp_path, capsys, command, json_output):
    db, _ = world
    root = tmp_path / "private-root"
    root.mkdir()
    secret = "credential-must-never-print"
    (root / "auth.json").write_text(secret, encoding="utf-8")
    base = ["--db", str(db), "account"]
    assert main(base + ["bind", "--peer", "worker", "--provider", "cc", "--alias", "work", "--root", str(root)]) == 0
    bound_output = capsys.readouterr()
    assert str(root) not in bound_output.out and secret not in bound_output.out + bound_output.err
    args = base + [command] + (["--peer", "worker"] if command == "show" else []) + (["--json"] if json_output else [])
    assert main(args) == 0
    output = capsys.readouterr()
    assert str(root) not in output.out and "config_root" not in output.out
    assert all(value in output.out for value in ("worker", "cc", "work", accounts.get_binding(db, "worker").slot_id))
    assert main(args + ["--show-root"]) == 0
    visible = capsys.readouterr()
    rendered = json.dumps(json.loads(visible.out), ensure_ascii=False) if json_output else visible.out
    assert str(root) in rendered.replace("\\\\", "\\")
    assert secret not in output.out + output.err + visible.out + visible.err


@pytest.mark.parametrize("mutation", ["delete-row", "clear-marker", "peer-register"])
def test_marker_row_xor_fails_closed(world, tmp_path, monkeypatch, capsys, mutation):
    db, _ = world
    bind(world, tmp_path)
    if mutation == "delete-row":
        with sqlite3.connect(db) as conn:
            conn.execute("DELETE FROM account_bindings WHERE peer_id='worker'")
    elif mutation == "clear-marker":
        CoreStore(db).register_peer(Peer(peer_id="worker", adapter_ref="cc"))
    else:
        assert main(["--db", str(db), "peer", "register", "--peer", "worker", "--adapter", "cc"]) == 0
        capsys.readouterr()

    def forbidden(*args, **kwargs):
        raise AssertionError("inconsistent account must not spawn")

    monkeypatch.setattr("peerhub.extensions.adapters.base.run_bounded", forbidden)
    monkeypatch.setattr("peerhub.extensions.adapters.base.BoundedProcess", forbidden)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(PrespawnError, match="(?i)account|marker|binding"):
        ask(db, "worker", "hello", workspace=workspace)
