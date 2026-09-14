# New-Track Fixture ID Assignment R1

**Status:** Proposed Phase 0 design addition — design-only. Assigns fixture
IDs and classifies verification mechanism; does not itself script, execute,
or capture any fixture, and does not expand `fixture-status-v1.json`'s
54-ID behavioral set until a ratified round does so explicitly.

**Scope:** The pre-TDD fixture lists already declared in
`QUOTA-PERIOD-SCALING-POLICY-R1.md` §5 (9 scenarios),
`UNIFIED-SETTINGS-SURFACE-R1.md` §6 (7 scenarios), and
`EXECUTABLE-DISCOVERY-AND-PORTABLE-REFERENCES-R1.md` §10 (12 scenarios,
not 11 — see §4) had no fixture IDs or verification-mechanism
classification. This document supplies both, reusing the
`DomainOracle`/`DomainSubjectAdapter` pattern from
`DOMAIN-ORACLE-VERIFIER-CONTRACT-R1.md` where a scenario needs it.

Prefixes: `QP` (quota pacing), `ST` (settings surface), `ED` (executable
discovery/portable references).

## 1. Quota pacing (QP-01..09)

| ID | Mechanism | Raw inputs → independently computed check |
|---|---|---|
| QP-01 | `DomainOracle` | Window boundaries, `now`, measured fraction, terminal rule `{target_fraction:1.0, effective_period_days:6.25}` → oracle computes `target_at`/progress/planned fraction/delta/assessment; required vectors at 50% and 100% of the effective period. |
| QP-02 | `DomainOracle` | Same shape, `NON_TERMINAL`/`0.8` → required vectors at 40% and 80% of the effective period, then automatic hold (not progression toward 100%). |
| QP-03 | `DomainOracle` | Evidence timestamps + freshness limit + presence flags → `PACING_EVIDENCE_UNAVAILABLE`; never assumed zero usage or unlimited quota. |
| QP-04 | `DomainOracle` — **spec gap, see §3** | Prior/current window key+start+reset values → recompute against the new boundary. |
| QP-05 | `DomainOracle` | Unordered rules/evidence for several `(quota_pool_id, usage_window_key)` pairs → independently keyed assessments, invariant under input order, no cross-window bleed. |
| QP-06 | `DomainOracle` — **error code not yet frozen, see §3** | Two rules resolving to one physical pool with incompatible roles/targets → rejection with conflict identities, zero `RoutingPolicy` revision change. |
| QP-07 | `DomainOracle` | Frozen planning revision vs. current routing-policy revision, dispatch count → `CONFIGURATION_STALE`, zero dispatch, current revision returned for replanning. |
| QP-08 | `DomainOracle` | Two independent homes sharing one physical account pool, each with locally admissible evidence → explicit `UNRESOLVED_CROSS_HOME_AUTHORITY`. **Not exit-eligible even if this fixture passes** — it captures the documented limitation (`QUOTA-PERIOD-SCALING-POLICY-R1.md` §4), not a solved coordination mechanism. |
| QP-09 | `DomainOracle` | Every pool's assessment/role/family/measured fraction → exactly one terminal-only bypass + one `PACING_UNESCAPE_EMERGENCY` record when all pools are throttled and terminal usage `< 1.0`; negative vectors (an unthrottled pool, or terminal usage `>= 1.0`) must produce no bypass. |

## 2. Settings surface (ST-01..07)

A lightweight isolated in-memory owner-store adapter is sufficient here —
none of these need SQLite transaction evidence (unlike `GB-01`).

| ID | Mechanism | Raw inputs → independently computed check |
|---|---|---|
| ST-01 | `DomainSubjectAdapter` | Descriptor + canonical-owner initial state/revision + ordered create/read/update/conflicting-update ops → successful ops advance only the owner revision; stale CAS is rejected with no mutation. |
| ST-02 | `DomainSubjectAdapter` | Initial state + repeated key/payload ops → first write mutates once; identical retry returns the original receipt; changed payload returns exactly `IDEMPOTENCY_PAYLOAD_MISMATCH`. |
| ST-03 | `DomainOracle` — **exact redacted representation not yet frozen, see §3** | Descriptor sensitivity + actor authorization + secret value → list/read representations must contain no secret bytes. |
| ST-04 | `DomainOracle` | Descriptor owner/subpath + all candidate owner stores + a write → exactly the named aggregate/subpath changes; façade-owned value rows stay at zero; reads resolve from that owner. |
| ST-05 | `DomainSubjectAdapter` — **cursor-gap behavior not yet frozen, see §3** | Immutable effect stream + prior cursor + retained range + configured/effective snapshot → deterministic recovery, gap indication, resumed cursor, no duplicated changes. |
| ST-06 | `DomainOracle` | Frozen dispatch revision vs. current owner revision + pre-effect counters → `CONFIGURATION_STALE`, zero effects, replanning input references the current revision. |
| ST-07 | `DomainSubjectAdapter` — **required-delete error code not yet frozen, see §3** | Required vs. optional descriptors + current values + versioned defaults → optional deletion exposes the built-in default with a recorded origin; required deletion is rejected without a revision change. |

## 3. Executable discovery and portable references (ED-01..11)

The source document (§10) actually lists 12 distinct requirements, not 11
— two of them (`subst` alias dedup, other same-physical-identity aliases)
are combined into `ED-04` as two required vectors of one fixture, matching
this corpus's existing precedent of one fixture ID covering multiple named
sub-cases (e.g. the legacy `DP-02`/`DP-03` shared-transcript pattern). This
keeps the total at 11 IDs without dropping coverage; a future round may
split them if that turns out to lose fidelity.

A controlled fake filesystem/namespace-graph subject adapter is used
throughout; the oracle independently computes from raw nodes, edges,
identities, revisions, and custody capabilities. Scripts must never supply
candidate acceptance, selected paths, errors, or spawn decisions directly.

| ID | Mechanism | Raw inputs → independently computed check |
|---|---|---|
| ED-01 | `DomainSubjectAdapter` | Identical sealed discovery contexts, different caller CWDs → byte-identical candidate sets/order/identities/digests regardless of CWD. |
| ED-02 | `DomainSubjectAdapter` | PATH with empty/relative/absolute entries + candidates under CWD → invalid entries ignored, CWD candidates absent, only absolute-entry candidates appear. |
| ED-03 | `DomainOracle` | Unchanged symbolic reference/config revision + two trusted anchor bindings naming the same tree under different absolute paths → unchanged config bytes/revision, distinct resolved paths, new binding/readiness evidence, zero config rewrites. |
| ED-04 | `DomainSubjectAdapter` | Two vectors: a `subst` alias, and a non-`subst` alias (case/short-name/drive alias) both resolving to one physical file identity → exactly one candidate + full alias/provenance evidence, independent of path text. |
| ED-05 | `DomainOracle` | One `anchor_id` bound to two distinct physical directories → `EXECUTABLE_ANCHOR_AMBIGUOUS`, no candidate selection/resolution/config write/readiness/spawn. |
| ED-06 | `DomainSubjectAdapter` | Trusted anchor + a junction/symlink graph whose final target lies outside it → `EXECUTABLE_REFERENCE_ESCAPES_ANCHOR` after physical canonicalization, never accepted on textual prefix alone. |
| ED-07 | `DomainOracle` | Captured candidate identity/digest vs. changed current identity/digest → `DISCOVERY_CANDIDATE_STALE`, zero `PeerInstanceConfig` writes. |
| ED-08 | `DomainOracle` | Frozen binding vs. mismatching immediate-pre-spawn identity/digest/config revision/anchor revision → `EXECUTABLE_BINDING_STALE`, zero spawn. |
| ED-09 | `DomainSubjectAdapter` | Successful pre-spawn validation + a replacement transition + acquisition capability/custody facts → if same-image custody is unavailable, `EXECUTABLE_IDENTITY_UNPROVABLE` and zero spawn; the adapter must not reopen the validated pathname and launch its replacement. |
| ED-10 | `DomainSubjectAdapter` | Stable wrapper identity + changed interpreter/target identity → stale/unprovable binding and zero spawn even though the wrapper's own digest is unchanged. |
| ED-11 | Hybrid: `runner.py` lifecycle **+** `DomainOracle` | Runner records `SPAWNED`/terminal events; domain inputs supply old/new anchor revisions + frozen binding → the in-flight attempt keeps its original binding, receives no kill/replay, and a subsequent dispatch requires new resolution/readiness. |

Two further evidence-boundary notes, not blockers: `ED-04`/`ED-06` verify
abstract identity/reparse *rules* pre-TDD but cannot prove actual Windows
`subst`/junction/filesystem-handle behavior — that requires later
platform-specific empirical evidence, same caveat as `ED-09`'s custody
question.

## 4. Items that must be frozen before scripting (not decided here)

Consistent with `RT-05`'s treatment in
`DOMAIN-ORACLE-VERIFIER-CONTRACT-R1.md` §6, the following are real,
previously-unnoticed spec gaps, not filled in with invented specifics:

- **QP-04**: whether a changed reset boundary under the same window key is
  inconsistent evidence, an in-place recalculation, or a new logical
  window. Proposed default: fail-closed inconsistency unless the provider
  supplies a new window identity — consistent with this corpus's standing
  "never guess, fail closed" pattern.
- **QP-01/02/09**: the exact assessment-classification rule — equality/
  tolerance handling and the boundary between `ON_TRACK`,
  `AHEAD_OF_TARGET`, and `TARGET_HELD`.
- **QP-06**: the exact machine error code for a shared-pool role conflict.
  Proposed: `QUOTA_POOL_ROLE_CONFLICT`.
- **ST-03**: the exact redacted-value representation and whether
  visibility is privilege-dependent.
- **ST-05**: whether an expired/out-of-range cursor returns an error, a
  full snapshot, or a snapshot-plus-tail.
- **ST-07**: the exact error code for rejecting deletion of a required
  setting.

None of these block *this* document (ID assignment and mechanism
classification); they block writing the actual event scripts and oracle
code for the affected IDs, the same way `RT-05` blocks its own scripting
until its formula is ratified.

## 5. Consequence for the task plan

Writing real, runnable event scripts and producing genuine `V1_CAPTURE`
evidence for any `DomainOracle`/`DomainSubjectAdapter` fixture — all 26 of
these 27 IDs, plus the 16 pre-existing `NARROW_COVERAGE` IDs — requires the
oracle/adapter framework from `DOMAIN-ORACLE-VERIFIER-CONTRACT-R1.md` to
actually exist as code first. That implementation is not yet scheduled.
Only `ED-11`'s lifecycle half could run against the existing runner today;
its domain half cannot, so no ID in this document can be fully captured
yet.

## 6. Provenance

Drafted independently by `ag.deepthink` and `cx.deepthink`. `cx`'s
treatment was adopted throughout: it correctly kept `QP-08` as a real,
scriptable fixture whose expected outcome is an explicit unresolved-
authority marker — matching what `QUOTA-PERIOD-SCALING-POLICY-R1.md` §5
itself already required ("documented as unresolved, not silently
passing") — where `ag`'s draft dropped it as out-of-scope entirely. `cx`
also caught that the executable-discovery source document lists 12
requirements, not 11, and flagged six real spec gaps (§4) that `ag`'s
draft filled with confident, unratified specifics instead of flagging.
`ag`'s IDs, prefix convention, and DomainOracle/DomainSubjectAdapter
mechanism assignments were otherwise consistent with `cx`'s and are not
separately re-litigated here.
