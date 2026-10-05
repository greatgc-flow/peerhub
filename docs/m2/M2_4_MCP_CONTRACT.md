# MCP Integration Surface Contract (M2.4)

## 1. Authority Separation & Core Invariants
- **Core Invariant 10: `MCP never writes storage directly`:**
  - MCP is strictly an external projection and integration boundary adapter.
  - All read and write operations initiated via MCP protocols MUST route exclusively through public core and extension ports (`CoreStore`, `WorkProjection`, `ArtifactStore`, `SkillCatalog`).
  - Direct file I/O against database files (`core.db`, `work.db`) or raw blob storage from MCP handlers is strictly prohibited.
- **Protocol Baseline:**
  - Standard JSON-RPC 2.0 framing over standard input/output streams (`stdio-first`).
  - Request, response, error, and notification framing complies with standard MCP specification.
- **Session & Connection Decoupling:**
  - PeerHub Work/Session state is NEVER coupled to transport connection state.
  - Explicit session handles and capability references are required and validated per invocation.
- **Secret Masking & Output Sanitization:**
  - Sensitive tokens, credentials, and internal secret paths are masked in all MCP responses.
- **Zero Dev-Dependency Violation (REL-009):**
  - Pure Python standard library implementation. No unpinned or third-party MCP SDK dependencies.

## 2. Public Interfaces & Protocols
The MCP surface (`peerhub.m2.mcp`) provides typed public interfaces:
- `MCPServer(core_store, work_projection=None, artifact_store=None, skill_catalog=None)`:
  - `handle_message(message: str | bytes) -> str | None`: Process a single JSON-RPC 2.0 request or notification string.
  - `run_stdio(reader, writer) -> None`: Stream processor reading from stdio and writing responses.
  - `register_session(session_id: str, client_info: dict) -> MCPSessionHandle`
  - `revoke_session(session_id: str) -> None`
- Available MCP Tools:
  - `peerhub_append_record(stream: str, record_type: str, payload: dict, session_id: str) -> dict`
  - `peerhub_get_stream_slice(stream: str, start_offset: int, limit: int) -> dict`
  - `peerhub_get_work_tree(work_id: str | None) -> dict`
  - `peerhub_store_artifact(blob_content_b64: str, mime_type: str, session_id: str) -> dict`
  - `peerhub_search_skills(query: str, tags: list[str] | None) -> dict`
- Available MCP Resources:
  - `peerhub://streams`: List available streams.
  - `peerhub://stream/{stream_name}`: Read stream records.
  - `peerhub://artifacts`: List stored artifacts metadata.
  - `peerhub://artifact/{digest}`: Read artifact content or metadata.
- Available MCP Prompts:
  - `peerhub_agent_context(role: str, task: str) -> dict`: Formats prompt context for agent execution.

## 3. MCP Session State Machine
```text
[MCP Session Lifecycle]
  UNINITIALIZED -> INITIALIZING -> ACTIVE -> REVOKED
                         │
                         ▼
                       CLOSED
```
- `initialize`: transitions `UNINITIALIZED` -> `INITIALIZING` -> `ACTIVE`, negotiating server capabilities.
- `shutdown`: transitions `ACTIVE` -> `CLOSED`.
- Invalid session handles on sensitive tools raise `MCPSessionInvalidError`.
- Direct storage manipulation attempts raise `MCPDirectStorageAccessError`.
