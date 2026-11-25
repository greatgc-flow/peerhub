"""Wiring of the API peers ca, oa (#378) and ga (#381): kinds, profiles, guide, batch, quota and usage contracts. No
provider is called."""
import ast
import json
import re
import tomllib
from pathlib import Path

import pytest

from peerhub.extensions.adapters import api
from peerhub.extensions.adapters.api import ApiRuntimeTarget, CaRuntimeTarget, GaRuntimeTarget, OaRuntimeTarget
from peerhub.core.peer_kinds import API_PEER_KINDS as API_KINDS
from peerhub.core.peer_kinds import BUILTIN_PEER_CHOICES, BUILTIN_PEER_KINDS, BUILTIN_PEER_LABELS

REPO = Path(__file__).resolve().parents[3]
TOML = tomllib.loads((REPO / "peerhub" / "config_data" / "model-defaults.toml").read_text(encoding="utf-8"))
API_PROFILES = {pid: p for pid, p in TOML["profiles"].items() if pid.split(".", 1)[0] in API_KINDS}


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "config"))


def test_the_api_peers_are_builtin_peer_kinds_without_other_aliases():
    from peerhub.core.peer_kinds import ALIASES

    assert all(ALIASES[kind] == kind for kind in API_KINDS)
    assert sorted(alias for alias, kind in ALIASES.items() if kind in API_KINDS) == sorted(API_KINDS)
    assert api.API_TARGETS == {"ca": CaRuntimeTarget, "oa": OaRuntimeTarget, "ga": GaRuntimeTarget}
    assert tuple(api.API_TARGETS) == API_KINDS


def _kind_lists(tree: ast.AST):
    """Every string collection of a module: (line, strings) of each tuple, list, set and dict (its keys), at any depth."""
    for node in ast.walk(tree):
        items = node.keys if isinstance(node, ast.Dict) else node.elts if isinstance(node, (ast.Tuple, ast.List, ast.Set)) else []
        strings = {item.value for item in items if isinstance(item, ast.Constant) and isinstance(item.value, str)}
        if strings:
            yield node.lineno, strings


def test_every_list_that_names_ca_and_oa_names_ga():
    """Design 8 (#381): a kind list that knows `ca` and `oa` must know `ga`. Walks the package, the live gate and the
    packaged texts two ways, so a missing entry fails here and not in a store or in a message:

    1. every Python collection (tuple, list, set, dict keys) that holds "ca" and "oa" holds "ga";
    2. every line that names `ca` and `oa` (a regular expression, an SQL list, a message, a comment) names `ga`."""
    word = {kind: re.compile(rf"(?<![A-Za-z0-9_.-]){kind}(?![A-Za-z0-9_-])") for kind in API_KINDS}
    python = [*sorted((REPO / "peerhub").rglob("*.py")), REPO / "tools" / "live_gate.py"]
    texts = [*sorted((REPO / "peerhub" / "config_data").glob("*.toml")), REPO / "pyproject.toml"]
    assert len(python) > 50 and len(texts) >= 3  # the walk really sees the package
    missing: list[str] = []
    seen = 0
    for path in python:
        source = path.read_text(encoding="utf-8")
        for line, strings in _kind_lists(ast.parse(source)):
            if {"ca", "oa"} <= strings:
                seen += 1
                if "ga" not in strings:
                    missing.append(f"{path.relative_to(REPO)}:{line}: a collection with ca and oa has no ga")
    for path in (*python, *texts):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if word["ca"].search(line) and word["oa"].search(line):
                seen += 1
                if not word["ga"].search(line):
                    missing.append(f"{path.relative_to(REPO)}:{number}: a line with ca and oa has no ga")
    assert not missing, "\n".join(missing)
    assert seen >= 30  # the files of design 8 are among them (the next assertions name each one)
    from peerhub.cli import recovery
    from peerhub.extensions import batch, bridge, peer_kinds, session_receipts, usage_normalization

    usage_contract = usage_normalization.PROVIDER_USAGE_CONTRACT
    assert set(usage_contract) == set(BUILTIN_PEER_KINDS)
    assert usage_contract["ga"] == ("delivery", "ga.response.usage")
    for kind in BUILTIN_PEER_KINDS:
        match = session_receipts._ADAPTER_VERSION.fullmatch(f"adapter:{kind}:v12")  # pyright: ignore[reportPrivateUsage]
        assert match and match.groups() == (kind, "12")
        assert recovery._PROFILE_ID.fullmatch(f"{kind}.example")  # pyright: ignore[reportPrivateUsage]
    api_kinds = ", ".join(json.dumps(kind) for kind in API_KINDS)
    bridge_source = Path(bridge.__file__).read_text(encoding="utf-8")
    assert f"runtime_kind\"] in ({api_kinds})" in bridge_source
    assert api_kinds in Path(batch.__file__).read_text(encoding="utf-8")
    assert peer_kinds.ALIASES["ga"] == "ga" and usage_normalization.REPORTED_FIELDS["ga"] == {"input_tokens", "output_tokens"}
    for name in ("cli/app.py", "cli/recovery.py", "extensions/ask.py", "config_data/lessons.toml"):
        text = (REPO / "peerhub" / name).read_text(encoding="utf-8")
        assert word["ga"].search(text), name


def test_the_output_limit_is_per_class_in_every_text_that_names_it():
    """Design 3 (#381): 16,000 for ca and oa, 32,000 for ga, in the request, the messages and the recovery texts."""
    from peerhub.cli.lessons import load_lessons
    from peerhub.cli.recovery import REGISTRY, RESULT_STATUS_REGISTRY

    limits = {kind: cls.max_output_tokens for kind, cls in api.API_TARGETS.items()}
    assert limits == {"ca": 16_000, "oa": 16_000, "ga": 32_000}
    named = "16,000 tokens for ca and oa; 32,000 for ga"
    assert named in REGISTRY["truncated"].meaning and named in RESULT_STATUS_REGISTRY["truncated"].meaning
    lesson = next(item for item in load_lessons() if item["id"] == "api-peers")
    assert "16,000 output tokens for ca and oa and at 32,000 for ga" in lesson["fix"]
    for pid, profile in API_PROFILES.items():
        assert f"{limits[pid.split('.', 1)[0]]:,}" in " ".join(profile["traits"]), pid
    for kind, cls in api.API_TARGETS.items():
        target = cls(REPO, model={"ca": "claude-x", "oa": "gpt-x", "ga": "gemini-x"}[kind])
        cut = target._reply("", None, complete=False, truncated=True, why="why")  # pyright: ignore[reportPrivateUsage]
        assert cut.detail == f"{kind} reply cut at {limits[kind]} output tokens (why)"
    for path in ("README.md", "docs/adapters/README.md", "docs/guidance-reference.md", "docs/lessons.md"):
        text = (REPO / path).read_text(encoding="utf-8")
        assert "32,000" in text and "16,000" in text, path


def test_the_ga_provider_limits_are_named_where_the_error_table_is_documented():
    """#384: 402 and 429 with RESOURCE_EXHAUSTED are the provider limits of ga, in the code, the table, the README and the lesson."""
    from peerhub.cli.lessons import load_lessons

    assert api.GA_LIMITS == ((402, "RESOURCE_EXHAUSTED"), (429, "RESOURCE_EXHAUSTED"))
    table = (REPO / "docs" / "adapters" / "README.md").read_text(encoding="utf-8")
    assert "| 402 or 429 | `RESOURCE_EXHAUSTED` | `not_executed`, `quota_exhausted`, no reset time, the message kept |" in table
    assert "| 429 | `RESOURCE_EXHAUSTED` |" not in table
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "HTTP 402 or 429 answer with the status `RESOURCE_EXHAUSTED`" in readme
    for text in (readme, " ".join(table.split())):  # the adapter document wraps its lines
        assert "04:24Z" in text and "Your prepayment credits are depleted" in text and "Google AI Studio" in text
    lesson = next(item for item in load_lessons() if item["id"] == "api-peers")
    assert "HTTP 402 or 429 with RESOURCE_EXHAUSTED is a provider limit" in lesson["fix"] and "#384" in lesson["issue"]


@pytest.mark.parametrize("kind,cls", [("ca", CaRuntimeTarget), ("oa", OaRuntimeTarget), ("ga", GaRuntimeTarget)])
def test_targets_share_one_base_and_are_stateless(tmp_path, kind, cls):
    target = cls(tmp_path, profile=f"{kind}.standard")
    assert isinstance(target, ApiRuntimeTarget) and target.runtime_kind == kind
    assert target.stateless is True and target.resumable is False
    assert target.resume_session("any") == "unsupported"
    assert target.fingerprint() == f"adapter:{kind}:v1"
    assert target.create_session().startswith(f"{kind}-local-")
    assert target.session_binding() == {"profile": f"{kind}.standard", "model": API_PROFILES[f"{kind}.standard"]["model"],
                                        "effort": None, "writable": False}
    assert not (target.supports_interrupt or target.supports_terminate or target.supports_steer)


def test_the_shared_default_stream_stays_for_the_cli_peers(tmp_path):
    import sys

    from peerhub.extensions.adapters import CliRuntimeTarget
    from peerhub.extensions.ask import ask
    from tests.communication.adapters.conftest import FAKE

    runtime = CliRuntimeTarget("cx", tmp_path, command=[sys.executable, FAKE], env_extra={"FAKE_KIND": "cx"}, timeout_s=120)
    result = ask(tmp_path / "core.db", "cx", "question", request_id="r1", runtime=runtime)
    assert (result["status"], result["stream_id"]) == ("delivered", "peer:cx:chat")  # only a stateless peer gets a stream per ask


def test_one_rule_names_the_default_stream():
    from peerhub.extensions.ask import default_stream

    assert default_stream("cx", "r1", stateless=False) == default_stream("cx", None, stateless=False) == "peer:cx:chat"
    assert default_stream("ca", "r1", stateless=True) == "peer:ca:r1"
    assert default_stream("ca", None, stateless=True) is None  # named after a request id that only the ask generates


@pytest.mark.parametrize("peer,flags,stream", [
    ("ca", ["--request-id", "r1"], "peer:ca:r1"),
    ("oa", ["--request-id", "r1"], "peer:oa:r1"),
    ("ga", ["--request-id", "r1"], "peer:ga:r1"),
    ("ga", ["--request-id", "r1", "--stream", "named"], "named"),
    ("ga", [], None),
    ("voter", ["--request-id", "r1"], "peer:voter:r1"),  # a custom peer registered with --adapter ca
    ("judge", ["--request-id", "r1"], "peer:judge:r1"),  # a custom peer registered with --adapter ga
    ("ca", ["--request-id", "r1", "--stream", "named"], "named"),
    ("ca", [], None),  # no request id known: no stream is named
    ("cx", ["--request-id", "r1"], "peer:cx:chat"),
    ("reviewer", ["--request-id", "r1"], "peer:reviewer:chat"),  # a custom peer registered with --adapter codex
])
def test_exception_guidance_names_the_stream_the_ask_really_used(tmp_path, monkeypatch, capsys, peer, flags, stream):
    from peerhub.cli.app import main
    from peerhub.core.models import Peer
    from peerhub.core.store import CoreStore
    from peerhub.extensions import ask as ask_module
    from peerhub.extensions.bridge_claims import ClaimHeldError

    db = tmp_path / "core.db"
    store = CoreStore(db)
    store.register_peer(Peer(peer_id="voter", adapter_ref="ca"))
    store.register_peer(Peer(peer_id="judge", adapter_ref="ga"))
    store.register_peer(Peer(peer_id="reviewer", adapter_ref="codex"))

    def held(*args, **kwargs):
        raise ClaimHeldError("claim held by another ask")

    monkeypatch.setattr(ask_module, "ask", held)
    assert main(["--db", str(db), "ask", peer, "question", "--json", *flags]) == 1
    guidance = json.loads(capsys.readouterr().out)["guidance"]
    assert guidance["code"] == "ask_claim_held"
    argv = guidance["recover"][0]["argv"]
    named = argv[argv.index("--stream") + 1]
    assert named == (stream or "STREAM_ID")  # the placeholder stays when the stream is not known
    assert f"peer:{peer}:chat" not in argv or stream == f"peer:{peer}:chat"


def test_the_registered_adapter_comes_before_the_peer_name_as_in_ask(tmp_path, monkeypatch, capsys):
    from peerhub.cli.app import main
    from peerhub.core.models import Peer
    from peerhub.core.store import CoreStore
    from peerhub.extensions import ask as ask_module
    from peerhub.extensions.bridge_claims import ClaimHeldError
    from peerhub.core.peer_kinds import peer_kind

    assert peer_kind("codex", "ca") == "cx" and peer_kind("ca", "cx") == "ca"  # the one rule `ask` uses
    assert peer_kind(None, "oa") == "oa" and peer_kind(None, "nobody") is None and peer_kind("no-such-adapter", "ca") is None
    assert peer_kind(None, "ga") == "ga" and peer_kind("ga", "cx") == "ga" and peer_kind("agy", "ga") == "ag"
    db = tmp_path / "core.db"
    store = CoreStore(db)
    store.register_peer(Peer(peer_id="ca", adapter_ref="codex"))  # a builtin name registered with another adapter
    store.register_peer(Peer(peer_id="cx", adapter_ref="oa"))
    store.register_peer(Peer(peer_id="ga", adapter_ref="claude"))
    store.register_peer(Peer(peer_id="cc", adapter_ref="ga"))

    def held(*args, **kwargs):
        raise ClaimHeldError("claim held by another ask")

    monkeypatch.setattr(ask_module, "ask", held)
    for peer, stream in (("ca", "peer:ca:chat"), ("cx", "peer:cx:r1"), ("ga", "peer:ga:chat"), ("cc", "peer:cc:r1")):
        assert main(["--db", str(db), "ask", peer, "question", "--json", "--request-id", "r1"]) == 1
        argv = json.loads(capsys.readouterr().out)["guidance"]["recover"][0]["argv"]
        assert argv[argv.index("--stream") + 1] == stream


@pytest.mark.parametrize("store", ["garbage", "directory", "missing"])
def test_a_store_problem_on_an_ask_to_a_custom_peer_ends_as_without_the_peer_lookup(tmp_path, monkeypatch, capsys, store):
    """The error path reads the store to name the stream; that read must never replace the error being reported."""
    import peerhub.cli.app as app

    db = tmp_path / "core.db"
    if store == "garbage":
        db.write_bytes(b"this is not a database file\n" * 200)
    elif store == "directory":
        db.mkdir()
    argv = ["--db", str(db), "ask", "voter", "question", "--json", "--request-id", "r1"]
    code = app.main(argv)  # no exception escapes (before the fix: sqlite3.DatabaseError, a traceback)
    payload = json.loads(capsys.readouterr().out)
    with monkeypatch.context() as patch:  # what the same command did before the error path read the store
        patch.setattr(app, "_peer_kind", lambda db, peer: None)
        before = app.main(argv)
        assert (code, payload["error"]) == (before, json.loads(capsys.readouterr().out)["error"])
    if store == "garbage":
        assert code == 4 and payload["error"]["code"] == "storage_corrupt" and payload["guidance"]["code"]


@pytest.mark.parametrize("failure", [__import__("sqlite3").DatabaseError("database disk image is malformed"),
                                     __import__("sqlite3").OperationalError("database is locked"), OSError("gone"), RuntimeError("x")])
def test_a_failed_peer_lookup_names_no_stream_and_keeps_the_reported_error(tmp_path, monkeypatch, capsys, failure):
    import peerhub.cli.app as app
    from peerhub.extensions import ask as ask_module
    from peerhub.extensions.bridge_claims import ClaimHeldError

    def held(*args, **kwargs):
        raise ClaimHeldError("claim held by another ask")

    def lookup(db, peer):
        raise failure

    monkeypatch.setattr(ask_module, "ask", held)
    monkeypatch.setattr(app, "_peer_kind", lookup)
    assert app.main(["--db", str(tmp_path / "core.db"), "ask", "voter", "question", "--json", "--request-id", "r1"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["guidance"]["code"] == "ask_claim_held"
    argv = payload["guidance"]["recover"][0]["argv"]
    assert argv[argv.index("--stream") + 1] == "STREAM_ID"  # the adapter is not known, so no stream is named


def test_packaged_api_profiles_have_their_tiers_a_card_and_no_pool():
    """ca and oa have three tiers; ga has no deep profile (#381: gemini-4-hydrogen gets no profile yet, and
    gemini-4-argon had no quota for the measured key), so a stronger Gemini model goes by --model."""
    tiers = {kind: cls.profile_tiers for kind, cls in api.API_TARGETS.items()}  # pyright: ignore[reportAttributeAccessIssue]
    assert tiers == {"ca": ("standard", "effort", "deepthink"), "oa": ("standard", "effort", "deepthink"), "ga": ("standard", "effort")}
    assert sorted(API_PROFILES) == sorted(f"{kind}.{tier}" for kind in API_KINDS for tier in tiers[kind])
    for kind, cls in api.API_TARGETS.items():
        named = ", ".join(f"{kind}.{tier}" for tier in tiers[kind])
        with pytest.raises(api.RequestRejected) as missing:
            cls(REPO)
        assert str(missing.value) == f"{kind} needs --profile ({named}) or --model"
    models = {"ca": api.CA_MODEL, "oa": api.OA_MODEL, "ga": api.GA_MODEL}
    for pid, profile in API_PROFILES.items():
        kind = pid.split(".", 1)[0]
        assert profile["selection_mode"] == "pinned" and "reasoning_effort" not in profile, pid
        assert models[kind].fullmatch(profile["model"]), pid
        assert "pools" not in profile and "pools_source" not in profile, pid
        assert f"inline the text; {kind} reads no file" in profile["use_for"], pid
        assert profile["evidence"] == (["#381"] if kind == "ga" else ["#378"]), pid
    assert {pid: API_PROFILES[pid]["model"] for pid in API_PROFILES if pid.startswith("ga.")} == {
        "ga.standard": "gemini-3.5-flash-lite", "ga.effort": "gemini-3.6-flash"}  # 3.6: it answered 4 of 4 calls (#381)
    effort = API_PROFILES["ga.effort"]
    assert (effort["checked"], effort["card_checked"]) == ("2026-11-16", "2026-11-16")
    assert "4 of 4" in " ".join(effort["traits"]) and "no API benchmark" in " ".join(effort["traits"])  # no capability claim
    assert not any(kind in TOML["observed_versions"] for kind in API_KINDS)  # no CLI version drift check
    from peerhub.cli.recovery import REGISTRY

    prereqs = REGISTRY["request_rejected"].recover[0]["prereqs"]
    assert set(re.findall(r"\b[a-z]{2}\.[a-z]+\b", prereqs)) == set(API_PROFILES)  # the recovery text names the packaged ones only


def test_profile_list_and_explain_show_the_api_profiles(tmp_path, monkeypatch, capsys):
    from peerhub.cli.app import main
    from peerhub.cli.recovery import run_explain_cli

    assert main(["profile", "list", "--workspace", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert all(f"inline the text; {kind} reads no file" in out for kind in API_KINDS)
    monkeypatch.chdir(tmp_path)
    for pid in ("ca.standard", "oa.deepthink", "ga.standard", "ga.effort"):
        assert run_explain_cli(pid) == 0
        assert capsys.readouterr().out.startswith(f"Profile: {pid}")
    assert run_explain_cli("ga.deepthink") == 1  # not packaged
    assert "unknown profile" in capsys.readouterr().err


def test_guide_names_the_api_peers_and_points_to_their_topic(capsys):
    """The guide names API peers and points to their lesson and the batch resource rule."""
    from peerhub.cli.app import main
    from peerhub.cli.guide import GUIDE_MAX_BYTES
    from peerhub.cli.lessons import lesson_by_id

    assert main(["guide"]) == 0
    out = capsys.readouterr().out
    assert "ca/oa/ga = API credits, text only" in out and "api-peers" in out
    assert len(out.replace("\r\n", "\n").encode("utf-8")) <= GUIDE_MAX_BYTES
    api_topic = " ".join(lesson_by_id("api-peers")[0]["fix"].split())
    assert "ca, oa and ga read no file" in api_topic and "ga (Gemini API; the key only in the environment variable GEMINI_API_KEY)" in api_topic
    load_topic = " ".join(lesson_by_id("batch-and-load")[0]["fix"].split())
    assert "PeerHub does not cap the count" in load_topic
    assert "RAM is low or CPU load is high" in load_topic


def test_suggest_never_names_an_api_peer(tmp_path):
    from peerhub.cli.monitor_view import _compact_suggestions  # pyright: ignore[reportPrivateUsage]
    from peerhub.extensions.adapters.profiles import list_profiles

    profiles = list_profiles(tmp_path)
    assert {"ca.standard", "oa.standard", "ga.standard"} <= {p["profile"] for p in profiles}
    rows = [{"peer": peer, "pool": pool, "aged": False, "state": "ok", "remaining": 0.9, "reset_seconds": 3600.0}
            for p in profiles for peer in [p["profile"].split(".", 1)[0]] for pool in (p["fields"]["pools"]["value"] or [])]
    text = _compact_suggestions(profiles, rows)
    assert text.startswith("suggest: eco=") and not any(f"{kind}." in text for kind in API_KINDS)


def test_cached_readiness_lists_no_api_profile(tmp_path):
    from peerhub.extensions.diag_readiness import configured_profiles

    ids = {profile["id"] for profile in configured_profiles(tmp_path)}
    assert "cx.standard" in ids and not any(pid.split(".", 1)[0] in API_KINDS for pid in ids)  # readiness is pool evidence


def test_quota_refresh_has_no_probe_for_an_api_peer(tmp_path):
    from peerhub.core.store import CoreStore
    from peerhub.extensions.quota_capture import refresh_quota

    db = tmp_path / "core.db"
    CoreStore(db)
    for peer in API_KINDS:
        with pytest.raises(ValueError, match="quota providers: cx/codex, cc/claude, ag/agy"):
            refresh_quota(db, [peer], pollers={})


def test_batch_accepts_api_peers_refuses_writable_and_classifies_incomplete_replies(tmp_path):
    from peerhub.extensions import batch
    from peerhub.core.peer_kinds import ALIASES

    task = batch.Task("t1", "ca", "question", None, "ca.standard", False, 60.0)
    batch.validate_tasks([task, batch.Task("t2", "oa", "question", None, None, False, 60.0),
                          batch.Task("t4", "ga", "question", None, "ga.standard", False, 60.0)], kind_of=ALIASES.get)
    for kind in API_KINDS:
        with pytest.raises(batch.ManifestError, match=f"t3: {kind} has no workspace access"):
            batch.validate_tasks([batch.Task("t3", kind, "question", None, None, True, 60.0)], kind_of=ALIASES.get)
    for status in ("truncated", "incomplete"):
        out = json.dumps({"status": status, "certainty": "STARTED", "response": None, "error": {"code": status, "message": "m"}})
        assert batch.classify_worker(1, out) == ("FAILED", status)
    rejected = json.dumps({"status": "error", "response": None, "error": {"code": "request_rejected", "message": "m"}})
    assert batch.classify_worker(2, rejected) == ("FAILED", "request_rejected")


def test_usage_contract_knows_the_api_peers():
    from peerhub.extensions.usage_normalization import normalize_provider_usage, validate_provider_usage

    assert normalize_provider_usage("ca", {"input_tokens": 5, "output_tokens": 2}) == {"provider_input_tokens": 5, "output_tokens": 2}
    assert normalize_provider_usage("oa", {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}) == {
        "provider_input_tokens": 5, "output_tokens": 2, "provider_total_tokens": 7}
    assert normalize_provider_usage("ga", {"input_tokens": 5, "output_tokens": 2}) == {"provider_input_tokens": 5, "output_tokens": 2}
    for kind, source in (("ca", "ca.message.usage"), ("oa", "oa.response.usage"), ("ga", "ga.response.usage")):
        reported = {"input_tokens": 5, "output_tokens": 2}
        candidate = {"schema_version": 1, "scope": "delivery", "source": source, "reported": reported,
                     "normalized": normalize_provider_usage(kind, reported), "coverage": {"state": "MEASURED", "reason": None}}
        checked = validate_provider_usage(candidate, kind)
        assert checked["coverage"]["state"] == "MEASURED" and checked["attribution"] == "delivery"
        assert validate_provider_usage(dict(candidate, source="cc.result.usage"), kind)["coverage"]["reason"] == "provider_scope_mismatch"


def test_messages_name_the_api_peers(tmp_path, capsys):
    from peerhub.cli.app import build_parser
    from peerhub.extensions import batch
    from peerhub.extensions.ask import ask

    with pytest.raises(ValueError, match=f"--adapter {re.escape(BUILTIN_PEER_CHOICES)}"):
        ask(tmp_path / "core.db", "nobody", "question")
    with pytest.raises(batch.ManifestError, match=f"--adapter {re.escape(BUILTIN_PEER_CHOICES)}"):
        batch.validate_tasks([batch.Task("t1", "nobody", "question", None, None, False, 60.0)], kind_of=lambda peer: None)
    with pytest.raises(SystemExit):
        build_parser().parse_args(["ask", "--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    assert ", ".join(BUILTIN_PEER_LABELS) in help_text
    assert "ca, oa and ga: peer:<id>:<request-id>" in help_text


def test_live_gate_can_select_the_api_peers():
    from tools import live_gate

    canary = (REPO / "tests" / "communication" / "live" / "test_api_peer_canary.py").read_text(encoding="utf-8")
    assert "pytest.mark.canary" in canary and "pytest.mark.live" not in canary  # outside the release catalog and the G3 stages
    assert "OPENAI_API_KEY" in canary and "LIVE-OA-KEY-MISSING" in canary
    assert "GEMINI_API_KEY" in canary and "LIVE-GA-KEY-MISSING" in canary
    ga_profiles = sorted(pid for pid in API_PROFILES if pid.startswith("ga."))
    assert canary.count("@pytest.mark.ga\n") == 1 and "ga.deepthink" not in canary
    assert f'@pytest.mark.parametrize("profile", {json.dumps(sorted(ga_profiles, reverse=True))})' in canary  # every packaged ga profile
    parser_source = Path(live_gate.__file__).read_text(encoding="utf-8")
    choices = re.search(r'add_argument\("--peer", choices=(\([^)]+\))', parser_source)
    assert choices and set(ast.literal_eval(choices[1])) == set(BUILTIN_PEER_KINDS)
    markers = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]["markers"]
    assert all(kind in markers for kind in API_KINDS)
