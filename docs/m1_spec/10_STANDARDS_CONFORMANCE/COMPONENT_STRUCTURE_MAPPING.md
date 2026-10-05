# PeerHub Component Structure Mapping — Unified Standard 1.2

- **CORE**: M1 Peer / Stream / Record / Offset + persistence/public ports.
- **MODULE**: M1 Session Bridge / Observation / Readonly Diag. M1에 정적으로 조립되는 필수 module이며 M2 Generic Extension Host로 소급 변환하지 않습니다.
- **EXTENSION**: M2 Artifact/Work/Skill-Catalog/MCP/Backup/Eval과 M3 Search/Memory/A2A/Routing/Orchestration/Approval/Execution Runtime.
- **GENERATED_VIEW**: Work projection, Search index, diagnostics/report, vendor skill projection.
- **EXTERNAL**: AI CLI/process/provider/network/remote agent.

Boundary role은 별도 축입니다. A2A/MCP는 `ADAPTER`, Execution Runtime backend는 `PROVIDER`, OTel 계열은 `EXPORTER`입니다.

Authority 방향: `CORE <- public port <- MODULE/EXTENSION <- ADAPTER/PROVIDER/EXTERNAL`. Core가 Extension 구현을 import하지 않습니다.
