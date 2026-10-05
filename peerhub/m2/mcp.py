"""M2.4 MCP Integration Surface (MCP-001..012).

Freeze Invariant 10: MCP never writes storage directly.
Core imports Extension = 0.
Zero dev-dependency violation: Python standard library only.
"""
from __future__ import annotations

import json
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO, cast


class MCPError(Exception):
    """Base class for MCP errors."""
    code: int = -32603


class MCPDirectStorageAccessError(MCPError):
    """Raised when an operation attempts direct storage write bypassing public ports (Invariant 10)."""
    code: int = -32001


class MCPProtocolError(MCPError):
    """Raised on invalid JSON-RPC 2.0 protocol formatting."""
    code: int = -32600


class MCPMethodNotFoundError(MCPError):
    """Raised when requested tool, resource, or prompt method is unknown."""
    code: int = -32601


class MCPInvalidParamsError(MCPError):
    """Raised when input parameters fail schema or semantic validation."""
    code: int = -32602


class MCPSessionInvalidError(MCPError):
    """Raised when session handle is invalid, unauthenticated, or revoked."""
    code: int = -32000


SECRET_KEY_PATTERN = re.compile(r"(?i)(api[_-]?key|secret[_-]?token|password|credential|bearer[_-]?token|auth[_-]?token)")


def mask_secrets(data: Any) -> Any:
    """Recursively mask sensitive values in dictionaries and lists."""
    if isinstance(data, dict):
        masked: dict[str, Any] = {}
        data_dict = cast(dict[str, Any], data)
        for k, v in data_dict.items():
            if SECRET_KEY_PATTERN.search(str(k)):
                masked[str(k)] = "***MASKED***"
            else:
                masked[str(k)] = mask_secrets(v)
        return masked
    elif isinstance(data, list):
        data_list = cast(list[Any], data)
        return [mask_secrets(item) for item in data_list]
    return data


@dataclass
class MCPSession:
    session_id: str
    client_info: dict[str, Any] = field(default_factory=dict[str, Any])
    state: str = "ACTIVE"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class MCPServer:
    """JSON-RPC 2.0 stdio MCP server exposing PeerHub public ports."""

    def __init__(
        self,
        core_store: Any,
        work_projection: Any = None,
        artifact_store: Any = None,
        skill_catalog: Any = None,
    ) -> None:
        self.core_store = core_store
        self.work_projection = work_projection
        self.artifact_store = artifact_store
        self.skill_catalog = skill_catalog
        self.sessions: dict[str, MCPSession] = {}
        self.is_initialized: bool = False

    def direct_storage_write(self, target_path: Path, data: bytes) -> None:
        """Enforces Invariant 10: direct storage write attempt from MCP layer is blocked."""
        raise MCPDirectStorageAccessError(
            f"Invariant 10 violation: MCP cannot write directly to storage at {target_path}. "
            "All writes must route through public ports."
        )

    def register_session(self, session_id: str, client_info: dict[str, Any]) -> MCPSession:
        session = MCPSession(session_id=session_id, client_info=client_info, state="ACTIVE")
        self.sessions[session_id] = session
        return session

    def revoke_session(self, session_id: str) -> None:
        if session_id in self.sessions:
            self.sessions[session_id].state = "REVOKED"

    def close(self) -> None:
        """Disables server and clears all session state."""
        self.sessions.clear()
        self.is_initialized = False

    def _validate_session(self, session_id: str | None) -> None:
        if not session_id or session_id not in self.sessions:
            raise MCPSessionInvalidError("MCPSessionInvalidError: unauthenticated or invalid session handle")
        if self.sessions[session_id].state != "ACTIVE":
            raise MCPSessionInvalidError(f"MCPSessionInvalidError: session is in {self.sessions[session_id].state} state")

    def handle_message(self, message: str | bytes) -> str | None:
        """Process a single JSON-RPC 2.0 message and return JSON response string (or None for notifications)."""
        if isinstance(message, bytes):
            message = message.decode("utf-8")

        try:
            req_raw: Any = json.loads(message)
        except Exception:
            return json.dumps({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error: invalid JSON"},
            })

        if not isinstance(req_raw, dict):
            return json.dumps({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request: expected jsonrpc 2.0 object"},
            })

        req = cast(dict[str, Any], req_raw)
        jsonrpc_val = req["jsonrpc"] if "jsonrpc" in req else None
        if jsonrpc_val != "2.0":
            err_id: Any = req["id"] if "id" in req else None
            return json.dumps({
                "jsonrpc": "2.0",
                "id": err_id,
                "error": {"code": -32600, "message": "Invalid Request: expected jsonrpc 2.0 object"},
            })

        msg_id: Any = req["id"] if "id" in req else None
        method_str: str = str(req["method"]) if "method" in req else ""
        params_val: Any = req["params"] if "params" in req else {}
        params_dict: dict[str, Any] = cast(dict[str, Any], params_val) if isinstance(params_val, dict) else {}

        try:
            result = self._dispatch_method(method_str, params_dict)
            if msg_id is not None:
                return json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}, ensure_ascii=False)
            return None
        except MCPError as exc:
            if msg_id is not None:
                return json.dumps({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": exc.code, "message": str(exc)},
                }, ensure_ascii=False)
            return None
        except Exception as exc:
            if msg_id is not None:
                return json.dumps({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32603, "message": f"Internal error: {exc}"},
                }, ensure_ascii=False)
            return None

    def _dispatch_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            self.is_initialized = True
            return {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                    "prompts": {"listChanged": False},
                },
                "serverInfo": {
                    "name": "peerhub-mcp",
                    "version": "0.1.0",
                },
            }
        elif method == "tools/list":
            return self._list_tools()
        elif method == "tools/call":
            return self._call_tool(params)
        elif method == "resources/list":
            return self._list_resources()
        elif method == "resources/read":
            return self._read_resource(params)
        elif method == "prompts/list":
            return self._list_prompts()
        elif method == "prompts/get":
            return self._get_prompt(params)
        else:
            raise MCPMethodNotFoundError(f"Method not found: {method}")

    def _list_tools(self) -> dict[str, Any]:
        return {
            "tools": [
                {
                    "name": "peerhub_append_record",
                    "description": "Appends an authoritative record to a stream via CoreStore port.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "stream_id": {"type": "string"},
                            "author_peer_id": {"type": "string"},
                            "kind": {"type": "string"},
                            "body": {"type": "object"},
                            "session_id": {"type": "string"},
                            "idempotency_key": {"type": "string"},
                        },
                        "required": ["stream_id", "author_peer_id", "kind", "body", "session_id"],
                    },
                },
                {
                    "name": "peerhub_get_stream_slice",
                    "description": "Reads records from a stream via CoreStore port.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "stream_id": {"type": "string"},
                            "after_position": {"type": "integer"},
                            "limit": {"type": "integer"},
                        },
                        "required": ["stream_id"],
                    },
                },
            ]
        }

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        tool_name = params.get("name")
        args = params.get("arguments", {})

        if tool_name == "peerhub_append_record":
            for field_name in ("stream_id", "author_peer_id", "kind", "body"):
                if field_name not in args:
                    raise MCPInvalidParamsError(f"Missing required argument: {field_name}")

            session_id = args.get("session_id")
            self._validate_session(session_id)

            stream_id = args["stream_id"]
            author_peer_id = args["author_peer_id"]
            kind = args["kind"]
            body = args["body"]
            idempotency_key = args.get("idempotency_key") or f"mcp-{uuid.uuid4().hex[:12]}"
            created_at = args.get("created_at") or datetime.now(timezone.utc).isoformat()

            rec = self.core_store.append_record(
                stream_id=stream_id,
                author_peer_id=author_peer_id,
                kind=kind,
                body=body,
                idempotency_key=idempotency_key,
                created_at=created_at,
            )
            return {
                "record_id": rec.record_id,
                "position": rec.position,
                "stream_id": rec.stream_id,
            }

        elif tool_name == "peerhub_get_stream_slice":
            stream_id = args.get("stream_id")
            if not stream_id:
                raise MCPInvalidParamsError("Missing required argument: stream_id")

            after_position = args.get("after_position", 0)
            limit = args.get("limit", 100)
            if not isinstance(limit, int) or limit < 1:
                raise MCPInvalidParamsError("Argument limit must be a positive integer")

            records = self.core_store.read_records(stream_id, after_position=after_position, limit=limit)
            masked_records = [
                {
                    "record_id": r.record_id,
                    "position": r.position,
                    "stream_id": r.stream_id,
                    "author_peer_id": r.author_peer_id,
                    "kind": r.kind,
                    "body": mask_secrets(r.body),
                }
                for r in records
            ]
            return {"records": masked_records}

        else:
            raise MCPMethodNotFoundError(f"Unknown tool: {tool_name}")

    def _list_resources(self) -> dict[str, Any]:
        with self.core_store._read() as conn:
            rows = conn.execute("SELECT stream_id, title FROM streams ORDER BY stream_id").fetchall()
            resources = [
                {
                    "uri": f"peerhub://stream/{row['stream_id']}",
                    "name": f"Stream {row['stream_id']}",
                    "mimeType": "application/json",
                }
                for row in rows
            ]
        return {"resources": resources}

    def _read_resource(self, params: dict[str, Any]) -> dict[str, Any]:
        uri = params.get("uri", "")
        if uri.startswith("peerhub://stream/"):
            stream_id = uri[len("peerhub://stream/"):]
            records = self.core_store.read_records(stream_id, after_position=0, limit=100)
            content_data = [
                {
                    "record_id": r.record_id,
                    "position": r.position,
                    "kind": r.kind,
                    "body": mask_secrets(r.body),
                }
                for r in records
            ]
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(content_data, ensure_ascii=False),
                    }
                ]
            }
        else:
            raise MCPInvalidParamsError(f"Unsupported resource URI: {uri}")

    def _list_prompts(self) -> dict[str, Any]:
        return {
            "prompts": [
                {
                    "name": "peerhub_agent_context",
                    "description": "Standard agent context and instruction prompt template.",
                    "arguments": [
                        {"name": "role", "description": "Agent persona or role", "required": True},
                        {"name": "task", "description": "Assigned task description", "required": True},
                    ],
                }
            ]
        }

    def _get_prompt(self, params: dict[str, Any]) -> dict[str, Any]:
        prompt_name = params.get("name")
        args = params.get("arguments", {})

        if prompt_name == "peerhub_agent_context":
            role = args.get("role", "Collaborator")
            task = args.get("task", "Participate in stream")
            text = (
                f"You are operating as {role} within PeerHub.\n"
                f"Current task: {task}\n"
                "Invariant: maintain Core imports Extension = 0 and route mutations through public ports."
            )
            return {
                "messages": [
                    {
                        "role": "system",
                        "content": {
                            "type": "text",
                            "text": text,
                        },
                    }
                ]
            }
        else:
            raise MCPMethodNotFoundError(f"Unknown prompt: {prompt_name}")

    def run_stdio(self, reader: TextIO = sys.stdin, writer: TextIO = sys.stdout) -> None:
        """Stream processor reading from stdio and writing responses."""
        for line in reader:
            line = line.strip()
            if not line:
                continue
            resp = self.handle_message(line)
            if resp is not None:
                writer.write(resp + "\n")
                writer.flush()
