# Boundary Map

```text
Human
  │ CLI/UI
  ▼
PeerHub Core
  │
  ├─ Session Bridge ── Local Claude/Codex/Agy
  │                └─ Future Managed Harness
  │
  ├─ Observation ───── provider/runtime facts
  ├─ Diag (read only)
  ├─ MCP Extension ─── Tool/Data/Context      [future]
  ├─ A2A Adapter ───── Remote independent Peer [future]
  ├─ Memory Extension ─ Second Brain          [future]
  └─ OTel Exporter ─── external telemetry     [future]
```

## Internal
Use typed Python calls by default for same-process boundaries within Core.

## MCP
Only when Tool/Data/Context forms an actual external boundary.

## A2A
Only when a remote independent agent exists.

## OpenAPI/AsyncAPI/CloudEvents
Unnecessary for M1. Consider conditionally when a public HTTP/event boundary exists.

Do not reshape the internal architecture around external protocols just to support the latest technology.
