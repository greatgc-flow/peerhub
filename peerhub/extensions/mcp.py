"""M2.4 MCP Integration Surface (MCP-001..012).

Freeze Invariant 10: MCP never writes storage directly.
Core imports Extension = 0.
Zero dev-dependency violation: Python standard library only.
"""
from __future__ import annotations

import json
import base64
import binascii
import re
import sys
import uuid
from dataclasses import asdict, dataclass, field
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


SECRET_KEY_PATTERN = re.compile(r"(?i)(api[_-]?key|secret|password|credential|bearer[_-]?token|auth[_-]?token|access[_-]?token|private[_-]?key)")
SECRET_TEXT_PATTERN = re.compile(r"(?i)(?:\bBearer\s+\S+|\bsk-[A-Za-z0-9_-]{8,}|(?:api[_-]?key|password|secret[_-]?token)\s*[=:]\s*\S+)")
SECRET_PATH_PATTERN = re.compile(
    r"(?i)(?:[a-z]:[\\/]|/)[^\r\n\"<>]*?(?:auth\.json|credentials\.json|\.env(?:\.[\w-]+)?|id_(?:rsa|ed25519))(?!\w)"
)


def mask_secrets(data: Any) -> Any:
    """Recursively mask sensitive values in dictionaries and lists."""
    if isinstance(data, dict):
        masked: dict[str, Any] = {}
        data_dict = cast(dict[str, Any], data)
        for k, v in data_dict.items():
            if SECRET_KEY_PATTERN.search(str(k)) or str(k).casefold() in {"token", "authorization"}:
                masked[str(k)] = "***MASKED***"
            else:
                masked[str(k)] = mask_secrets(v)
        return masked
    elif isinstance(data, list):
        data_list = cast(list[Any], data)
        return [mask_secrets(item) for item in data_list]
    if isinstance(data, str):
        return SECRET_PATH_PATTERN.sub("***MASKED***", SECRET_TEXT_PATTERN.sub("***MASKED***", data))
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
        """Host-only trusted registration, never exposed as an MCP wire method.

        Hosts may bind author_peer_id and allowed_streams in client_info. A handle
        with neither binding is an explicitly trusted unrestricted local session.
        """
        if not isinstance(cast(object, session_id), str) or not session_id or session_id in self.sessions:
            raise MCPSessionInvalidError("Session must have a unique nonempty handle")
        if not isinstance(cast(object, client_info), dict):
            raise MCPSessionInvalidError("Session capabilities must be an object")
        if "author_peer_id" in client_info and not isinstance(client_info["author_peer_id"], str):
            raise MCPSessionInvalidError("Session author capability must be a string")
        allowed: Any = client_info.get("allowed_streams")
        if allowed is not None and (not isinstance(allowed, list)
                                   or any(not isinstance(value, str) for value in cast(list[Any], allowed))):
            raise MCPSessionInvalidError("Session stream capabilities must be a list of strings")
        # Caller mutation cannot broaden an already registered capability.
        client_info = json.loads(json.dumps(client_info))
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
        if not isinstance(cast(object, session_id), str) or not session_id or session_id not in self.sessions:
            raise MCPSessionInvalidError("MCPSessionInvalidError: unauthenticated or invalid session handle")
        if self.sessions[session_id].state != "ACTIVE":
            raise MCPSessionInvalidError(f"MCPSessionInvalidError: session is in {self.sessions[session_id].state} state")

    def handle_message(self, message: str | bytes) -> str | None:
        """Process a single JSON-RPC 2.0 message and return JSON response string (or None for notifications)."""
        try:
            if isinstance(message, bytes):
                message = message.decode("utf-8")
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
        has_id = "id" in req
        if (not isinstance(req.get("method"), str) or not req.get("method")
                or isinstance(msg_id, (dict, list, bool))):
            return json.dumps({"jsonrpc": "2.0", "id": None,
                               "error": {"code": -32600, "message": "Invalid Request"}})
        method_str: str = req["method"]
        params_val: Any = req["params"] if "params" in req else {}

        try:
            if not isinstance(params_val, dict):
                raise MCPInvalidParamsError("params must be an object")
            params_dict = cast(dict[str, Any], params_val)
            result = self._dispatch_method(method_str, params_dict)
            if has_id:
                return json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": mask_secrets(result)}, ensure_ascii=False)
            return None
        except MCPError as exc:
            if has_id:
                return json.dumps({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": exc.code, "message": mask_secrets(str(exc))},
                }, ensure_ascii=False)
            return None
        except Exception:
            if has_id:
                return json.dumps({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32603, "message": "Internal error"},
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
            data = mask_secrets(self._call_tool(params))
            return {**data, "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                    "isError": False}
        elif method in ("notifications/initialized", "ping"):
            return {}
        elif method == "shutdown":
            self.close()
            return {}
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
        result: dict[str, Any] = {
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
        optional: list[tuple[str, Any, dict[str, Any], list[str]]] = [
            ("peerhub_get_work_tree", self.work_projection, {"work_id": {"type": "string"}}, []),
            ("peerhub_store_artifact", self.artifact_store,
             {"blob_content_b64": {"type": "string"}, "mime_type": {"type": "string"},
              "session_id": {"type": "string"}}, ["blob_content_b64", "mime_type", "session_id"]),
            ("peerhub_search_skills", self.skill_catalog,
             {"query": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}}, ["query"]),
        ]
        for name, port, properties, required in optional:
            if port is not None:
                result["tools"].append({"name": name, "description": "Uses the configured public extension port.",
                    "inputSchema": {"type": "object", "properties": properties, "required": required}})
        return result

    @staticmethod
    def _string(args: dict[str, Any], key: str, *, empty: bool = False) -> str:
        value = args.get(key)
        if not isinstance(value, str) or (not empty and not value):
            raise MCPInvalidParamsError(f"{key} must be a string" + ("" if empty else " and nonempty"))
        return value

    def _authorize(self, session_id: str, stream_id: str | None = None, author: str | None = None) -> None:
        self._validate_session(session_id)
        info = self.sessions[session_id].client_info
        if author is not None and info.get("author_peer_id", author) != author:
            raise MCPSessionInvalidError("Session author capability mismatch")
        if stream_id is not None and "allowed_streams" in info and stream_id not in info["allowed_streams"]:
            raise MCPSessionInvalidError("Session stream capability mismatch")

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        tool_name = params.get("name")
        args_raw: Any = params.get("arguments", {})
        if not isinstance(args_raw, dict):
            raise MCPInvalidParamsError("arguments must be an object")
        args = cast(dict[str, Any], args_raw)

        if tool_name == "peerhub_append_record":
            for field_name in ("stream_id", "author_peer_id", "kind", "body"):
                if field_name not in args:
                    raise MCPInvalidParamsError(f"Missing required argument: {field_name}")

            session_id = args.get("session_id")
            self._validate_session(session_id)
            session_id = cast(str, session_id)

            stream_id = self._string(args, "stream_id")
            author_peer_id = self._string(args, "author_peer_id")
            kind = self._string(args, "kind")
            self._authorize(session_id, stream_id, author_peer_id)
            body = args["body"]
            if not isinstance(body, dict):
                raise MCPInvalidParamsError("body must be an object")
            try:
                json.dumps(body, allow_nan=False)
            except (ValueError, TypeError) as exc:
                raise MCPInvalidParamsError("body must contain finite JSON values") from exc
            if "idempotency_key" in args:
                self._string(args, "idempotency_key")
            idempotency_key = args.get("idempotency_key") or f"mcp-{uuid.uuid4().hex[:12]}"
            previous = self.core_store.find_record_by_idempotency(stream_id, author_peer_id, idempotency_key)
            created_at = args.get("created_at") or (previous.created_at if previous else datetime.now(timezone.utc).isoformat())

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
                "idempotency_key": idempotency_key,
            }

        elif tool_name == "peerhub_get_stream_slice":
            stream_id = self._string(args, "stream_id")

            after_position = args.get("after_position", 0)
            limit = args.get("limit", 100)
            if type(limit) is not int or not 1 <= limit <= 1000:
                raise MCPInvalidParamsError("Argument limit must be an integer in 1..1000")
            if type(after_position) is not int or after_position < 0:
                raise MCPInvalidParamsError("after_position must be an integer >= 0")

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

        elif tool_name == "peerhub_get_work_tree" and self.work_projection is not None:
            work_id = args.get("work_id")
            items = ([self.work_projection.get_work(self._string(args, "work_id"))]
                     if work_id is not None else self.work_projection.list_work())
            return {"work": [asdict(item) for item in items]}

        elif tool_name == "peerhub_search_skills" and self.skill_catalog is not None:
            query = self._string(args, "query", empty=True)
            tags = args.get("tags")
            if tags is not None:
                if not isinstance(tags, list):
                    raise MCPInvalidParamsError("tags must be a list of strings")
                if any(not isinstance(tag, str) for tag in cast(list[Any], tags)):
                    raise MCPInvalidParamsError("tags must be a list of strings")
            skills = [asdict(item) for item in self.skill_catalog.search_skills(query, tags)]
            for item in skills:
                item.pop("path", None)  # Internal source paths are not integration metadata.
            return {"skills": skills}

        elif tool_name == "peerhub_store_artifact" and self.artifact_store is not None:
            self._authorize(self._string(args, "session_id"))
            encoded = self._string(args, "blob_content_b64", empty=True)
            mime = self._string(args, "mime_type")
            if len(encoded) > 12 * 1024 * 1024:
                raise MCPInvalidParamsError("Artifact exceeds MCP upload limit")
            try:
                blob = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise MCPInvalidParamsError("Invalid base64 artifact") from exc
            staged = self.artifact_store.stage_bytes(blob)
            digest = self.artifact_store.commit_staged(staged)
            return {"digest": digest, "size_bytes": len(blob), "mime_type": mime}

        else:
            raise MCPMethodNotFoundError(f"Unknown tool: {tool_name}")

    def _list_resources(self) -> dict[str, Any]:
        resources = [
                {
                    "uri": f"peerhub://stream/{stream.stream_id}",
                    "name": f"Stream {stream.stream_id}",
                    "mimeType": "application/json",
                }
                for stream in self.core_store.list_streams()
        ]
        resources.append({"uri": "peerhub://streams", "name": "Streams", "mimeType": "application/json"})
        if self.artifact_store is not None:
            resources.append({"uri": "peerhub://artifacts", "name": "Artifacts", "mimeType": "application/json"})
        return {"resources": resources}

    def _read_resource(self, params: dict[str, Any]) -> dict[str, Any]:
        uri = self._string(params, "uri")
        if uri == "peerhub://streams":
            data = [{"stream_id": stream.stream_id, "title": stream.title} for stream in self.core_store.list_streams()]
            return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(mask_secrets(data))}]}
        if uri == "peerhub://artifacts" and self.artifact_store is not None:
            data = sorted(self.artifact_store.list_all_digests())
            return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data)}]}
        if uri.startswith("peerhub://artifact/") and self.artifact_store is not None:
            digest = uri[len("peerhub://artifact/"):]
            if not self.artifact_store.validate_digest(digest):
                raise MCPInvalidParamsError("Invalid artifact digest")
            blob = self.artifact_store.read_bytes(digest)
            # Metadata-only prevents binary artifacts from bypassing secret sanitization.
            data = {"digest": digest, "size_bytes": len(blob)}
            return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data)}]}
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
        raw_args: Any = params.get("arguments", {})
        if not isinstance(raw_args, dict):
            raise MCPInvalidParamsError("arguments must be an object")
        args = cast(dict[str, Any], raw_args)

        if prompt_name == "peerhub_agent_context":
            role = self._string(args, "role")
            task = self._string(args, "task")
            text = (
                f"You are operating as {role} within PeerHub.\n"
                f"Current task: {task}\n"
                "Invariant: maintain Core imports Extension = 0 and route mutations through public ports."
            )
            return {
                "messages": [
                    {
                        "role": "user",
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
