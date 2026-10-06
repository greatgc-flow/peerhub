"""M2.4 MCP Integration Surface Test Suite (MCP-001..012).

Freeze Invariant 10: MCP never writes storage directly.
Core imports Extension = 0.
"""
from __future__ import annotations

import io
import json
import pytest
from pathlib import Path
from peerhub.core.models import Peer, Stream
from peerhub.core.store import CoreStore
from peerhub.extensions.mcp import (
    MCPServer,
    MCPSessionInvalidError,
    MCPDirectStorageAccessError,
    MCPProtocolError,
    MCPMethodNotFoundError,
    MCPInvalidParamsError,
)


@pytest.fixture
def core_store(tmp_path: Path) -> CoreStore:
    db_path = tmp_path / "core.db"
    store = CoreStore(db_path)
    store.register_peer(Peer(peer_id="peer:agent1", display_name="Agent 1"))
    store.create_stream(Stream(stream_id="events", members=["peer:agent1"]))
    store.create_stream(Stream(stream_id="stream-alpha", members=["peer:agent1"]))
    store.create_stream(Stream(stream_id="auth-log", members=["peer:agent1"]))
    store.create_stream(Stream(stream_id="post-mcp", members=["peer:agent1"]))
    return store


@pytest.fixture
def mcp_server(core_store: CoreStore) -> MCPServer:
    return MCPServer(core_store=core_store)


def test_mcp_001_invariant_10_storage_direct_write_blocked(mcp_server: MCPServer, tmp_path: Path):
    """MCP-001: Invariant 10 - direct storage write attempt from MCP layer is blocked."""
    unauthorized_db = tmp_path / "malicious.db"
    with pytest.raises(MCPDirectStorageAccessError):
        mcp_server.direct_storage_write(unauthorized_db, b"malicious content")


def test_mcp_002_jsonrpc_framing_and_initialize(mcp_server: MCPServer):
    """MCP-002: JSON-RPC 2.0 stdio message framing and initialize capability handshake."""
    req = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0.0"},
        },
    }
    resp_raw = mcp_server.handle_message(json.dumps(req))
    assert resp_raw is not None
    resp = json.loads(resp_raw)
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == "1"
    assert "result" in resp
    result = resp["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert "capabilities" in result
    assert "tools" in result["capabilities"]
    assert "resources" in result["capabilities"]
    assert "prompts" in result["capabilities"]
    assert result["serverInfo"]["name"] == "peerhub-mcp"


def test_mcp_003_tools_list_and_validation(mcp_server: MCPServer):
    """MCP-003: tools/list returns canonical schemas and tools/call validates parameters."""
    # List tools
    req_list = {"jsonrpc": "2.0", "id": "2", "method": "tools/list", "params": {}}
    resp_list = json.loads(mcp_server.handle_message(json.dumps(req_list)) or "{}")
    tools = resp_list["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert "peerhub_append_record" in tool_names
    assert "peerhub_get_stream_slice" in tool_names

    # Call unknown tool
    req_unknown = {
        "jsonrpc": "2.0",
        "id": "3",
        "method": "tools/call",
        "params": {"name": "non_existent_tool", "arguments": {}},
    }
    resp_unknown = json.loads(mcp_server.handle_message(json.dumps(req_unknown)) or "{}")
    assert "error" in resp_unknown
    assert resp_unknown["error"]["code"] == -32601

    # Call with missing required arguments
    req_invalid = {
        "jsonrpc": "2.0",
        "id": "4",
        "method": "tools/call",
        "params": {"name": "peerhub_append_record", "arguments": {"stream_id": "events"}},
    }
    resp_invalid = json.loads(mcp_server.handle_message(json.dumps(req_invalid)) or "{}")
    assert "error" in resp_invalid
    assert resp_invalid["error"]["code"] == -32602


def test_mcp_004_tools_call_append_record_via_core_port(mcp_server: MCPServer, core_store: CoreStore):
    """MCP-004: tools/call executes peerhub_append_record and returns valid offset via CoreStore port."""
    mcp_server.register_session("sess-1", {"client": "test"})
    req = {
        "jsonrpc": "2.0",
        "id": "5",
        "method": "tools/call",
        "params": {
            "name": "peerhub_append_record",
            "arguments": {
                "stream_id": "events",
                "author_peer_id": "peer:agent1",
                "kind": "m2.test.event",
                "body": {"status": "ok", "count": 42},
                "session_id": "sess-1",
            },
        },
    }
    resp = json.loads(mcp_server.handle_message(json.dumps(req)) or "{}")
    assert "result" in resp
    assert resp["result"]["position"] >= 1

    # Verify authoritative record exists in CoreStore
    records = core_store.read_records("events", after_position=0, limit=10)
    assert len(records) == 1
    assert records[0].kind == "m2.test.event"
    assert records[0].body["count"] == 42


def test_mcp_005_resources_list_and_read(mcp_server: MCPServer, core_store: CoreStore):
    """MCP-005: resources/list and resources/read safely expose peerhub:// URIs."""
    core_store.append_record(
        stream_id="stream-alpha",
        author_peer_id="peer:agent1",
        kind="init",
        body={"hello": "world"},
        idempotency_key="init-1",
        created_at="2026-10-06T00:00:00Z",
    )

    # resources/list
    req_list = {"jsonrpc": "2.0", "id": "6", "method": "resources/list", "params": {}}
    resp_list = json.loads(mcp_server.handle_message(json.dumps(req_list)) or "{}")
    resources = resp_list["result"]["resources"]
    uris = [r["uri"] for r in resources]
    assert "peerhub://stream/stream-alpha" in uris

    # resources/read
    req_read = {
        "jsonrpc": "2.0",
        "id": "7",
        "method": "resources/read",
        "params": {"uri": "peerhub://stream/stream-alpha"},
    }
    resp_read = json.loads(mcp_server.handle_message(json.dumps(req_read)) or "{}")
    contents = resp_read["result"]["contents"]
    assert len(contents) >= 1
    assert "hello" in contents[0]["text"]


def test_mcp_006_prompts_list_and_get(mcp_server: MCPServer):
    """MCP-006: prompts/list and prompts/get render parameterized system prompt templates."""
    req_list = {"jsonrpc": "2.0", "id": "8", "method": "prompts/list", "params": {}}
    resp_list = json.loads(mcp_server.handle_message(json.dumps(req_list)) or "{}")
    prompts = resp_list["result"]["prompts"]
    prompt_names = [p["name"] for p in prompts]
    assert "peerhub_agent_context" in prompt_names

    req_get = {
        "jsonrpc": "2.0",
        "id": "9",
        "method": "prompts/get",
        "params": {
            "name": "peerhub_agent_context",
            "arguments": {"role": "Auditor", "task": "Verify records"},
        },
    }
    resp_get = json.loads(mcp_server.handle_message(json.dumps(req_get)) or "{}")
    messages = resp_get["result"]["messages"]
    assert len(messages) >= 1
    content = messages[0]["content"]["text"]
    assert "Auditor" in content
    assert "Verify records" in content


def test_mcp_007_session_handle_isolation(mcp_server: MCPServer):
    """MCP-007: Session handle isolation rejects invalid or unauthenticated session tokens."""
    # Attempt sensitive call with unauthenticated session_id
    req_unauth = {
        "jsonrpc": "2.0",
        "id": "10",
        "method": "tools/call",
        "params": {
            "name": "peerhub_append_record",
            "arguments": {
                "stream_id": "events",
                "author_peer_id": "peer:agent1",
                "kind": "test",
                "body": {},
                "session_id": "invalid-session-token",
            },
        },
    }
    resp_unauth = json.loads(mcp_server.handle_message(json.dumps(req_unauth)) or "{}")
    assert "error" in resp_unauth
    assert resp_unauth["error"]["code"] == -32000
    assert "MCPSessionInvalidError" in resp_unauth["error"]["message"]

    # Revoke session and ensure calls are rejected
    mcp_server.register_session("sess-revoke", {})
    mcp_server.revoke_session("sess-revoke")
    req_revoked = {
        "jsonrpc": "2.0",
        "id": "11",
        "method": "tools/call",
        "params": {
            "name": "peerhub_append_record",
            "arguments": {
                "stream_id": "events",
                "author_peer_id": "peer:agent1",
                "kind": "test",
                "body": {},
                "session_id": "sess-revoke",
            },
        },
    }
    resp_revoked = json.loads(mcp_server.handle_message(json.dumps(req_revoked)) or "{}")
    assert "error" in resp_revoked
    assert resp_revoked["error"]["code"] == -32000


def test_mcp_008_malformed_jsonrpc_parse_error(mcp_server: MCPServer):
    """MCP-008: Malformed JSON-RPC payload returns ParseError (-32700) without crashing stdio loop."""
    resp_raw = mcp_server.handle_message("{not_valid_json::")
    assert resp_raw is not None
    resp = json.loads(resp_raw)
    assert resp["error"]["code"] == -32700

    # Ensure server still functions for valid message
    valid_req = {"jsonrpc": "2.0", "id": "12", "method": "tools/list", "params": {}}
    valid_resp = json.loads(mcp_server.handle_message(json.dumps(valid_req)) or "{}")
    assert "result" in valid_resp


def test_mcp_009_tool_execution_exception_isolation(mcp_server: MCPServer):
    """MCP-009: Tool execution exception returns JSON-RPC error response and preserves session."""
    mcp_server.register_session("sess-active", {})
    # Negative limit on get_stream_slice
    req = {
        "jsonrpc": "2.0",
        "id": "13",
        "method": "tools/call",
        "params": {
            "name": "peerhub_get_stream_slice",
            "arguments": {"stream_id": "events", "after_position": 0, "limit": -5},
        },
    }
    resp = json.loads(mcp_server.handle_message(json.dumps(req)) or "{}")
    assert "error" in resp or resp.get("result", {}).get("isError") is True

    # Server remains active
    req_ok = {"jsonrpc": "2.0", "id": "14", "method": "tools/list", "params": {}}
    resp_ok = json.loads(mcp_server.handle_message(json.dumps(req_ok)) or "{}")
    assert "result" in resp_ok


def test_mcp_010_secret_masking_in_mcp_output(mcp_server: MCPServer, core_store: CoreStore):
    """MCP-010: Secret masking in MCP tool outputs and serialized representations."""
    mcp_server.register_session("sess-sec", {})
    # Append record with secret payload
    req = {
        "jsonrpc": "2.0",
        "id": "15",
        "method": "tools/call",
        "params": {
            "name": "peerhub_append_record",
            "arguments": {
                "stream_id": "auth-log",
                "author_peer_id": "peer:agent1",
                "kind": "auth.credential",
                "body": {"api_key": "sk-live-123456789abcdef", "secret_token": "bearer-xyz"},
                "session_id": "sess-sec",
            },
        },
    }
    mcp_server.handle_message(json.dumps(req))

    # Read stream slice
    slice_req = {
        "jsonrpc": "2.0",
        "id": "16",
        "method": "tools/call",
        "params": {
            "name": "peerhub_get_stream_slice",
            "arguments": {"stream_id": "auth-log", "after_position": 0, "limit": 10},
        },
    }
    slice_resp = json.loads(mcp_server.handle_message(json.dumps(slice_req)) or "{}")
    text_content = json.dumps(slice_resp)
    assert "sk-live-123456789abcdef" not in text_content
    assert "***MASKED***" in text_content


def test_mcp_011_extension_disablement_clean(mcp_server: MCPServer, core_store: CoreStore):
    """MCP-011: Clean extension disablement: disabling MCP preserves storage and frees server bindings."""
    mcp_server.register_session("sess-close", {})
    mcp_server.close()

    # Sessions are closed
    assert len(mcp_server.sessions) == 0

    # CoreStore continues operating normally
    rec = core_store.append_record(
        stream_id="post-mcp",
        author_peer_id="peer:agent1",
        kind="ping",
        body={"status": "alive"},
        idempotency_key="ping-1",
        created_at="2026-10-06T00:00:00Z",
    )
    assert rec.position >= 1
    records = core_store.read_records("post-mcp", after_position=0, limit=10)
    assert len(records) == 1


def test_mcp_012_zero_dev_dependency_violation():
    """MCP-012: Zero dev-dependency violation: MCP implementation runs on standard library (REL-009)."""
    import importlib
    mcp_mod = importlib.import_module("peerhub.extensions.mcp")
    for bad in ("pytest", "yaml", "opentelemetry", "mcp"):
        assert bad not in mcp_mod.__dict__
