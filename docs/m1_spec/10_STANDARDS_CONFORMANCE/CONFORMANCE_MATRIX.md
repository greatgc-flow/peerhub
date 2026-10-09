> `COMPLY/ADAPT/...` is target architecture conformance. For current implementation maturity, see `product-maturity.json`.

# Unified Standard Conformance

| Control | Status | Implementation | Rationale |
|---|---|---|---|
| UPS-CLI | **ADAPT** | CLI root peerhub; domain noun/verb is primary; diag/status are first-class read views. | Communication domains benefit from noun/verb hierarchy; no rename solely for Engram symmetry. |
| UPS-HELP | **COMPLY** | Public help must be side-effect free and generated/drift-tested from the command contract. | Matches common discoverability rule. |
| UPS-EXIT | **ADAPT** | Publish a PeerHub semantic exit map; preserve existing numeric behavior until a compatibility-safe migration. | Breaking automation for numeric symmetry has low value. |
| UPS-CONFIG | **COMPLY** | Declarative config/catalog/policy with explicit precedence; runtime/model facts remain structured. | Matches no-hardcoding and UNKNOWN-preservation principles. |
| UPS-LAYOUT | **ADAPT** | Core authoritative SQLite/records separated from extension projections, logs, evidence and catalogs. | Physical layout can remain PeerHub-specific. |
| UPS-SCHEMA | **COMPLY** | JSON Schema 2020-12 contracts; versioned Core/Extension schemas and transactional migration. | Already aligned. |
| UPS-COMP | **COMPLY** | Core=Peer/Stream/Record/Offset; M1 first-party modules; M2+ generic extensions/adapters. | Dependency direction is the architecture foundation. |
| UPS-EXT | **ADAPT** | M1 Session Bridge/Observation/Diag are statically assembled first-party modules; M2.0 introduces generic Extension Host semantics. | This resolves the apparent M1-extension vs M2-extension-runtime contradiction. |
| UPS-OBS | **COMPLY** | Observation owns quota/rate/runtime evidence; Diag is strict read-only; action/recovery is separate. | Exact common pattern. |
| UPS-EVID | **COMPLY** | Record/evidence/source identity, release evidence and gate freshness are explicit. | Matches common contract. |
| UPS-SEC | **COMPLY** | Fencing/idempotency/readonly diag/public-port-only extension writes/fail-closed storage. | Matches trust-boundary contract. |
| UPS-MIG | **COMPLY** | The retired v0 command surface was removed (2026-10-08); historical lesson/directive/task/governance names do not force old domains to return. | Single current CLI. |
| UPS-TEST | **COMPLY** | 85 requirements/207 tests plus state/exception/interaction/fault/meta closure. | Stronger than common baseline. |
| UPS-REL | **COMPLY** | Candidate-bound gates, live/package gates and evidence freshness policy. | Matches common release assurance. |
| UPS-DOC | **COMPLY** | Machine SSOT plus generated/views and validator drift checks. | Matches common SSOT rule. |

# Maturity & Implementation Status

- **CLI**: The only CLI is peerhub (side-by-side peerhub-m1 and legacy-import/109 table are gone).
- **M1**: Implementation status keeps its old claim but labelled historical (bound to SHA 4a6994e).
- **M2 and M3**: IMPLEMENTED, awaiting candidate-bound release verification. Verification claims should reference release evidence (	ools/release_evidence.py output) instead of asserting PASS.
