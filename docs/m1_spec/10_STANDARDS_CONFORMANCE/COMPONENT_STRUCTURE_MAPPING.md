# PeerHub Component Structure Mapping — Unified Standard 1.2

- **CORE**: M1 Peer / Stream / Record / Offset + persistence/public ports.
- **MODULE**: M1 Session Bridge / Observation / Readonly Diag. These are mandatory modules statically assembled into M1 and are not retroactively converted to the M2 Generic Extension Host.
- **EXTENSION**: M2 Artifact/Work/Skill-Catalog/MCP/Backup/Eval and M3 Search/Memory/A2A/Routing/Orchestration/Approval/Execution Runtime.
- **GENERATED_VIEW**: Work projection, Search index, diagnostics/report, vendor skill projection.
- **EXTERNAL**: AI CLI/process/provider/network/remote agent.

Boundary role is a separate axis. A2A/MCP are `ADAPTER`, Execution Runtime backend is `PROVIDER`, OTel family is `EXPORTER`.

Authority direction: `CORE <- public port <- MODULE/EXTENSION <- ADAPTER/PROVIDER/EXTERNAL`. Core does not import Extension implementations.
