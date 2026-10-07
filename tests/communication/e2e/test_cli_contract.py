"""M1 public CLI cutover gate items 1, 3, 5: canonical command inventory (derived from the REAL parser), no inert options, help/exit/JSON contract."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from peerhub.cli.app import build_parser, main
from tests.communication.harness.legacy_fixture import make_legacy
from tests.communication.spec import ROOT
from tools import command_inventory as inv

pytestmark = [pytest.mark.integration]
CT = "2026-10-01T00:00:00Z"


# ------------------------------------------------------------------ gate item 1: inventory derived from the parser, committed, compared
def test_inventory_file_equals_the_real_parser():
    committed = json.loads(inv.INVENTORY.read_text(encoding="utf-8"))
    assert committed == json.loads(inv.render(inv.derive()))
    assert [c["command"] for c in committed["commands"]] == [
        "ask", "diag health", "diag quota", "legacy-import apply", "legacy-import dry-run", "monitor", "observation refresh", "offset advance", "offset get", "peer get", "peer register",
        "record append", "record read", "stream create", "stream show"]  # literal oracle: a new/removed leaf must be a conscious edit here and in the file


def test_inventory_detects_divergence_positive_control(monkeypatch):
    real = inv.derive()
    real_build = build_parser

    def extended():
        p = real_build()
        leaf = next(a for a in p._actions if a.__class__.__name__ == "_SubParsersAction").choices["peer"]._subparsers._group_actions[0].choices["get"]
        leaf.add_argument("--ghost")
        return p

    monkeypatch.setattr("peerhub.cli.app.build_parser", extended)
    assert inv.derive() != real and inv.main(["--check"]) == 1  # an extra parser option makes the committed-file check fail
    monkeypatch.undo()
    assert inv.derive() == real and inv.main(["--check"]) == 0


# ------------------------------------------------------------------ helpers
def run(capsys, db, *args):
    try:
        code = main(["--db", str(db), *args])
    except SystemExit as e:  # argparse
        code = e.code
    cap = capsys.readouterr()
    return code, cap.out, cap.err


@pytest.fixture
def env(tmp_path, capsys):
    class E:
        pass

    e = E()
    e.tmp, e.n = tmp_path, 0

    def fresh(populate=True):
        e.n += 1
        db = tmp_path / f"db{e.n}" / "m1.db"
        db.parent.mkdir()
        if populate:
            for a in (("peer", "register", "--peer", "a"), ("peer", "register", "--peer", "b"), ("stream", "create", "--stream", "s", "--members", "a", "b")):
                assert run(capsys, db, *a)[0] == 0
            for i in range(3):
                assert run(capsys, db, "record", "append", "--stream", "s", "--author-peer", "a", "--kind", "message", "--body", json.dumps(f"m{i}"),
                           "--idempotency-key", f"k{i}", "--created-at", CT)[0] == 0
        return db

    def j(db, *a, code=0):
        c, out, err = run(capsys, db, *a)
        assert c == code, (a, c, err[-300:])
        return json.loads(out) if out.strip() else None

    e.fresh, e.j, e.run = fresh, j, lambda db, *a: run(capsys, db, *a)
    return e


# ------------------------------------------------------------------ gate item 3: every published option has a behavioural proof of effect
APPEND = ("record", "append", "--stream", "s", "--author-peer", "a", "--kind", "message", "--body", '"m"', "--idempotency-key", "kk", "--created-at", CT)


def _swap(args, flag, value):
    a = list(args)
    a[a.index(flag) + 1] = value
    return a


def _quota_world(e):
    from tests.communication.e2e.test_cli_diag_quota import seed
    from tests.communication.harness.observation import ObservationHarness

    h = ObservationHarness(e.tmp / f"qw{e.n}")
    e.n += 1
    seed(h.db_path, pools=[("P", "QUOTA")], rows=[("peer:a", "quota", "P", "MEASURED", 5, {"remaining": 1}, "s"), ("peer:b", "rate_limit", None, "MEASURED", 5, {"used": 2}, "s")])
    return h.db_path


def x_db(e):
    d1, d2 = e.fresh(), e.fresh(populate=False)
    assert e.j(d1, "peer", "get", "--peer", "a")["peer_id"] == "a"
    assert e.run(d2, "peer", "get", "--peer", "a")[0] == 1  # same command, other --db: different outcome


def reg_id(e):
    db = e.fresh(populate=False)
    assert e.j(db, "peer", "register", "--peer", "zed")["peer_id"] == "zed" and e.j(db, "peer", "get", "--peer", "zed")["peer_id"] == "zed"


def reg_name(e):
    assert e.j(e.fresh(False), "peer", "register", "--peer", "p", "--name", "Nom")["display_name"] == "Nom"


def reg_adapter(e):
    assert e.j(e.fresh(False), "peer", "register", "--peer", "p", "--adapter", "ad:1")["adapter_ref"] == "ad:1"


def get_id(e):
    db = e.fresh()
    assert e.j(db, "peer", "get", "--peer", "b")["peer_id"] == "b" and e.run(db, "peer", "get", "--peer", "nobody")[0] == 1


def sc_id(e):
    assert e.j(e.fresh(), "stream", "create", "--stream", "s2")["stream_id"] == "s2"


def sc_title(e):
    assert e.j(e.fresh(), "stream", "create", "--stream", "s2", "--title", "Ttl")["title"] == "Ttl"


def sc_members(e):
    assert e.j(e.fresh(), "stream", "create", "--stream", "s2", "--members", "b", "a")["members"] == ["b", "a"]


def ss_id(e):
    db = e.fresh()
    assert e.j(db, "stream", "show", "--stream", "s")["stream_id"] == "s" and e.run(db, "stream", "show", "--stream", "nope")[0] == 1


def ra_stream(e):
    db = e.fresh()
    e.j(db, "stream", "create", "--stream", "s2", "--members", "a")
    assert e.j(db, *_swap(APPEND, "--stream", "s2"))["stream_id"] == "s2" and e.j(db, *APPEND)["stream_id"] == "s"


def ra_author(e):
    assert e.j(e.fresh(), *_swap(APPEND, "--author-peer", "b"))["author_peer_id"] == "b"


def ra_kind(e):
    assert e.j(e.fresh(), *_swap(APPEND, "--kind", "note"))["kind"] == "note"


def ra_body(e):
    assert e.j(e.fresh(), *_swap(APPEND, "--body", '{"q": 1}'))["body"] == {"q": 1}


def ra_idemp(e):
    db = e.fresh()
    first = e.j(db, *APPEND)
    assert e.j(db, *APPEND)["record_id"] == first["record_id"]  # same key: idempotent retry
    other = e.j(db, *_swap(APPEND, "--idempotency-key", "other"))
    assert other["record_id"] != first["record_id"] and other["position"] == first["position"] + 1  # other key: new record


def ra_created(e):
    assert e.j(e.fresh(), *_swap(APPEND, "--created-at", "2026-01-02T03:04:05Z"))["created_at"] == "2026-01-02T03:04:05Z"


def ra_targets(e):
    assert e.j(e.fresh(), *APPEND, "--targets", "b")["targets"] == ["b"]


def rr_stream(e):
    db = e.fresh()
    e.j(db, "stream", "create", "--stream", "s2", "--members", "a")
    assert len(e.j(db, "record", "read", "--stream", "s")) == 3 and e.j(db, "record", "read", "--stream", "s2") == []


def rr_after(e):
    assert [r["position"] for r in e.j(e.fresh(), "record", "read", "--stream", "s", "--after", "2")] == [3]


def rr_limit(e):
    assert [r["position"] for r in e.j(e.fresh(), "record", "read", "--stream", "s", "--limit", "2")] == [1, 2]


def og_peer(e):
    db = e.fresh()
    e.j(db, "offset", "advance", "--peer", "b", "--stream", "s", "--position", "2", "--revision", "1")
    assert e.j(db, "offset", "get", "--peer", "b", "--stream", "s")["read_through_position"] == 2 and e.j(db, "offset", "get", "--peer", "a", "--stream", "s")["read_through_position"] == 0


def og_stream(e):
    db = e.fresh()
    e.j(db, "stream", "create", "--stream", "s2", "--members", "a")
    e.j(db, "offset", "advance", "--peer", "a", "--stream", "s", "--position", "1", "--revision", "1")
    assert e.j(db, "offset", "get", "--peer", "a", "--stream", "s")["read_through_position"] == 1 and e.j(db, "offset", "get", "--peer", "a", "--stream", "s2")["read_through_position"] == 0


def oa_peer(e):
    db = e.fresh()
    e.j(db, "offset", "advance", "--peer", "a", "--stream", "s", "--position", "1", "--revision", "1")
    assert e.j(db, "offset", "get", "--peer", "b", "--stream", "s")["read_through_position"] == 0


def oa_stream(e):
    db = e.fresh()
    e.j(db, "stream", "create", "--stream", "s2", "--members", "a")
    e.j(db, "offset", "advance", "--peer", "a", "--stream", "s2", "--position", "0", "--revision", "1")
    assert e.j(db, "offset", "get", "--peer", "a", "--stream", "s")["revision"] == 1


def oa_position(e):
    assert e.j(e.fresh(), "offset", "advance", "--peer", "a", "--stream", "s", "--position", "3", "--revision", "1")["read_through_position"] == 3


def oa_revision(e):
    db = e.fresh()
    assert e.run(db, "offset", "advance", "--peer", "a", "--stream", "s", "--position", "1", "--revision", "9")[0] == 3  # CAS lost
    assert e.j(db, "offset", "advance", "--peer", "a", "--stream", "s", "--position", "1", "--revision", "1")["revision"] == 2


def dh_stream(e):
    db = e.fresh()
    assert e.j(db, "diag", "health", "--stream", "s")["record_count"] == 3 and e.j(db, "diag", "health", "--stream", "zz")["status"] == "NOT_FOUND"


def dh_obsdb(e):
    db, q = e.fresh(), _quota_world(e)
    own = e.j(db, "diag", "health", "--stream", "s")["observations"]
    other = e.j(db, "diag", "health", "--stream", "s", "--observation-db", str(q))["observations"]
    assert own["source_db"] == str(db) and own["status"] == "UNAVAILABLE"
    assert other["source_db"] == str(q) and other["latest_total"] == 2 and other["status"] == "OK"


def dq_obsdb(e):
    db, q = e.fresh(), _quota_world(e)
    assert e.j(db, "diag", "quota", "--json", code=5)["status"] == "UNAVAILABLE"
    assert e.j(db, "diag", "quota", "--json", "--observation-db", str(q))["status"] == "OK"


def dq_peer(e):
    q = _quota_world(e)
    items = [i["subject_ref"] for g in e.j(q, "diag", "quota", "--json", "--peer", "peer:b")["pools"] for i in g["items"]]
    assert items == ["peer:b"]


def dq_pool(e):
    q = _quota_world(e)
    items = [i["subject_ref"] for g in e.j(q, "diag", "quota", "--json", "--pool", "P")["pools"] for i in g["items"]]
    assert items == ["peer:a"]


def dq_json(e):
    q = _quota_world(e)
    assert e.j(q, "diag", "quota", "--json")["schema_version"] == "1.0"
    assert e.run(q, "diag", "quota")[1].lstrip().startswith("quota/rate-limit evidence")  # not JSON without the flag


def _legacy(e):
    src = e.tmp / "legacy"
    src.mkdir(exist_ok=True)
    return make_legacy(src / f"legacy{e.n}.db")


def li_source(e, cmd="dry-run"):
    db, src = e.fresh(False), _legacy(e)
    assert e.j(db, "legacy-import", cmd, "--source", str(src))["totals"]["imported_units"] >= 0 if cmd == "dry-run" else True
    code, _, err = e.run(db, "legacy-import", cmd, "--source", str(e.tmp / "missing.db"))
    assert code == 7 and "LEGACY IMPORT REFUSED" in err  # a different source changes the outcome


def li_apply_source(e):
    li_source(e, "apply")


def li_digest(e):
    db, src = e.fresh(False), _legacy(e)
    plan = e.j(db, "legacy-import", "dry-run", "--source", str(src))["plan_digest"]
    code, _, err = e.run(db, "legacy-import", "apply", "--source", str(src), "--plan-digest", "0" * len(plan))
    assert code == 7 and "refused" in err.lower()
    assert e.j(db, "legacy-import", "apply", "--source", str(src), "--plan-digest", plan)["totals"]["imported_units"] > 0


EFFECT = {
    "* --db": x_db,
    "peer register --peer": reg_id, "peer register --name": reg_name, "peer register --adapter": reg_adapter, "peer get --peer": get_id,
    "stream create --stream": sc_id, "stream create --title": sc_title, "stream create --members": sc_members, "stream show --stream": ss_id,
    "record append --stream": ra_stream, "record append --author-peer": ra_author, "record append --kind": ra_kind, "record append --body": ra_body,
    "record append --idempotency-key": ra_idemp, "record append --created-at": ra_created, "record append --targets": ra_targets,
    "record read --stream": rr_stream, "record read --after": rr_after, "record read --limit": rr_limit,
    "offset get --peer": og_peer, "offset get --stream": og_stream, "offset advance --peer": oa_peer, "offset advance --stream": oa_stream,
    "offset advance --position": oa_position, "offset advance --revision": oa_revision,
    "diag health --stream": dh_stream, "diag health --observation-db": dh_obsdb,
    "diag quota --observation-db": dq_obsdb, "diag quota --peer": dq_peer, "diag quota --pool": dq_pool, "diag quota --json": dq_json,
    "legacy-import dry-run --source": li_source, "legacy-import apply --source": li_apply_source, "legacy-import apply --plan-digest": li_digest,
}

from tests.communication.e2e.cli_extension_effects import EFFECT as EXTENSION_EFFECT
EFFECT.update(EXTENSION_EFFECT)


def test_every_published_option_has_an_effect_proof():
    assert inv.option_keys(inv.derive()) == sorted(EFFECT)  # a new option/positional/alias at any level without a behavioural proof (or a stale proof) fails here


def test_option_key_walker_closes_the_registry_loopholes():
    import argparse

    p = argparse.ArgumentParser(prog="t")
    p.add_argument("--db")
    sp = p.add_subparsers(dest="c", required=True)
    g = sp.add_parser("grp")
    g.add_argument("--group-level")  # option on an intermediate parser
    leaf = g.add_subparsers(dest="a", required=True).add_parser("leaf")
    leaf.add_argument("--opt", "-o", "--opt-alias")  # secondary aliases
    leaf.add_argument("thing")  # positional
    keys = inv.option_keys(inv.derive(p))
    assert keys == sorted(["* --db", "grp --group-level", "grp leaf --opt", "grp leaf -o", "grp leaf --opt-alias", "grp leaf thing"])
    assert not (set(keys) - {"* --db"}) & set(EFFECT)  # every one would be reported as lacking a proof


@pytest.mark.parametrize("key", sorted(EFFECT))
def test_option_has_observable_effect(key, env):
    EFFECT[key](env)


# ------------------------------------------------------------------ gate item 5: help / exit / JSON contract
LEAVES = [c for c in inv.derive()["commands"]]


@pytest.mark.parametrize("leaf", LEAVES, ids=[c["command"] for c in LEAVES])
def test_leaf_help_exit_zero_lists_every_option_and_unknown_option_exits_2(leaf, capsys):
    argv = leaf["command"].split()
    code, out, err = run(capsys, "unused.db", *argv, "--help")
    assert code == 0 and out.startswith(("usage: peerhub", "usage: peerhub-m1")) and "Traceback" not in err
    for o in leaf["options"]:
        assert o["flags"][0] in out, (leaf["command"], o)
    if argv[0] == "diag":
        assert "example" in out.lower()  # public diag leaves carry copyable examples
    req = [x for o in leaf["options"] if o["required"] for x in (o["flags"][0], "1" if o["dest"] in ("new_position", "expected_revision") else "x")]
    code, out, err = run(capsys, "unused.db", *argv, *req, "--definitely-unknown")
    assert code == 2 and "Traceback" not in err and "unrecognized arguments: --definitely-unknown" in err


def test_root_help_and_bad_invocations(capsys):
    code, out, err = run(capsys, "x.db", "--help")
    assert code == 0 and all(w in out for w in ("peer", "stream", "record", "offset", "diag", "legacy-import"))
    code, out, err = run(capsys, "x.db")
    assert code == 2 and "required" in err and "Traceback" not in err
    code, out, err = run(capsys, "x.db", "diag", "nonsense")
    assert code == 2 and "invalid choice" in err
    r = subprocess.run([sys.executable, "-m", "peerhub.cli.app", "peer", "--nope"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "Traceback" not in r.stderr  # real process, not only in-process


def test_json_outputs_parse_with_stable_keys(env):
    db = env.fresh()
    assert set(env.j(db, "peer", "get", "--peer", "a")) == {"schema_version", "peer_id", "display_name", "adapter_ref", "metadata", "created_at"}
    assert set(env.j(db, "stream", "show", "--stream", "s")) == {"schema_version", "stream_id", "title", "state", "members", "revision", "metadata", "created_at"}
    rec = env.j(db, "record", "read", "--stream", "s")[0]
    assert set(rec) == {"schema_version", "record_id", "stream_id", "position", "author_peer_id", "kind", "body", "targets", "reply_to", "refs", "metadata",
                        "idempotency_key", "payload_digest", "created_at", "appended_at"}
    assert set(env.j(db, "offset", "get", "--peer", "a", "--stream", "s")) == {"schema_version", "peer_id", "stream_id", "read_through_position", "revision"}
    assert set(env.j(db, "diag", "health", "--stream", "s")) == {"status", "stream_id", "title", "state", "revision", "head_position", "record_count", "members",
                                                              "offsets", "observations"}
    assert set(env.j(db, "diag", "health", "--stream", "s")["observations"]) == {"source_db", "status", "latest_total", "by_state", "error"}
    assert set(env.j(db, "diag", "quota", "--json", code=5)) == {"schema_version", "status", "source_db", "read_at", "filters", "overall", "pools", "reset_credits", "error"}


def test_documented_exit_codes_are_the_ones_returned(env):
    db = env.fresh()
    assert env.run(db, "peer", "get", "--peer", "nobody")[0] == 1
    assert env.run(db, *APPEND)[0] == 0
    assert env.run(db, *_swap(APPEND, "--body", '"different"'))[0] == 2  # idempotency conflict
    assert env.run(db, "offset", "advance", "--peer", "a", "--stream", "s", "--position", "1", "--revision", "9")[0] == 3
    assert Path(db).exists()
