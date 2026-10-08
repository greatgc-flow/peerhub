# Test → Release Gate Traceability

> `TEST_RELEASE_GATE_MAP.json`이 SSOT입니다. 각 테스트는 하나의 primary gate에 배정되며 gate dependency가 상위 gate를 연결합니다.

| Gate | Test count | Purpose |
|---|---:|---|
| G0 | 77 | fast/static/unit contract |
| G1 | 39 | Core/storage/concurrency/migration |
| G2 | 68 | Bridge/Observation/Diag/fake-runtime E2E |
| G3 | 6 | real-provider live |
| G4 | 13 | package/clean-install/matrix |
| G6 | 2 | non-blocking soak/capacity |

## Gate별 테스트 ID

### G0
`ARCH-001`, `ARCH-002`, `ARCH-003`, `ARCH-004`, `ARCH-005`, `SCH-001`, `SCH-002`, `SCH-003`, `SCH-004`, `SCH-005`, `SCH-006`, `SCH-007`, `SCH-008`, `SCH-009`, `SCH-010`, `CORE-001`, `CORE-002`, `CORE-003`, `CORE-004`, `CORE-005`, `CORE-006`, `CORE-007`, `CORE-008`, `CORE-009`, `CORE-010`, `CORE-012`, `CORE-011`, `OFF-001`, `OFF-002`, `OFF-003`, `OFF-004`, `OFF-005`, `PROP-001`, `PROP-002`, `PROP-003`, `PROP-004`, `PROP-005`, `PROP-006`, `OBS-001`, `OBS-002`, `OBS-003`, `OBS-004`, `OBS-005`, `SCH-011`, `SCH-012`, `SCH-013`, `PROP-007`, `PROP-008`, `SEC-001`, `STR-001`, `STR-002`, `STR-005`, `OFF-006`, `CTX-001`, `CTX-002`, `CLM-001`, `CTL-003`, `OBS-009`, `OBS-010`, `OBS-011`, `PROP-009`, `META-001`, `META-002`, `META-003`, `META-004`, `IDEM-001`, `IDEM-002`, `CORE-013`, `READ-001`, `READ-002`, `PROP-010`, `OBS-017`, `OBS-018`, `SCH-014`, `SCH-015`, `PROP-011`, `CERT-001`

### G1
`SQL-001`, `SQL-002`, `SQL-003`, `SQL-004`, `SQL-005`, `SQL-007`, `SQL-008`, `SQL-009`, `SQL-010`, `CON-001`, `CON-002`, `CON-003`, `CON-004`, `CON-005`, `CON-008`, `CON-009`, `FLT-001`, `FLT-002`, `FLT-006`, `FLT-008`, `FLT-009`, `STR-003`, `STR-004`, `STR-006`, `OFF-007`, `OFF-008`, `CON-010`, `FLT-011`, `FLT-012`, `FLT-013`, `MIG-001`, `MIG-002`, `MIG-003`, `MP-001`, `MP-002`, `MP-003`, `IDEM-003`, `SQL-011`, `CON-011`

### G2
`SQL-006`, `CON-006`, `CON-007`, `FLT-003`, `FLT-004`, `FLT-005`, `FLT-007`, `FLT-010`, `BRG-001`, `BRG-002`, `BRG-003`, `BRG-004`, `BRG-005`, `BRG-006`, `BRG-007`, `BRG-008`, `BRG-009`, `BRG-010`, `OBS-006`, `OBS-007`, `OBS-008`, `DIA-001`, `DIA-002`, `DIA-003`, `DIA-004`, `DIA-005`, `DIA-006`, `DIA-007`, `E2E-001`, `E2E-002`, `E2E-003`, `E2E-004`, `E2E-005`, `E2E-006`, `E2E-007`, `E2E-008`, `E2E-009`, `BRG-011`, `BRG-012`, `BRG-013`, `BRG-014`, `BRG-015`, `BRG-016`, `BRG-017`, `CTX-003`, `CLM-002`, `CLM-003`, `CTL-001`, `CTL-002`, `CTL-004`, `CTL-005`, `OBS-012`, `OBS-013`, `OBS-014`, `DIA-008`, `DIA-009`, `DIA-010`, `E2E-010`, `E2E-011`, `OBS-015`, `OBS-016`, `BRG-018`, `MP-004`, `FLT-014`, `E2E-012`, `BRG-019`, `CERT-002`, `CERT-003`

### G3
`LIVE-CC-001`, `LIVE-CX-001`, `LIVE-AG-001`, `LIVE-004`, `LIVE-005`, `LIVE-006`

### G4
`REL-001`, `REL-002`, `REL-003`, `REL-004`, `REL-005`, `REL-006`, `REL-007`, `REL-008`, `REL-009`, `REL-010`, `REL-011`, `REL-013`, `REL-014`

### G6
`SOAK-001`, `SOAK-002`
