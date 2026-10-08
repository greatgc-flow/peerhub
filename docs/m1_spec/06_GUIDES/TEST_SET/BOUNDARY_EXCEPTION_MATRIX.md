# Boundary / Exception Matrix

> SSOT: `EXCEPTION_CATALOG.json`

> Total: **89** / OPEN: **0**

| ID | Category | Case | Status | Tests / Trigger |
|---|---|---|---|---|
| EX-001 | Input/Schema | missing required field | **COVERED** | SCH-011 |
| EX-002 | Input/Schema | null in non-null field | **COVERED** | SCH-011, SCH-012 |
| EX-003 | Input/Schema | empty identifier | **COVERED** | SCH-002, SCH-011 |
| EX-004 | Input/Schema | unknown property | **COVERED** | SCH-002, SCH-013 |
| EX-005 | Input/Schema | unsupported schema version | **COVERED** | SCH-011, MIG-003 |
| EX-006 | Input/Schema | malformed date/digest/enum | **COVERED** | SCH-013 |
| EX-007 | Input/Schema | duplicate members/targets | **COVERED** | SCH-003, SCH-005, STR-005 |
| EX-008 | Input/Schema | null versus empty arrays | **COVERED** | SCH-012, STR-005 |
| EX-009 | Input/Encoding | Unicode/newline/combining characters | **COVERED** | PROP-007 |
| EX-010 | Input/Encoding | SQL/control-like text | **COVERED** | SEC-001, PROP-007 |
| EX-011 | Input/Encoding | large valid body within test envelope | **COVERED** | PROP-009, SOAK-001 |
| EX-020 | State | append to CLOSED Stream | **COVERED** | STR-003 |
| EX-021 | State | stale Stream revision | **COVERED** | STR-002, STR-004 |
| EX-022 | State | Offset rewind | **COVERED** | OFF-004 |
| EX-023 | State | Offset past head | **COVERED** | OFF-006, CON-010 |
| EX-024 | State | Offset nonexistent peer/stream | **COVERED** | OFF-008 |
| EX-025 | State | reopen CLOSED Stream | **DEFERRED** | Introduce explicit reopen API/control. |
| EX-030 | Concurrency | same Stream concurrent append | **COVERED** | CON-001, MP-001 |
| EX-031 | Concurrency | same idempotency key same payload | **COVERED** | CON-003, MP-002 |
| EX-032 | Concurrency | same idempotency key different payload | **COVERED** | CON-004 |
| EX-033 | Concurrency | Offset CAS race | **COVERED** | CON-005, MP-003 |
| EX-034 | Concurrency | Bridge claim race | **COVERED** | CON-006 |
| EX-035 | Concurrency | claim expiry/takeover race | **COVERED** | CON-007, CLM-003 |
| EX-036 | Concurrency | duplicate terminal callback | **COVERED** | BRG-014 |
| EX-040 | Crash | before append commit | **COVERED** | FLT-001 |
| EX-041 | Crash | after commit before client success | **COVERED** | FLT-002, E2E-002 |
| EX-042 | Crash | after runtime start before terminal | **COVERED** | FLT-003, FLT-006, BRG-013 |
| EX-043 | Crash | after terminal before Offset ack | **COVERED** | FLT-005, BRG-015 |
| EX-044 | Storage | SQLite busy/locked | **COVERED** | FLT-009 |
| EX-045 | Storage | disk full/write failure | **COVERED** | FLT-011 |
| EX-046 | Storage | read-only filesystem | **COVERED** | FLT-012 |
| EX-047 | Storage | database corruption | **COVERED** | FLT-013 |
| EX-048 | Storage | process killed exactly during OS fsync hardware failure | **OUT_OF_SCOPE** | Add hardware/power-cut lab before claiming power-loss certification. |
| EX-050 | Runtime | pre-spawn failure | **COVERED** | BRG-011 |
| EX-051 | Runtime | timeout after start | **COVERED** | BRG-012 |
| EX-052 | Runtime | partial output then crash | **COVERED** | BRG-013 |
| EX-053 | Runtime | resume rejected | **COVERED** | BRG-016 |
| EX-054 | Runtime | all session acquisition fails | **COVERED** | BRG-017 |
| EX-055 | Runtime | unsupported interrupt | **COVERED** | BRG-009 |
| EX-056 | Runtime | terminate/kill failure | **COVERED** | CTL-002 |
| EX-057 | Runtime | provider-specific tool protocol mutation | **DEFERRED** | Add when a provider RuntimeTarget exposes tool-call mutation semantics. |
| EX-060 | Time | lease renewal just before expiry | **COVERED** | CLM-001 |
| EX-061 | Time | takeover just before expiry | **COVERED** | CLM-002 |
| EX-062 | Time | exact lease expiry | **COVERED** | CLM-003 |
| EX-063 | Time | exact observation TTL boundary | **COVERED** | OBS-009 |
| EX-064 | Time | source clock far future/skew | **COVERED** | OBS-010, OBS-011 |
| EX-065 | Observation | probe timeout/malformed | **COVERED** | FLT-010, OBS-012 |
| EX-066 | Observation | quota exhausted | **COVERED** | OBS-013 |
| EX-067 | Observation | rate limited | **COVERED** | OBS-014 |
| EX-070 | Diag | malformed observation section | **COVERED** | DIA-005 |
| EX-071 | Diag | authoritative DB read failure | **COVERED** | DIA-006, FLT-013 |
| EX-072 | Diag | optional log unreadable | **COVERED** | DIA-009 |
| EX-073 | Diag | concurrent writer during report | **COVERED** | DIA-008 |
| EX-074 | Diag | stale evidence tempts refresh | **COVERED** | DIA-010 |
| EX-080 | Migration | interrupted schema migration | **COVERED** | MIG-001 |
| EX-081 | Migration | supported upgrade | **COVERED** | MIG-002 |
| EX-082 | Migration | future major/unsupported downgrade | **COVERED** | MIG-003 |
| EX-086 | Release | artifact missing runtime data | **COVERED** | REL-008, REL-009 |
| EX-087 | Release | undeclared dev dependency | **COVERED** | REL-009 |
| EX-088 | Release | declared Python/OS not tested | **COVERED** | REL-010, REL-011 |
| EX-089 | Release | provider auth/quota unavailable | **COVERED** | LIVE-004, REL-006 |
| EX-100 | Resource | very large Stream/cardinality | **COVERED** | SOAK-001, SOAK-002 |
| EX-101 | Resource | true process OOM by host exhaustion | **OUT_OF_SCOPE** | Add isolated chaos/soak environment if production SLO requires OOM behavior certification. |
| EX-102 | Security | credential/secret redaction in arbitrary Observation payload | **DEFERRED** | Define sensitivity metadata/redaction policy before exposing Diag/evidence outside trusted local boundary. |
| EX-103 | Security | malicious SQL-like text | **COVERED** | SEC-001 |
| EX-012 | Input/Integrity | caller injects server-owned Record fields | **COVERED** | CORE-013, SQL-011 |
| EX-013 | Idempotency | same key reused in another Stream/author scope | **COVERED** | IDEM-001, IDEM-002, IDEM-003 |
| EX-026 | Read/Catch-up | empty/head/page boundary | **COVERED** | READ-001, READ-002, PROP-010 |
| EX-027 | Read/Catch-up | append occurs between pages | **COVERED** | CON-011 |
| EX-068 | Observation | unknown resource pool reference | **COVERED** | OBS-015 |
| EX-069 | Observation | captured_at missing | **COVERED** | OBS-017 |
| EX-069B | Observation | equal local capture timestamps | **COVERED** | OBS-018 |
| EX-091 | Release | manifest-covered file tampered | **COVERED** | REL-013 |
| EX-092 | Release | validator screen/evidence drift | **COVERED** | REL-014 |
| EX-014 | Input/JSON | bytes/set/custom runtime object in Record payload | **COVERED** | SCH-014 |
| EX-015 | Input/JSON | NaN/Infinity non-finite number | **COVERED** | SCH-015 |
| EX-058 | Runtime | session mapping after process restart | **COVERED** | BRG-018 |
| EX-059 | Runtime | stale owner receives late terminal after takeover | **COVERED** | FLT-014 |
| EX-059B | Runtime | two Bridge processes recover same unread Record | **COVERED** | E2E-012, MP-004 |
| EX-059C | Runtime | conflicting duplicate terminal callbacks | **COVERED** | BRG-019 |
| EX-104 | Security/Resource | arbitrarily deep JSON nesting / parser exhaustion | **DEFERRED** | Define explicit payload-size/depth resource policy before accepting untrusted remote JSON boundary. |
| EX-105 | Execution Certainty | MAY_HAVE_STARTED requires explicit retry reconciliation | **COVERED** | CERT-001, CERT-002, CERT-003 |
| EX-106 | Record References | reply_to missing/cross-Stream reference semantics | **DEFERRED** | Ratify before UI/thread semantics depend on reply_to integrity. |
| EX-107 | Record Targets | target Peer exists but is not a current Stream member | **DEFERRED** | Define delivery authorization/membership semantics before enforcing target membership. |
| EX-108 | Identifier Grammar | whitespace-only or Unicode-normalization-equivalent identifiers | **DEFERRED** | Ratify generated/user-supplied identifier grammar before remote/public API exposure. |
