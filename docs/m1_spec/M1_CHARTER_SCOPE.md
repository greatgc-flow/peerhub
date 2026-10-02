# M1 Scope / Non-Scope

## Scope
- Peer identity;
- Stream + minimal membership;
- immutable ordered Record;
- Offset;
- idempotent append;
- workspace identity / SQLite durability;
- local Claude/Codex/Agy adapters;
- Session Bridge;
- structured Observation;
- read-only status/peers/logs views;
- separate Readonly Diag.

## Non-Scope
Orchestration, routing, governance, task engine, long-term memory, artifact lifecycle,
A2A/MCP server, distributed runtime, general lock/lease system, web UI.

M1은 “현재 모든 기능을 유지한 작은 버전”이 아니라 **제품 목적을 다시 세운 첫 vertical slice**입니다.
