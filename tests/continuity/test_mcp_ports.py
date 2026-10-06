"""Independent public-port integration and MCP boundary regressions."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from peerhub.core.models import Peer, Stream
from peerhub.core.store import CoreStore, IdempotencyConflictError
from peerhub.extensions.artifact import ArtifactStore
from peerhub.extensions.mcp import MCPInvalidParamsError, MCPServer, MCPSessionInvalidError
from peerhub.extensions.mcp import mask_secrets
from peerhub.extensions.skills import SkillCatalogEngine
from peerhub.extensions.work import WorkProjection


@pytest.fixture
def server(tmp_path: Path) -> MCPServer:
    store = CoreStore(tmp_path / "core.db")
    for peer in ("alice", "bob"):
        store.register_peer(Peer(peer_id=peer))
    store.create_stream(Stream(stream_id="main", members=["alice", "bob"]))
    store.create_stream(Stream(stream_id="other", members=["alice", "bob"]))
    return MCPServer(store, WorkProjection(tmp_path / "work.db", store),
                     ArtifactStore(tmp_path / "artifacts"),
                     SkillCatalogEngine(tmp_path / "skills.db", store))


def call(server: MCPServer, name: str, args: dict) -> dict:
    return server._call_tool({"name": name, "arguments": args})


def append_args() -> dict:
    return {"stream_id": "main", "author_peer_id": "alice", "kind": "example",
            "body": {"value": 1}, "session_id": "trusted", "idempotency_key": "request-1"}


def test_public_ports_and_wire_content(server: MCPServer):
    # MCP handlers must work without any raw/private Core connection API.
    def prohibited(*_args, **_kwargs):
        raise AssertionError("MCP bypassed public ports")
    server.core_store._read = prohibited
    response = json.loads(server.handle_message(json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "resources/list"})) or "{}")
    assert "peerhub://streams" in {r["uri"] for r in response["result"]["resources"]}
    # Restore internal implementation; read_records itself may use private Core internals.
    del server.core_store._read
    response = json.loads(server.handle_message(json.dumps({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "peerhub_get_stream_slice", "arguments": {"stream_id": "main"}}})) or "{}")
    result = response["result"]
    assert result["content"][0]["type"] == "text"
    assert json.loads(result["content"][0]["text"]) == {"records": []}
    assert result["isError"] is False


def test_durable_idempotency_survives_server_restart(server: MCPServer):
    server.register_session("trusted", {"author_peer_id": "alice", "allowed_streams": ["main"]})
    first = call(server, "peerhub_append_record", append_args())
    replacement = MCPServer(server.core_store)
    replacement.register_session("trusted", {"author_peer_id": "alice", "allowed_streams": ["main"]})
    assert call(replacement, "peerhub_append_record", append_args()) == first
    changed = append_args()
    changed["body"] = {"value": 2}
    with pytest.raises(IdempotencyConflictError):
        call(replacement, "peerhub_append_record", changed)
    assert replacement.core_store.stream_head("main") == 1


@pytest.mark.parametrize("mutation", [{"author_peer_id": "bob"}, {"stream_id": "other"}])
def test_session_capability_scope(server: MCPServer, mutation: dict):
    info = {"author_peer_id": "alice", "allowed_streams": ["main"]}
    server.register_session("trusted", info)
    info["allowed_streams"].append("other")
    with pytest.raises(MCPSessionInvalidError):
        call(server, "peerhub_append_record", {**append_args(), **mutation})
    assert server.core_store.stream_head("main") == 0
    assert server.core_store.stream_head("other") == 0


@pytest.mark.parametrize("args", [[], {"stream_id": 1}, {"stream_id": "main", "limit": True},
                                   {"stream_id": "main", "after_position": -1},
                                   {"stream_id": "main", "limit": 1001}])
def test_invalid_read_arguments(server: MCPServer, args):
    with pytest.raises(MCPInvalidParamsError):
        server._call_tool({"name": "peerhub_get_stream_slice", "arguments": args})


def test_artifact_port_digest_idempotency_and_metadata_resource(server: MCPServer):
    server.register_session("trusted", {})
    args = {"blob_content_b64": base64.b64encode(b"private binary content").decode(),
            "mime_type": "application/octet-stream", "session_id": "trusted"}
    first = call(server, "peerhub_store_artifact", args)
    assert call(server, "peerhub_store_artifact", args) == first
    assert server.artifact_store.read_bytes(first["digest"]) == b"private binary content"
    result = server._read_resource({"uri": "peerhub://artifact/" + first["digest"]})
    assert json.loads(result["contents"][0]["text"]) == {
        "digest": first["digest"], "size_bytes": len(b"private binary content")}
    assert "private binary content" not in json.dumps(result)
    with pytest.raises(MCPInvalidParamsError):
        call(server, "peerhub_store_artifact", {**args, "blob_content_b64": "!invalid"})
    server.revoke_session("trusted")
    with pytest.raises(MCPSessionInvalidError):
        call(server, "peerhub_store_artifact", args)


def test_optional_ports_are_advertised_only_when_configured(server: MCPServer):
    names = {tool["name"] for tool in server._list_tools()["tools"]}
    assert {"peerhub_get_work_tree", "peerhub_store_artifact", "peerhub_search_skills"} <= names
    assert call(server, "peerhub_get_work_tree", {}) == {"work": []}
    assert call(server, "peerhub_search_skills", {"query": "", "tags": []}) == {"skills": []}
    bare = MCPServer(server.core_store)
    assert len(bare._list_tools()["tools"]) == 2


def test_real_work_and_skill_projection_reads(server: MCPServer, tmp_path: Path):
    server.work_projection.create_work("main", "review", "Review change", {"secret_path": "hidden"},
                                       author_peer_id="alice")
    tree = call(server, "peerhub_get_work_tree", {"work_id": "review"})
    assert tree["work"][0]["title"] == "Review change"
    skill_dir = tmp_path / "source-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: review\ndescription: Audit changes\ntags: [quality]\n---\nReview carefully.\n",
        encoding="utf-8")
    server.skill_catalog.index_skill("main", skill_dir, author_peer_id="alice")
    skills = call(server, "peerhub_search_skills", {"query": "audit", "tags": ["quality"]})
    assert [item["skill_id"] for item in skills["skills"]] == ["review"]
    assert "path" not in skills["skills"][0]
    assert call(server, "peerhub_search_skills", {"query": "audit", "tags": ["missing"]}) == {"skills": []}


def test_protocol_invalid_utf8_params_null_id_and_secret_errors(server: MCPServer):
    assert json.loads(server.handle_message(b"\xff") or "{}")["error"]["code"] == -32700
    result = json.loads(server.handle_message(json.dumps({"jsonrpc": "2.0", "id": None,
        "method": "tools/list", "params": []})) or "{}")
    assert result["id"] is None and result["error"]["code"] == -32602
    class BrokenPort:
        def list_streams(self):
            raise RuntimeError("password=DO_NOT_LEAK C:/secret/credentials.json")
    result = MCPServer(BrokenPort()).handle_message(json.dumps({"jsonrpc": "2.0", "id": 3,
        "method": "resources/list"}))
    assert "DO_NOT_LEAK" not in (result or "")
    assert "credentials.json" not in (result or "")


def test_free_text_credentials_and_secret_paths_are_masked():
    text = "note sk-private123456789 Bearer ABC123 credentials at C:\\Users\\x\\.codex\\auth.json"
    masked = mask_secrets({"note": text, "token": "hidden", "authorization": "hidden"})
    serialized = json.dumps(masked)
    for secret in ("sk-private", "ABC123", "auth.json", "hidden"):
        assert secret not in serialized


def test_prompt_role_and_shutdown_do_not_touch_durable_core(server: MCPServer):
    result = server._get_prompt({"name": "peerhub_agent_context", "arguments": {"role": "Auditor", "task": "Inspect"}})
    assert result["messages"][0]["role"] == "user"
    server.register_session("trusted", {})
    server._dispatch_method("shutdown", {})
    assert not server.sessions
    assert server.core_store.get_stream("main") is not None
