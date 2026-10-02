# Harness / MCP / A2A 연계 가이드

## Harness
managed/local agent runtime은 Session Bridge target.

## MCP
AI ↔ Tool/Data/Context 경계가 생길 때 Extension.
Core API를 우회해 DB를 직접 수정시키지 않습니다.

## A2A
remote independent agent가 생기면 `A2A Adapter -> Peer`.
A2A Task state machine을 Core로 복사하지 않습니다.

## OTel
Observation을 외부 telemetry backend로 내보내는 exporter.
OTel semantic convention이 Core DB schema가 되지 않습니다.
