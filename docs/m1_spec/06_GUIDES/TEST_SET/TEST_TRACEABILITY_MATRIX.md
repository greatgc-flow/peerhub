# Test Traceability Matrix

> Requirements: **88** / Tests: **210**

| Requirement | Area | P | Missing dimensions | Tests |
|---|---|---|---|---|
| `REQ-ARCH-001` | Architecture | P0 | **NONE** | ARCH-001, ARCH-002, E2E-009 |
| `REQ-CORE-001` | Core/Peer | P0 | **NONE** | ARCH-005, SCH-001, SCH-002, CORE-001 |
| `REQ-CORE-002` | Core/Stream | P0 | **NONE** | SCH-003 |
| `REQ-CORE-003` | Core/Record | P0 | **NONE** | SCH-004, SCH-005, CORE-003, CORE-004, SQL-004 |
| `REQ-CORE-004` | Core/Offset | P0 | **NONE** | SCH-006, OFF-001, OFF-005, BRG-001 |
| `REQ-ORDER-001` | Ordering | P0 | **NONE** | CORE-003, CORE-005, PROP-004, SQL-005, CON-001, CON-002, CON-009, FLT-001, FLT-009, E2E-001, MP-001, CON-011 |
| `REQ-IDEM-001` | Idempotency | P0 | **NONE** | CORE-006, PROP-001, CON-003, FLT-002, E2E-002, MP-002 |
| `REQ-IDEM-002` | Idempotency | P0 | **NONE** | CORE-007, CORE-010, PROP-002, CON-004 |
| `REQ-IDEM-003` | Idempotency | P0 | **NONE** | CORE-008, CORE-009, CORE-010, PROP-002, PROP-003, PROP-008, SQL-011, PROP-011 |
| `REQ-DELIVERY-001` | Delivery | P0 | **NONE** | CORE-012 |
| `REQ-CONTROL-001` | Control | P1 | **NONE** | CORE-011, BRG-008, BRG-009, E2E-005 |
| `REQ-PERSIST-001` | Persistence | P0 | **NONE** | SQL-001, SQL-002, SQL-003, SQL-004, SQL-005, SQL-006, CON-002, FLT-009 |
| `REQ-PERSIST-002` | Persistence | P0 | **NONE** | ARCH-002, SQL-008, E2E-009 |
| `REQ-OFFSET-001` | Offset | P0 | **NONE** | SCH-006, OFF-002, OFF-003, PROP-005, CON-005, CON-010, MP-003 |
| `REQ-OFFSET-002` | Offset | P0 | **NONE** | OFF-004, PROP-006 |
| `REQ-FAULT-001` | Crash | P0 | **NONE** | FLT-001 |
| `REQ-FAULT-002` | Crash | P0 | **NONE** | FLT-002, E2E-002 |
| `REQ-FAULT-003` | Crash | P0 | **NONE** | FLT-003, FLT-006, E2E-004, BRG-012 |
| `REQ-FAULT-004` | Crash | P0 | **NONE** | FLT-005, BRG-015 |
| `REQ-FAULT-005` | Crash | P0 | **NONE** | CON-007, CON-008, FLT-007, FLT-014 |
| `REQ-FAULT-006` | Crash/Restore | P0 | **NONE** | SQL-009, SQL-010, FLT-008, E2E-008 |
| `REQ-BRIDGE-001` | Session Bridge | P0 | **NONE** | FLT-005, BRG-001, BRG-006, BRG-007, E2E-001, LIVE-CC-001, LIVE-CX-001, LIVE-AG-001 |
| `REQ-BRIDGE-002` | Session Bridge | P0 | **NONE** | CON-006, CON-007, FLT-007, E2E-008, MP-004, E2E-012 |
| `REQ-BRIDGE-003` | Session Bridge | P1 | **NONE** | BRG-002, BRG-003, BRG-010, E2E-006, LIVE-004, BRG-018 |
| `REQ-BRIDGE-004` | Session Bridge | P0 | **NONE** | BRG-004, E2E-003, E2E-006, CTX-003, E2E-010 |
| `REQ-BRIDGE-005` | Session Bridge | P0 | **NONE** | BRG-005 |
| `REQ-BRIDGE-006` | Session Bridge | P0 | **NONE** | BRG-008, BRG-009, E2E-005 |
| `REQ-CERTAINTY-001` | Execution Certainty | P0 | **NONE** | FLT-003, FLT-004, BRG-007, E2E-004, BRG-011, CERT-001 |
| `REQ-CONT-001` | Continuity | P0 | **NONE** | SQL-007, E2E-002, E2E-003, REL-003, SOAK-001, E2E-012 |
| `REQ-OBS-001` | Observation | P0 | **NONE** | SCH-007, FLT-010, BRG-009, OBS-003, OBS-004, OBS-008, LIVE-004, LIVE-005 |
| `REQ-OBS-002` | Observation | P0 | **NONE** | OBS-005, OBS-008, OBS-009 |
| `REQ-OBS-003` | Observation | P0 | **NONE** | OBS-001, OBS-002, OBS-014 |
| `REQ-OBS-004` | Observation | P1 | **NONE** | SCH-008, OBS-001, OBS-006, OBS-007, E2E-007, OBS-016 |
| `REQ-DIAG-001` | Diag | P0 | **NONE** | ARCH-004, SQL-006, DIA-001, DIA-002, DIA-003, DIA-004, DIA-006, DIA-007, E2E-007, DIA-010 |
| `REQ-DIAG-002` | Diag | P1 | **NONE** | DIA-005, DIA-006 |
| `REQ-CARD-001` | Cardinality | P0 | **NONE** | ARCH-003, CORE-002, CON-009, BRG-010, OBS-006, E2E-001, SOAK-002 |
| `REQ-REL-001` | Release | P0 | **NONE** | REL-001, REL-002, REL-009 |
| `REQ-REL-002` | Release | P0 | **NONE** | LIVE-CC-001, LIVE-CX-001, LIVE-AG-001, LIVE-006, REL-005, REL-006 |
| `REQ-REL-003` | Release | P0 | **NONE** | SCH-009, SCH-010, LIVE-005, REL-004, REL-007 |
| `REQ-REL-004` | Release | P0 | **NONE** | REL-003 |
| `REQ-BOUND-001` | Boundary Validation | P0 | **NONE** | SCH-011, SCH-013, STR-003, MIG-003 |
| `REQ-BOUND-002` | Boundary Validation | P0 | **NONE** | PROP-007, SEC-001, PROP-009 |
| `REQ-BOUND-003` | Boundary Validation | P0 | **NONE** | SCH-012, STR-005 |
| `REQ-STREAM-001` | Stream Lifecycle | P0 | **NONE** | STR-001, STR-002, STR-003, STR-004 |
| `REQ-STREAM-002` | Stream Membership | P0 | **NONE** | STR-005, STR-006 |
| `REQ-OFFSET-003` | Offset | P0 | **NONE** | OFF-006, OFF-007, CON-010 |
| `REQ-OFFSET-004` | Offset | P0 | **NONE** | OFF-008 |
| `REQ-PERSIST-003` | Persistence Faults | P0 | **NONE** | FLT-011, FLT-012 |
| `REQ-PERSIST-004` | Persistence Integrity | P0 | **NONE** | FLT-013 |
| `REQ-PERSIST-005` | Multi-process Concurrency | P0 | **NONE** | MP-001, MP-002, MP-003, MP-004 |
| `REQ-MIG-001` | Schema Migration | P0 | **NONE** | MIG-001 |
| `REQ-MIG-002` | Schema Migration | P0 | **NONE** | MIG-002, MIG-003 |
| `REQ-BRIDGE-007` | Session Bridge | P0 | **NONE** | BRG-011, BRG-012 |
| `REQ-BRIDGE-008` | Session Bridge | P0 | **NONE** | BRG-013 |
| `REQ-BRIDGE-009` | Session Bridge | P0 | **NONE** | BRG-014, BRG-015 |
| `REQ-BRIDGE-010` | Session Bridge | P0 | **NONE** | BRG-016, BRG-017, E2E-010 |
| `REQ-BRIDGE-011` | Context Catch-up | P0 | **NONE** | CTX-001, CTX-002, CTX-003 |
| `REQ-BRIDGE-012` | Claim Lifecycle | P0 | **NONE** | CLM-001, CLM-002, CLM-003 |
| `REQ-CONTROL-002` | Control | P0 | **NONE** | CTL-001, CTL-002, E2E-011 |
| `REQ-CONTROL-003` | Control | P0 | **NONE** | CTL-003, E2E-011 |
| `REQ-CONTROL-004` | Long-Horizon Direction | P1 | **NONE** | CTL-004, CTL-005 |
| `REQ-OBS-005` | Observation Time | P0 | **NONE** | OBS-009, OBS-010, OBS-011, OBS-017 |
| `REQ-OBS-006` | Observation Isolation | P0 | **NONE** | OBS-012, DIA-010 |
| `REQ-OBS-007` | Quota Exhaustion | P0 | **NONE** | OBS-013, OBS-014 |
| `REQ-DIAG-003` | Diag Snapshot | P0 | **NONE** | DIA-008 |
| `REQ-DIAG-004` | Diag Inputs | P0 | **NONE** | DIA-009 |
| `REQ-SEC-001` | Data Integrity / Security | P0 | **NONE** | PROP-007, SEC-001 |
| `REQ-RES-001` | Capacity / Resource | P1 | **NONE** | SOAK-001, SOAK-002, PROP-009 |
| `REQ-IMP-001` | Legacy Import / Cutover | P1 | **NONE** | IMP-001, IMP-002 |
| `REQ-IMP-002` | Legacy Import / Cutover | P1 | **NONE** | IMP-003, IMP-004 |
| `REQ-REL-005` | Package Contents | P0 | **NONE** | REL-008, REL-009 |
| `REQ-REL-006` | Compatibility Matrix | P0 | **NONE** | REL-010, REL-011 |
| `REQ-LEGACY-001` | Legacy Disposition | P0 | **NONE** | REL-012 |
| `REQ-META-001` | Test Meta-Quality | P0 | **NONE** | META-001 |
| `REQ-META-002` | Test Meta-Quality | P0 | **NONE** | META-002 |
| `REQ-META-003` | Test Meta-Quality | P0 | **NONE** | META-003 |
| `REQ-META-004` | Test Meta-Quality | P0 | **NONE** | META-004 |
| `REQ-IDEM-004` | Idempotency | P0 | **NONE** | CORE-006, CORE-007, CON-003, CON-004, IDEM-001, IDEM-002, IDEM-003 |
| `REQ-CORE-005` | Core/Record Ownership | P0 | **NONE** | CORE-013, SQL-011 |
| `REQ-READ-001` | Record Read / Catch-up | P0 | **NONE** | READ-001, READ-002, PROP-010, CON-011 |
| `REQ-OBS-008` | Observation Referential Integrity | P0 | **NONE** | OBS-015, OBS-016 |
| `REQ-OBS-009` | Observation Capture Ordering | P0 | **NONE** | OBS-017, OBS-018 |
| `REQ-REL-007` | Release Evidence Integrity | P0 | **NONE** | REL-013, REL-014 |
| `REQ-CANON-001` | Canonical JSON Domain | P0 | **NONE** | SCH-014, SCH-015, PROP-011 |
| `REQ-BRIDGE-013` | Session Mapping Durability | P0 | **NONE** | BRG-018 |
| `REQ-BRIDGE-014` | Bridge Fencing Scope | P0 | **NONE** | MP-004, FLT-014, E2E-012 |
| `REQ-BRIDGE-015` | Terminal Callback Consistency | P0 | **NONE** | BRG-014, BRG-015, BRG-019 |
| `REQ-CERTAINTY-002` | Execution Certainty Recovery | P1 | **NONE** | CERT-001, CERT-002, CERT-003 |
