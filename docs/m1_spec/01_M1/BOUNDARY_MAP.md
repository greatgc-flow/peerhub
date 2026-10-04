# 경계면 지도

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

## 내부
Core 내부 same-process 경계는 Python typed call을 기본으로 합니다.

## MCP
Tool/Data/Context가 실제 외부 경계가 될 때만.

## A2A
Remote independent agent가 생길 때만.

## OpenAPI/AsyncAPI/CloudEvents
M1에는 불필요. HTTP/event public boundary가 생기면 조건부 검토.

최신 기술 지원을 이유로 내부 구조를 외부 protocol에 맞추지 않습니다.
