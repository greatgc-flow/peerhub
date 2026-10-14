# PeerHub Pre-TDD Renewal and Architecture Synthesis (2026-10-14)

**Document Path:** `docs/design/peerhub-pre-tdd-renewal-2026-10-14.md`
**Status:** PROPOSED Architecture Specification & Pre-TDD Contracts (ag/cx/cc gave GO for pre-TDD design only; implementation, owner-choice and empirical gates remain)
**Stream & Author Provenance:** The initial synthesis was authored by **Gemini 3.8 Flash High** on a new ag stream after the original Sonnet stream could not continue. cx and cc supplied separate analyses and reviews on their recorded streams. These streams do not share native model continuity. Repository claims were checked against source where stated; reviewer assertions and unresolved claims are identified separately below.
**Owner Authorization:** The owner explicitly authorized reconsidering implemented, frozen, or planned designs and requested completion through the pre-TDD boundary. This document proposes that redesign; it does not claim implementation authorization.

No repository implementation changes, test execution, production-peer invocation, dependency changes, migration, or release occurred during this design review. AI peer review calls did occur.

---

## 1. Executive Current-vs-Proposed Architecture

PeerHub's core architectural mission is durable, governed multi-turn AI peer coordination. This renewal resolves critical operational bottlenecks identified during the October 2026 deepthink debate across the ag, cx, and cc streams.

```
+----------------------------------------------------------------------------------------------------+
|                                      PEERHUB CORE (CURRENT CONTRACT)                                 |
|  Primitives: Peer, Stream, Record, Offset                                                          |
|  Invariants: Sequential record position, single-writer append, CAS offset acknowledgement           |
|  (Retained by engineering justification of domain minimalism, not by owner prohibition)            |
+----------------------------------------------------------------------------------------------------+
                                                  │
                                                  ▼
+----------------------------------------------------------------------------------------------------+
|                                 STORAGE COMPATIBILITY EPOCH FENCE                                  |
|  Unified store-format guard checking user_version (proposed next format: 4)                         |
|  Causes old v0.15.2 binaries to fail fast with exit code 6 before executing any DDL or writes      |
+----------------------------------------------------------------------------------------------------+
          │                                       │                                       │
          ▼                                       ▼                                       ▼
+-----------------------+               +-----------------------+               +--------------------+
|    SESSION REUSE      |               |     MONITORING V2     |               |  STUCK STREAMS &   |
|     (EXTENSIONS)      |               |     (DIAGNOSTICS)     |               |     RECOVERY       |
| • Compatible reuse    |               | • SnapshotContext     |               | • Audited stream   |
| • Caller-intent replay|               |   (owns ro connection)|               |   resolve (PR6)    |
| • Immutable keyset    |               | • Modular readers     |               | • Preserved STARTED|
| • Tri-part budgets    |               | • Pure alert fold     |   certainty        |
| • Self-response omit  |               | • 150ms target watch  | • Fenced recovery  |
| • Single receipt auth |               | • Strict NDJSON export| • Manual abandon   |
+-----------------------+               +-----------------------+               +--------------------+
```

### Key Differences: As-Is vs. Proposed

This table records the baseline at design time. The CLI/monitoring slice later
implemented bounded `session list/show`, shared snapshot output and cadence;
see the [implementation addendum](peerhub-cli-monitoring-inspection-2026-10-14.md)
and [current status](../STATUS.md) for the live source state. In particular,
the Session Inspection and Monitoring V2 rows below are historical baselines,
not remaining-work claims. Core record totals and legacy observation fallback
still scan history.

| Domain | As-Is Implemented / Planned Contract | Proposed Reconciled Architecture | Rationale & Counterexample |
|---|---|---|---|
| **Core Domain** | Current M1 primitives are `Peer`, `Stream`, `Record`, `Offset`; claims and fencing are extensions. | Retain this boundary unless a concrete invariant requires changing it; this is a reviewed design choice, not an owner-imposed freeze. | Extension ownership reduces domain coupling today; a necessary invariant can justify revisiting the boundary with migration and compatibility evidence. |
| **Store Compatibility** | HEAD and tag v0.15.2 both support `user_version=3`; the selected live DB is also version 3. | A genuinely incompatible format uses version 4 and one atomic migration. Older binaries refuse it only if their guard checks the supported version; verify that with the actual v0.15.2 executable before cutover. | A bump to 2 is unsafe: it is already supported and would reject current stores. Stop all old writers, back up, migrate using one connection, stamp 4 last, and verify both migration rollback and the real old binary's pre-write refusal. |
| **Uncertainty & Offset** | Uncertain delivery freezes stream (`blocked_uncertain`); unacked delivery blocks cursor. | **Retain offset stream blocking; provide explicit audited `stream resolve`**. Taint session handle; after resolution, default ask follows ONE deterministic rule: select latest compatible non-LOST/non-tainted handle, or fresh with reason `prior_abandoned` if none exists. `--fresh` cannot bypass unresolved delivery. Disposed prompts are excluded or marked non-actionable in catch-up without re-execution. | Advancing offset past uncertain prompt $N$ orphans request; re-running later causes duplicate side effects. |
| **Output Budgets** | CLI default `max_output_bytes` is 1,000,000; process default `max_bytes` is 1,048,576. Cap truncates and drains; does NOT kill process. (64KB was an explicit health ping override). | **Tri-part output budget:** (1) Internal frame/buffer bounds to prevent memory DoS, (2) One public answer cap (default 1MB; tunable recommendation) applied to parsed text only, (3) Diagnostic tail buffer (last 16KB). No total drain kill cap! | Draining must continue under deadline. Verbose tool loops emitting 15MB of JSON-RPC chatter followed by 200-byte answer must not be killed. |
| **Process Supervision** | `BoundedProcess.wait()` times out 5s joining drain threads, then calls `BufferedReader.close()`. | **Non-blocking controller cleanup:** One absolute cleanup allowance; pump threads detach safely; no unsafe cross-thread `os.close` calls. Eventual cleanup verified via supervisor. | If grandchild inherits stdout, pump thread blocks in `read1()`; calling `s.close()` deadlocks on CPython buffer lock. |
| **Diagnostic Reader** | `ReadonlyDiag.render()` runs whole-store `PRAGMA quick_check` and an unbounded observation query. Health report measured 330 observations at 45.64–92.81ms; causality and broader percentile claims are unproven. | **Proposed:** one owned read-only/query-only snapshot; indexed bounded readers; separate SQLite `quick_check` from application receipt/counter recount. Targets are budgets to measure, not current performance results. | Full scans can scale with history. A snapshot avoids mixed-time sections; each section reports partial failure independently. |
| **Catch-Up & Delta** | `_native_delta` requests the stream tail and `catchup.py` accumulates redirect pins; no occupancy or production memory profile was measured in this review. | **Proposed:** bound candidate rows, body bytes and pins independently; reject before provider invocation when incomplete. Specify full native delta and fresh suffix-plus-pins separately. | Unbounded work can grow with history; the 30k-record/hundreds-of-megabytes scenario is a test fixture, not a measured incident. |
| **Session Inspection** | No shipped `session list/show` inspection or pagination surface. Internal session selection exists; user-visible history occupancy and pagination behavior are not established. | **Proposed:** bounded inspection with an immutable cursor bound to store, stream, peer, lineage, filter and insertion visibility, over one read-only snapshot. | Do not describe a future inspection contract as current behavior. Immutable cursor fixtures must cover concurrent completion and filtered traversal. |
| **Telemetry Truth** | Current receipts report unknown occupancy; session inspection/pagination is unimplemented. | **Proposed:** preserve independently attributable token values when window is unknown; do not derive a ratio without a known window; retain >100% as an anomaly only when its source is attributable. One versioned existing `bridge_evidence.kind=session_telemetry` receipt remains authoritative. Anomaly ratio proves no vendor overflow; does not assert actual occupancy. | Unknown fields must not erase known fields, and synthetic ratios must not be reported as verified vendor occupancy. Clamping >100% ratios is prohibited. |
| **Caller Intent & Replay** | Replay resolves current binding/fingerprint before returning cached result. Replay key is `(stream_id, peer_id, request_id)`. | **Persist caller intent separately from resolved configuration; compare intent first.** Intent hash detects conflicts, never used as prompt-dedup key. | Changing a default profile TOML prevents replay of identical earlier prompt requests. |
| **Retention & Expiry** | Arbitrary 30-day inactivity expiration drops sessions; unstarted allocations consume slots. | **Prune by deterministic count cap ($\le 20$); unstarted allocation failures do not evict established sessions; 30-day expiry is a proposed choice.** Retention strictly protects handles with active or unresolved deliveries. Redact vendor and external session IDs. | 30 days is an arbitrary software timer unrelated to vendor context caching; unstarted allocation errors evict valid sessions; unresolved deliveries must never be evicted. |

---

## 2. Verified Done / Pending / Obsolete Ledger

| Track | Shipped & Committed (`cfb28aba`) | Pending TDD Implementation | Obsolete / Dropped Requirements |
|---|---|---|---|
| **Session Reuse** | • PR1: Default current-slot reuse, `--resume` removed (`eec2cba4`).<br>• PR2: Registry, receipts, projections (`7cdc6b76`).<br>• PR3: Fresh/exact selectors (`12129517`).<br>• Health: One-time seeding skip, indexed evidence (`cfb28aba`). | • SR-PR4a: Bounded session inspection (`session list/show/diag`).<br>• SR-PR4b: Attributable numeric telemetry receipts in `bridge_evidence`.<br>• SR-PR5: Reduced context alerts (`CONTEXT_CRITICAL`). | • Standalone `bridge_retention.py` / `session_telemetry.py` modules (retention lives in `bridge_registry.py`, receipts in `session_receipts.py`).<br>• Proactive liveness probes.<br>• REUSE_UNUSED alert kind (unsupported by operational demand). |
| **Monitoring V2** | None on `main` (NO_COLOR env check exists; `--collect-every` and `diag --live` exist). | • MV2-1: Terminal styling, escapes, and color matrix.<br>• MV2-2: `SnapshotContext` and bounded section readers.<br>• MV2-3: Monotonic cadence, per-provider backoff, agy token guard.<br>• MV2-4: Pace balance math (R1 preserved) and single-line ASCII output.<br>• MV2-5: Preserve `diag --live`; make collection explicit in monitor help/output and show the committed snapshot before refresh.<br>• MV2-6: Strict NDJSON export allowlist. | • `monitor --read-only` and removal of `diag --live` (superseded; see [CLI/Monitoring Addendum](peerhub-cli-monitoring-inspection-2026-10-14.md)).<br>• `monitor --once` removal (already absent from codebase).<br>• Complex plugin registry for sections (explicit functions preferred).<br>• Whole-store `quick_check` during routine snapshots. |
| **Stuck Streams** | • PR1: Process tree termination confirmation (`#95`).<br>• PR2: Output drain and truncate (`#97`).<br>• PR3: Provider refusal classification as `NOT_EXECUTED` (`#98`). | • Stuck PR6: Audited `stream resolve` command and historical recovery.<br>• Enforcement provenance metadata in invocation markers. | • Stuck PR4: Quota pre-flight hard admission blocking (DROPPED: contradicts inform-only rule).<br>• Stuck PR5: Automatic timeout settlement (DEFERRED: lacks containment proof). |
| **Audit & Tooling** | • WP-A: Repo root hygiene and command inventory (`#91`). | • WP-B: CLI learnability and actionable error messages.<br>• WP-C: TOML configuration SSOT and removal of implicit `./peerhub` lookup.<br>• WP-D: Process supervisor deadlines and non-blocking cleanup.<br>• WP-E: Test skip prerequisites and orphan traceability. | • Speculative binary resolution guesses in `binary_resolution.py`.<br>• Unversioned dirty live-gate candidate stamping. |

---

## 3. Coherent Prerequisite DAG and Implementation Slices

The implementation DAG removes artificial cross-track coupling. Branches are independently executable; arrows below are required contracts, not release-order mandates:

```text
R0: Truthful Docs Update (STATUS, RETAINED_SESSIONS, etc.)
  │
  ├─► Branch 1: Storage & Discovery
  │     ├─► Slice B: Store-Format user_version Bump & Idempotent Trigger DDL
  │     └─► Slice A: Runtime Provenance & Strict Store Discovery (O-1 cwd/peerhub removal)
  │
  ├─► Branch 2: Execution & Supervision
  │     ├─► Slice P: TOML Config Authority & Process Resolver (WP-C)
  │     └─► Slice D: Deadlines, Pipe Non-Blocking Drain, Verified Tree Kill (WP-D)
  │           └─► Slice Stuck6: Audited Stream Resolution (peerhub stream resolve)
  │
  ├─► Branch 3: Session Continuity
  │     └─► Slice C: Bounded Catch-Up, Delta Paging, Self-Response Omission
  │
  ├─► Branch 4: Diagnostic Snapshot & Readers
  │     └─► SnapshotContext ──► bounded diagnostic readers ──► NDJSON export
  │            ├─► Session inspection (store selection + query + cursor only)
  │            └─► Read-only watch (snapshot + lifecycle; TTY styling when applicable)
  │
  ├─► Branch 5: Monitoring Collection & Cadence (independent of read-only inspection/watch)
  │     └─► Slice MV2-3: Monotonic Cadence ──► Per-Provider Backoff & Agy Token Guard
  │
  ├─► Branch 6: Attributable Telemetry & Alerts
  │     └─► Slice SR4b: Attributable Numeric Telemetry Receipts (bridge_evidence)
  │           └─► Slice SR5: Reduced Context Alerts (CONTEXT_CRITICAL; no inspection UI dependency)
  │
  └─► Branch 7: Tooling & Release Qualification (WP-E, Candidate Gates)
```

### Independent Slice Execution Requirements & Gates

Basic session inspection needs only store selection, the inspection query and its pagination contract; it does not depend on quota, color, telemetry, or probe collection. Read-only watch depends on the snapshot reader and terminal lifecycle only when rendering a TTY. Collecting watch separately depends on collection cadence and per-provider backoff. Export depends on snapshot readers. Telemetry alerts depend on attributable telemetry, not the inspection UI. Every slice below names its own first RED and stop gate; dependency edges are limited to these shared prerequisites.

| Slice ID | Title & Scope | Primary Owner / Reviewer | Entry Evidence | FIRST RED Test Contract | Stopping / Veto Gate |
|---|---|---|---|---|---|
| **Slice B** | Store-Format `user_version` 3→4 & Atomic Migration | cx / ag | Actual 0.15.2 binary and version-3 DB fixture; sealed MIG clauses | `PH-T001` (actual 0.15.2 refuses migrated v4 before writes) | One-connection migration, stop old writers, backup, stamp 4 last; sealed spec revision and rollback gates. |
| **Slice A** | Runtime Provenance & Strict Store Discovery | ag / cx | Workspace path fixture | `PH-T006` (Explicit `--db` overrides `PEERHUB_DB`) | Precedence documented; parent-walk removal gated as owner choice. |
| **Slice P** | TOML Authority & Resolver Metacharacter Hardening | ag / cx | Packaged JSON, workspace TOML | `PH-T011` (TOML overrides JSON defaults) | Zero speculative fallback to arbitrary path binaries. |
| **Slice M** | Model/Profile Verification | ag / cx | Packaged manifest, workspace TOML, provider catalogue and alias fixtures | `PH-T078` (configured/catalogued/selected/responded values remain distinct) | Runtime TOML is authoritative; catalog aliases normalize; do not retire handles solely from a catalogue refresh. |
| **Slice D** | Supervisor Deadlines & Non-Blocking Pipe Cleanup | cx / ag | Supervised child/grandchild fixture | `PH-T014` (Supervisor returns within allowance on pipe-holder) | No unsafe cross-thread `os.close`; bounded controller return. |
| **Slice R** | Caller Intent, Binding, Selection and Retention | cx / ag | Same request ID under ambient-only and explicit changes; protected native handles | `PH-T086` (intent/omission conflict matrix) | Sticky accepted intent, one abandoned-session selection rule, protected handles and owner-selected expiry policy. |
| **Slice O** | Protocol Framing and Output Budgets | cx / ag | Valid/invalid terminal frames, giant body and answer fixtures | `PH-T090` (drain, answer cap, frame certainty and persisted flags) | Public cap and answer semantics are chosen before TDD; incomplete framing never becomes terminal success. |
| **Slice Stuck6** | Audited Stream Resolution (`stream resolve`) | cx / ag | Blocked stream fixture | `PH-T019` (Audited resolve consumes prompt, frees offset) | Reject known-running execution; external effects accepted in either mode. |
| **Slice C** | Bounded Catch-Up, Delta Paging & Self-Response Omission | ag / cx | 10k-record stream fixture | `PH-T024` (Delta memory bounded; pin ceiling enforced) | Fresh suffix + pins reference semantics preserved. |
| **Slice MV2-1** | Terminal Standards, Colors, and Escapes | ag / cx | Non-TTY / TTY stdout captures | `PH-T029` (NO_COLOR disables escapes cleanly) | Pure ASCII output on plain streams; zero cursor corruption. |
| **Slice MV2-2** | `SnapshotContext` & Bounded Diagnostic Readers | cx / ag | unrelated-history fixture, pinned ro conn | `PH-T033` (bounded query work as unrelated rows grow) | Connection owned and closed by snapshot; integrity and recount remain separate. |
| **Slice SR4a** | Bounded Session Inspection (`list`/`show`) & Keyset Paging | cx / ag | Multi-session DB fixture | `PH-T053` (stable cursor bound to filter/store/lineage/index) | Inspection is usable without quota, colors, telemetry, or subprocess probes. |
| **Slice SR4b** | Attributable Telemetry Receipt | ag / cx | Matched provider log, incomplete newer call, wrong-turn fixture | `PH-T091` (exact association and immutable receipt) | Exactly one versioned `bridge_evidence.kind=session_telemetry` authority; extraction outside write transaction. |
| **Slice MV2-3** | Monotonic Cadence, Backoff & Agy Guard | cx / ag | Monotonic clock mock | `PH-T038` (Missed slots skipped; backoff per provider) | Zero-cost collection continues on exhausted pool to observe reset; agy probe suspended only if probe consumed tokens or cost unprovably zero; asks never blocked. |
| **Slice MV2-4** | Pace Balance Math & Diagnostic Line | ag / cx | 7D quota observations | `PH-T042` (R1 math preserved: $cc \le \min(ag)$, ag–cx $\le 5$pt) | Missing anchor provenance yields unknown balance; no fake pace. |
| **Slice MV2-5** | Shared Read-Only Watcher & Flag Removals | ag / cx | Read-only watch invocation | `PH-T046` (Zero subprocesses, zero SQL writes in watch) | 1 Hz redraw performs zero SQL when store unchanged. |
| **Slice MV2-6** | Export Allowlist & Structural Redaction | cx / ag | Sentinel prompt/path fixture | `PH-T050` (Strict redaction of paths, vendor IDs, prompts) | Schema-valid NDJSON; explicit oversized-frame handling. |
| **Slice SR4b** | Attributable Telemetry Receipts in `bridge_evidence` | ag / cx | Provider log fixture | `PH-T058` (Tokens stored independent of window; unclamped) | Cache normalized per provider; unverified Claude events rejected. |
| **Slice SR5** | Reduced Context Alerts (critical-only candidate) | cx / ag | Attributable receipt and stale/duplicate samples | `PH-T065` (90% critical transition; WARN is absent from this scope) | Threshold and exit behavior remain owner choices; no mandatory WARN contract. |
| **Slice WP-E** | Prerequisite Skips & Orphan Traceability | ag / cx | Declared prerequisite absent/present fixtures | `PH-T079` (skip has a named gate, reason and linked contract) | Every skip is attributable; unexplained skips and orphan tests fail traceability. |
| **Slice RC** | Release Qualification & Clean Candidate Verification | cx / ag | Clean source and wheel outside checkout | `PH-T074` (dirty source rejected for release evidence) | Prove source/wheel/outside-checkout behavior and all candidate gates; SHA/version ladder are not prerequisites. |

---

## 4. Concrete Final Interfaces and Module Ownership

New functionality is anchored to minimal, coherent extension modules:

```python
# ==============================================================================
# peerhub/extensions/schema_guard.py (Slice B)
# Unified compatibility barrier using the actual store format
# ==============================================================================
SUPPORTED_STORAGE_VERSION = 4

def assert_store_compatibility(conn: sqlite3.Connection) -> None:
    """Checks PRAGMA user_version against the supported format. Migration uses
    one SQLite connection for supported predecessor steps, extension DDL and
    receipts/projections; commits the new version only after all work succeeds.
    Constructors must not open another writer while this transaction is held."""

def ensure_extension_triggers(conn: sqlite3.Connection, owned_table: str,
                              expected_triggers: Mapping[str, str]) -> None:
    """Idempotently reconciles triggers by comparing normalized sqlite_master SQL.
    If an unknown trigger is detected on an owned table, raises MigrationSafetyError
    pending explicit migration (never silently deletes unknown triggers)."""

# ==============================================================================
# peerhub/extensions/diag_snapshot.py (Slice MV2-2)
# Shared read-only transaction context (owns connection opening)
# ==============================================================================
@dataclass(frozen=True)
class SnapshotLimits:
    max_streams: int = 50
    max_pools: int = 16
    max_observations_per_pool: int = 1
    statement_timeout_ms: int = 250

@dataclass(frozen=True)
class SnapshotContext:
    conn: sqlite3.Connection
    read_at_us: int
    limits: SnapshotLimits

def open_snapshot_context(db_path: str, *, limits: SnapshotLimits | None = None) -> SnapshotContext:
    """Opens connection with mode=ro, query_only=True, and read-only authorizer."""

def read_peers_section(ctx: SnapshotContext) -> dict[str, Any]: ...
def read_streams_section(ctx: SnapshotContext) -> dict[str, Any]: ...
def read_observations_section(ctx: SnapshotContext) -> dict[str, Any]: ...

# ==============================================================================
# peerhub/extensions/session_monitoring.py (Slice SR4a)
# Bounded session inspection and immutable keyset pagination
# ==============================================================================
@dataclass(frozen=True)
class SessionCursor:
    cursor_version: int
    store_id: str  # opaque stable store identity; never a filesystem path
    stream_id: str
    peer_id: str
    lineage_id: str | None
    filter_digest: str
    order_index_version: int
    high_water_generation: int  # UNIQUE(stream_id, peer_id, generation) excludes later inserts
    generation: int
    handle: str

def read_session(ctx: SnapshotContext, handle: str) -> SessionSnapshot | None: ...
def read_session_page(ctx: SnapshotContext, *, peer: str | None = None,
                      stream: str | None = None, lineage: str | None = None,
                      after: SessionCursor | None = None,
                      limit: int = 20) -> tuple[list[SessionSnapshot], SessionCursor | None]: ...

# Cursor order/index contract: require a scope or use a documented all-scope
# query plan; order by (stream_id, peer_id, generation DESC, session_handle DESC).
# Each supported filter shape has a matching index identified by
# order_index_version. The opaque cursor binds version, stable store_id, lineage,
# normalized filters and digest, order/index version, and first-page high-water
# generation. The store enforces UNIQUE(stream_id,peer_id,generation) and
# monotonic generation allocation, so later inserts are invisible to traversal.
# A later page rejects a different store, lineage, filters or order version.
# Mutable state is read at each page snapshot; stable identity/order, not frozen
# values, is promised across page requests.

# ==============================================================================
# peerhub/extensions/session_receipts.py (Slice SR4b)
# Single receipt authority: writes immutable telemetry evidence to bridge_evidence
# ==============================================================================
def record_telemetry_receipt(conn: sqlite3.Connection, *, delivery_id: str,
                             stream_id: str, peer_id: str, session_handle: str,
                             model: str | None, last_input_tokens: int | None,
                             tokens_source: str, context_window: int | None,
                             window_source: str, extracted_at: float) -> None:
    """Inserts kind='session_telemetry' into bridge_evidence outside invocation TX
    (unique on stream_id, peer_id, delivery_id, kind). Computes context_pct only
    when both tokens and window are attributable."""

# ==============================================================================
# peerhub/cli/stream_resolve.py (Slice Stuck6)
# Audited resolution for blocked/uncertain deliveries
# ==============================================================================
def resolve_delivery(store_path: str, *, stream_id: str, delivery_id: str,
                     disposition: str, reason: str, accept_unknown_effects: bool,
                     operator: str) -> None:
    """Fenced, atomic abandonment of an uncertain delivery. Rejects known-running
    processes. Requires accept_unknown_effects in either mode. Appends control.reconcile,
    consumes prompt delivery, taints session handle, and frees stream read cursor.
    Stale fence rejects late completions from original runner (audit evidence only;
    no response append, continuity revival, or dispatch)."""
```

---

## 5. Storage Invariants, Compatibility Epoch & Migration

### The `user_version` Epoch Fence
1. **The v0.15.2 Barrier (must be proven):** HEAD, tag v0.15.2, and the inspected DB all support format 3. A proposed incompatible format must therefore be 4. Verify with the actual v0.15.2 executable that it refuses a migrated v4 database before any write. Never infer this from a marker table or synthetic test alone. Migration must use one connection, apply supported predecessor steps plus extension DDL/receipt/projection changes atomically, stamp 4 last, and roll back every change on fault. Stop every already-open old writer before backup and migration. Align `SUPPORTED_SCHEMA_VERSION`, `migrations.CURRENT_VERSION`, and the sealed `REQ-MIG-001/002`, `MIG-001..003` clauses; constructors cannot acquire a second write connection beneath `BEGIN IMMEDIATE`.
2. **Sealed Spec Revision Gate:** Current M1 and migration requirements must be read at implementation start. Revise/reseal the actual `REQ-MIG-001/002` and `MIG-001..003` clauses to specify format 4, one-connection atomic upgrade from supported format 3, future-version refusal, rollback, and old-writer cutover. There is no version-1→2 requirement in this proposal.
3. **Operational Cutover:** Stop already-open 0.15.2 monitors before executing `peerhub migrate`. Migration is transactional: on failure, rollback restores `user_version`, table structures, trigger definitions, and data simultaneously.
4. **Dev DB Isolation:** Pre-release development is conventionally isolated to `--db .peerhub/dev.db`.

### Schema & Index Specifications (Verified Against Source)

- **`bridge_session_history` (Committed in PR2):**
  - Primary Key: `(session_handle)`
  - Columns: `session_handle`, `stream_id`, `peer_id`, `runtime_kind`, `external_session_id`, `session_generation`, `adapter_fingerprint`, `binding`, `resumable`, `state`, `last_seen`, `workspace_generation`, `vendor_session_id`, `context_watermark`, `bootstrap_truncated`, `usage_json`, `context_watermark_version`, `created_at`, `tombstoned_at`
  - Unique Constraint: `UNIQUE(stream_id, peer_id, session_generation)`
  - Indexes: `idx_bsh_monitor_scope`, `idx_bsh_monitor_live`, `idx_bsh_monitor_active`
- **`bridge_deliveries` (Committed in PR2):**
  - Primary Key: `(delivery_id)`
  - Columns: `delivery_id`, `seq`, `stream_id`, `peer_id`, `record_id`, `record_position`, `attempt`, `certainty`, `claim_generation`, `external_session_id`, `session_generation`, `execution_id`, `result_json`, `result_digest`, `terminal_at`, `response_record_id`, `acked`, `reconcile_record_id`
  - Unique Constraint: `UNIQUE(stream_id, peer_id, record_id, attempt)`
  - Indexes: `idx_bd_record_peer_attempt`
- **`bridge_evidence` (Existing Single Receipt Authority):**
  - Columns: `seq INTEGER PRIMARY KEY AUTOINCREMENT`, `delivery_id TEXT`, `stream_id TEXT`, `peer_id TEXT`, `kind TEXT`, `certainty TEXT`, `detail TEXT`
  - Indexes: `idx_be_delivery_seq`, `idx_be_stream_kind_seq`

---

## 6. Execution Scenarios and Lifecycle Traces

### Scenario 1: Default Invocation After Stream Resolution
- **Trace:** Delivery `deliv_1` timed out and was resolved via `stream resolve --as abandoned`. The latest session handle is `LOST`. User executes default `peerhub ask cc "next prompt"`.
- **Resolution:** Resolution recorded explicit abandonment consent. The next default ask follows ONE deterministic rule: select the latest compatible session excluding LOST/tainted handles; if none exists, allocate fresh with reason `prior_abandoned`. The original unresolved handle stays permanently ineligible.
- **Behavior:** Command succeeds without entering a tainted-latest error loop. Catch-up delta, fresh suffix, pin, projection and redirect-context paths exclude disposed prompts (or classify them non-actionable with an audit summary) without automatic re-execution. `--fresh` is never permitted to bypass an *unresolved* delivery.

### Scenario 2: Explicit Replay under Profile Configuration Change
- **Trace:** Prompt record 5 was executed under profile `gemini-3.8-flash-high`. Admin updates workspace profile TOML default to `gemini-3.8-flash-medium`. User reruns request with identical `(stream_id, peer_id, request_id)`.
- **Resolution:** Replay key is `(stream_id, peer_id, request_id)`. Caller intent hash detects conflicts, never used as prompt-dedup key.
- **Behavior:** Replay compares versioned caller intent (prompt text, explicit selector, workspace root, and omission semantics) before evaluating current runtime configuration. Ambient-only changes return the cached terminal result instantly without re-spawning; changed explicit caller options conflict even if effective config matches; pending retries retain accepted handle/generation/configuration; X→Y→X selection preserves history.

### Scenario 3: Process Exit vs. Semantic Output Truncation
- **Trace:** Model generates 15MB of internal tool traces followed by an incomplete answer; pipe reaches buffer limits and exits returncode 0.
- **Resolution:** Process supervisor notes returncode 0 but marks incomplete framing. Confirmed `STARTED` delivery certainty is strictly preserved (never downgraded to `MAY_HAVE_STARTED`).
- **Behavior:** Incomplete framing leaves outcome `INCOMPLETE`, disposition unset, and prior certainty preserved (`STARTED`); execution remains unresolved. Read-only intent (`writable=false`) does NOT settle as harmless without verified containment proof.

### Scenario 4: Audited Stream Resolution (`stream resolve`)
- **Trace:** Delivery `deliv_987` on stream `str_main` timed out and is locked in `blocked_uncertain`.
- **Execution:** Operator executes:
  `peerhub stream resolve --stream str_main --delivery deliv_987 --as abandoned --reason "Provider timeout" --accept-unknown-effects`
- **Behavior:** Under claim fence, Bridge:
  1. Verifies delivery is not currently running (rejects known-running execution).
  2. Requires `--accept-unknown-effects` regardless of `writable` intent.
  3. Appends `control.reconcile(abandon)` record to stream.
  4. Advances CAS offset through the prompt position.
  5. Records operator identity, source, evidence reference, and disposition atomically.
  Late completions from the original runner arriving after resolution are rejected by the stale fence and recorded as audit evidence only (no response append, continuity revival, retry, or manufactured success). Replaying the abandoned `request_id` returns the recorded abandonment disposition without re-executing.

---

## 7. Telemetry Provenance and Metric Contracts

### Single Receipt Authority in `bridge_evidence`
Context occupancy is recorded as an immutable evidence row with existing `kind='session_telemetry'` in `bridge_evidence`:
1. **Measured Input Tokens (`last_input_tokens`):** Extracted from final turn vendor events (e.g. Codex rollout `last_token_usage.input_tokens`). If only cumulative billing usage is available, `last_input_tokens` is stored as `null`. Unverified Claude `turn.completed` events are rejected pending real fixtures.
2. **Context Window (`context_window`):** Sourced from responding model manifest or explicit vendor rollout event. Never hardcoded as a universal provider constant.
3. **Independent Provenance:** Sourced as `tokens_source` and `window_source` (values: `'reported'`, `'catalog'`, `'unknown'`).
4. **Cache Normalization:**
   - OpenAI/Codex: cached tokens are a subset of input tokens (cache-inclusive; do not add).
   - Anthropic: input tokens exclude cache reads/writes (cache is additive; must be normalized per provider documentation).
5. **Ratio Anomaly Contract:** If `context_pct > 100.0%`, it is reported exactly as computed (unclamped) and labelled `ratio_anomaly`. Clamping is prohibited. Anomaly ratio proves no vendor overflow; does not assert actual occupancy.

---

## 8. Quota, Time, and Pacing Policy

### 5-Hour Availability vs. 7-Day Pace
- The October 14 quota incident recorded an interval measurement where `quota:ag:3P-5H` dropped to 0.0 and `3P-7D` dropped to 0.484, while Google-hosted pools remained $>92\%$ available. The 51.6% pool drop was an observed change over a multi-turn time interval across models, NOT the single-turn cost of one prompt.
- **Final comparable anchors (2026-10-14 09:39 UTC):** A fresh `observation refresh` collected ag, cx and cc quota evidence within 9 seconds. Using the existing R1 metric `pace = used% - elapsed%` for each provider's own 7-day window gives ag-3P `+45.4pt`, ag-Google `-0.3pt`, cx `+46.6pt`, and cc `-8.3pt`. R1 holds: `cc (-8.3) <= min(ag: +45.4, -0.3)`, and `abs(max(ag: +45.4, -0.3) - cx: +46.6) = 1.2pt <= 5pt`. Thus this snapshot meets the requested `cc << (ag == cx)` pacing target. Remaining quota is separate: ag-3P 49%, ag-Google 96%, cx 37%, cc 96%. This is a point-in-time quota pace, not attribution of cost to any one model turn.
- **Policy Contract:**
  1. Measured capacity is evidence, not automatic ask admission denial. Monitoring warnings remain advisory.
  2. Exhausted quota does NOT suspend zero-cost collection or block asks; continue collecting reset evidence. Suspend agy probe collection only on consumed OR unprovably-zero-cost probe token spend per agreed zero-token guard, with honest unknown/backoff.
  3. Original R1 balance math ($cc \le \min(ag\text{-3P}, ag\text{-Google})$ and $\text{abs}(\max(ag\text{-3P}, ag\text{-Google}) - cx) \le 5\text{ percentage points}$) is preserved. Missing anchor provenance yields unknown balance; no fake pace balance is calculated.
  4. Allocate work as the owner requested: cc handles short, targeted veto reviews; ag and cx carry the detailed independent analysis and cross-critique, with their observed 7-day paces kept within the R1 tolerance when valid anchors allow. The target is `cc << (ag == cx)`; do not create calls or spend quota to force a numeric appearance of balance. If the observed pools cannot establish the target, report the measured state and its uncertainty.
  5. Universal `<500` billing token promises are removed.

---

## 9. Comprehensive Numbered RED-First Specifications (PH-T001 – PH-T094)

*Note: Execution status of tests is unestablished; existing passes against committed PR1–PR3 code are allowed. Each contract defines an independent oracle and failure/boundary criterion.*

### Slice B: Store-Format 3→4 Atomic Migration & Idempotent Trigger Init
- **PH-T001:** GIVEN a DB migrated to `user_version=4`, WHEN the actual installed v0.15.2 executable opens it, THEN its real guard rejects before any logical or physical schema/data write. Compare before/after schema, trigger definitions, rows and `user_version`; assert the actual exit/result (exit code 6 `SchemaVersionError`) and verify logical database contents remain completely unchanged. A current v0.15.2 DB at version 3 remains accepted.
- **PH-T002:** GIVEN an upgraded database, WHEN `Bridge`, `ClaimStore`, and `ObservationStore` are opened 5 consecutive times, THEN `PRAGMA schema_version` remains strictly identical. (Oracle: SQLite schema cookie invariance).
- **PH-T003:** GIVEN an unrecognized trigger on a bridge-owned table, WHEN `ensure_extension_triggers` runs, THEN migration raises `MigrationSafetyError` rather than deleting unknown triggers. (Oracle: Exception type; trigger preserved).
- **PH-T004:** GIVEN a modified trigger body on `bridge_sessions`, WHEN `ensure_extension_triggers` executes, THEN the trigger is restored to exact specification. (Oracle: SQL comparison).
- **PH-T005:** GIVEN an injected fault during schema migration, WHEN transaction aborts, THEN `user_version`, table definitions, triggers, and row data roll back completely. (Oracle: Complete database snapshot comparison).

### Slice A: Provenance, Store Discovery & CLI Help (WP-B)
- **PH-T006:** GIVEN an explicit `--db <path>` flag and `PEERHUB_DB` env variable, WHEN `select_store` runs, THEN explicit `--db` wins. (Oracle: `StoreSelection.source == 'explicit'`).
- **PH-T007:** GIVEN implicit discovery in a directory tree, WHEN `select_store` runs, THEN implicit `./peerhub` subfolder discovery is skipped per owner O-1. (Oracle: Selected store path).
- **PH-T008:** GIVEN a workspace with spaces and `&` in the path, WHEN `store_select` emits error hints, THEN paths are safely quoted. (Oracle: Emitted string regex).
- **PH-T009:** GIVEN `peerhub --version`, WHEN executed, THEN stdout contains the configured semantic version. Optional build provenance may be reported separately if selected; SHA/dirty decoration is not required for ordinary CLI version output. (Oracle: Parsed version field).
- **PH-T010:** GIVEN `peerhub ask --help`, WHEN executed, THEN options `--session`, `--fresh`, and `--max-output-bytes` are documented with actionable examples. (Oracle: Substring assertions).

### Slice P: Configuration SSOT & Resolver (WP-C)
- **PH-T011:** GIVEN conflicting profile definitions in packaged JSON vs workspace TOML, WHEN `resolve_profile` runs, THEN TOML values strictly override JSON. (Oracle: Resolved profile object).
- **PH-T012:** GIVEN an invocation with shell metacharacters (`&`, `|`, `;`), WHEN `binary_resolution` prepares argv, THEN arguments are passed as discrete list elements without shell expansion. (Oracle: Process creation argument vector).
- **PH-T013:** GIVEN an uninstalled provider binary, WHEN resolution runs, THEN a clear warning is emitted without speculative fallback to arbitrary path binaries. (Oracle: Warning event and exit code 2).

### Slice D: Supervisor Deadlines & Non-Blocking Drain (WP-D)
- **PH-T014:** GIVEN a helper exits while an escaped descendant keeps stdout open, WHEN the process deadline/cleanup allowance expires, THEN controller return is bounded by one absolute deadline and reports drain incompleteness. When the pipe holder later exits, owned readers/handles are eventually reaped without waiting forever. (Oracle: wall-clock bound plus eventual thread/handle census).
- **PH-T015:** GIVEN a pump blocked in buffered `read1()`, WHEN cleanup runs, THEN it never calls unsafe cross-thread `os.close` or blocking buffered close; ownership and late cleanup are explicit. (Oracle: bounded return, no buffer-lock deadlock, resource census before/after pipe-holder exit).
- **PH-T016:** GIVEN a termination signal and escaped or reparented descendants, WHEN the supervisor reports containment, THEN it proves the documented process-tree boundary; successful `taskkill` or process-group polling alone does not prove an escaped descendant is gone. Unknown containment keeps delivery outcome unresolved. (Oracle: helper/descendant process-tree fixtures and truthful containment status).
- **PH-T017:** GIVEN repeated timeout/pipe-holder invocations, WHEN descendants eventually close their pipes, THEN OS handles, pump threads and cleanup tasks return to a stable bounded baseline. (Oracle: repeated handle/thread census at controller return and after eventual cleanup).
- **PH-T018:** GIVEN an abort signal during execution, WHEN `abort()` is called, THEN tree kill runs immediately and returns before deadline. (Oracle: Process liveness check).

### Slice Stuck6: Audited Stream Resolution
- **PH-T019:** GIVEN an unacked delivery in `STARTED` state, WHEN an authorized abandonment resolution executes, THEN operator, source, evidence, disposition, fence, and offset change commit atomically and idempotently. Known-running work is rejected; unknown external effects require explicit acceptance regardless of writable intent. Competing/completed resolvers cannot double-advance. (Oracle: transaction fault injection at each write boundary and concurrent resolver test).
- **PH-T020:** GIVEN an abandoned delivery, WHEN `peerhub ask` is replayed with the identical `(stream_id, peer_id, request_id)`, THEN it returns the recorded abandonment disposition without spawning a process. (Oracle: Replay disposition result).
- **PH-T021:** GIVEN an uncertain delivery with either `writable=True` or `writable=False`, WHEN resolved without `--accept-unknown-effects`, THEN command rejects resolution with exit code 2. (Oracle: Exit code 2).
- **PH-T022:** GIVEN a delivery resolved as abandoned, WHEN default selection runs, THEN it follows ONE deterministic rule: select the latest compatible session excluding LOST/tainted handles; if none exists, allocate fresh with reason `prior_abandoned`. It never loops on an older tainted session, and `--fresh` cannot bypass unresolved delivery. Disposed prompts are excluded or marked non-actionable in catch-up without re-execution. (Oracle: deterministic selection fixtures across several LOST/tainted/reusable handles).
- **PH-T023:** GIVEN an original runner completing after `stream resolve`, WHEN its completion arrives, THEN it is rejected by the stale fence and recorded as audit evidence only; no response append, continuity revival, retry, or manufactured success occurs. (Oracle: stale-generation write rejection and record/offset snapshot).

### Slice C: Bounded Catch-Up, Delta Paging & Self-Response Omission
- **PH-T024:** GIVEN a stream with giant record bodies (e.g. 5MB payloads), WHEN catch-up executes, THEN reader enforces candidate memory limits before materializing full bodies, failing pre-spawn with `delta_too_large` if budget exceeded. (Oracle: CycleResult status).
- **PH-T025:** GIVEN a stream with 100 pinned redirect records, WHEN catch-up executes with pin cap 50, THEN catch-up rejects pre-invocation with `delta_too_large`. (Oracle: Pin limit check).
- **PH-T026:** GIVEN prompt 1, response 2, and prompt 3 on session $S$, WHEN resuming $S$, THEN only the exact native response proven represented by native session identity, delivery, generation, response association and continuity watermark is omitted. Every unproven response remains in the delta; author or text alone never proves self-response. (Oracle: exhaustive association matrix).
- **PH-T027:** GIVEN prompt 1, response 2 on session $S_1$, and prompt 3 on session $S_2$, WHEN executing on $S_2$, THEN response 2 is included in catch-up delta. (Oracle: Foreign response inclusion).
- **PH-T028:** GIVEN a total prompt payload exceeding transport limits, WHEN delta wraps prompt, THEN payload byte size is validated before spawn. (Oracle: Pre-spawn validation error).

### Slice MV2-1: Terminal Styling, Colors, Escapes
- **PH-T029:** GIVEN `NO_COLOR=""`, WHEN terminal initializes, THEN colors remain enabled on capable TTY. (Oracle: ANSI escape sequence presence).
- **PH-T030:** GIVEN `NO_COLOR="1"`, WHEN terminal renders, THEN color/style SGR sequences are absent while cursor lifecycle controls required for watch-mode restoration still work. (Oracle: classify SGR separately from cursor-control sequences).
- **PH-T031:** GIVEN `FORCE_COLOR="1"` on non-TTY, WHEN rendering text, THEN colors are emitted. (Oracle: Captured bytes).
- **PH-T032:** GIVEN a window resize event during watch mode, WHEN redraw triggers, THEN terminal cursor is restored cleanly without visual artifacts. (Oracle: Cursor positioning bytes).

### Slice MV2-2: `SnapshotContext` & Bounded Diagnostic Readers
- **PH-T033:** GIVEN a large observation history, WHEN the bounded observations section reads, THEN its indexed query examines only scoped requested rows; this is a work oracle, not an asserted current-state or fixed 16-row behavior. (Oracle: query plan and rows visited under unrelated-history growth).
- **PH-T034:** GIVEN a database under active write load, WHEN `open_snapshot_context` runs, THEN all section queries read from one pinned snapshot with `mode=ro` and `query_only=1`. (Oracle: SQLite transaction isolation).
- **PH-T035:** GIVEN a corrupt row in `observations`, WHEN `read_observations_section` runs, THEN only the observations section degrades to PARTIAL while other sections return OK. (Oracle: Diagnostic section status).
- **PH-T036:** GIVEN watch mode active, WHEN rendering frames, THEN `PRAGMA quick_check` is never executed. (Oracle: SQL statement trace).
- **PH-T037:** GIVEN explicit integrity diagnostics, WHEN executed, THEN SQLite `quick_check` and application receipt/counter recount are reported as separate operations and outcomes; ordinary snapshots do not perform a global recount. (Oracle: distinct SQL traces and independently corrupted SQLite/application invariants).

### Slice MV2-3: Cadence, Backoff & Agy Guard
- **PH-T038:** GIVEN a collection probe that takes 12 seconds with interval 10 seconds, WHEN probe completes, THEN next cycle skips missed deadlines and executes at next boundary. (Oracle: Fake-clock monotonic ledger).
- **PH-T039:** GIVEN consecutive probe failures on peer `cx`, WHEN backoff evaluates, THEN backoff intervals advance: 120s -> 240s -> 900s. (Oracle: Scheduled probe timestamps).
- **PH-T040:** GIVEN a probe failure on `cx`, WHEN `ag` probe runs, THEN `ag` executes without backoff delay (per-provider isolation). (Oracle: Independent provider schedules).
- **PH-T041:** GIVEN an exhausted 5-hour pool but fresh, zero-cost reset evidence is available, WHEN quota collection runs, THEN it continues to collect and persist reset evidence and never blocks an ask. GIVEN a probe consumed a token or cost cannot be proven zero, THEN only that agy probe is suspended/backed off and the unknown is recorded; ask remains available. (Oracle: fake-clock scheduler plus provider call counter and durable observation rows).

### Slice MV2-4: Pace Balance & Diagnostic Line
- **PH-T042:** GIVEN valid fresh seven-day pace anchors for ag-3P, ag-Google and cx, WHEN balance computes, THEN it checks `cc <= min(ag-3P, ag-Google)` and `abs(max(ag-3P, ag-Google) - cx) <= 5 percentage points`; missing/stale anchor basis yields UNKNOWN. (Oracle: table-driven arithmetic and anchor validity fixtures; observed interval pool change is not per-turn cost).
- **PH-T043:** GIVEN valid anchors whose second R1 inequality differs by exactly 5.0 points, WHEN balance evaluates, THEN it is within tolerance; at 5.01 it is outside. (Oracle: boundary arithmetic).
- **PH-T044:** GIVEN `peerhub diag --line`, WHEN executed, THEN output is exactly one ASCII line terminated by LF. (Oracle: Captured bytes ending in `\n`, length count).
- **PH-T045:** GIVEN `peerhub diag --line --json`, WHEN executed, THEN CLI exits with code 2 (mutually exclusive options). (Oracle: Exit code 2).

### Slice MV2-5: Read-Only Watch and Collection Workflow
- **PH-T046:** GIVEN `peerhub monitor` and committed observations, WHEN the first cycle starts, THEN the committed snapshot is rendered before quota refresh and the completed frame reports refresh and diagnostic status separately. (Oracle: ordered fake probe/snapshot events and status matrix).
- **PH-T047:** GIVEN `peerhub diag --live`, WHEN invoked, THEN the command remains a read-only watch and starts no provider subprocess or SQL write. (Oracle: process-spawn trap and DB digest).
- **PH-T048:** GIVEN watch mode running at 1 Hz, WHEN redraw occurs without DB changes, THEN zero SQL queries are executed. (Oracle: Statement count = 0 during redraw).
- **PH-T049:** GIVEN user presses `q` or `Ctrl-C`, WHEN watcher exits, THEN terminal cursor is restored and process exits code 0. (Oracle: Exit code and terminal reset bytes).

### Slice MV2-6: Export Allowlist & Redaction
- **PH-T050:** GIVEN a diagnostic frame containing user prompt text and filesystem paths, WHEN `export_frame` runs, THEN prompt text and paths are absent from NDJSON output. (Oracle: String search on NDJSON frame).
- **PH-T051:** GIVEN an export containing account credit numbers or vendor session IDs, WHEN serialized, THEN credit and vendor IDs are completely excluded. (Oracle: Allowlist field validation).
- **PH-T052:** GIVEN a stream name containing CJK characters and spaces, WHEN serialized, THEN JSON escaping is valid RFC 8259. (Oracle: JSON parser round-trip).

### Slice SR4a: Bounded Session Inspection & Keyset Paging
- **PH-T053:** GIVEN 1,000 session rows and a supported scope/filter, WHEN `peerhub session list --limit 20` runs, THEN at most 20 rows and an opaque cursor are returned; cursor binds version, stable store ID, lineage, normalized filters, ordering/index version and first-page generation high-water. (Oracle: decode/replay cursor across changed store/filter/order and visibility fixtures).
- **PH-T054:** GIVEN a cursor after page 1, WHEN a new generation is inserted and an existing row changes state before page 2, THEN the new row is excluded by high-water, stable identity keys do not duplicate/drop, and mutable state is reported at page-2 snapshot time. (Oracle: two-page interleaving with exact key-set equality).
- **PH-T055:** GIVEN a query for session `sess_0000000000000000` (non-existent), WHEN `session show` runs, THEN command exits code 1 with a structured not-found result matching Core point reads. (Oracle: Exit code and response envelope).
- **PH-T056:** GIVEN a custom runtime session with no vendor ID, WHEN inspected, THEN session eligibility is derived from actual predicate (watermark version, resumable). (Oracle: Eligibility predicate).
- **PH-T057:** GIVEN a legacy session lacking token receipts, WHEN inspected, THEN token fields are reported as JSON `null`. (Oracle: JSON serialization).

### Slice SR4b: Attributable Telemetry Receipts in `bridge_evidence`
- **PH-T058:** GIVEN a Codex rollout containing `last_token_usage.input_tokens = 50000` and `model_context_window = 200000`, WHEN receipt commits to `bridge_evidence`, THEN `context_pct = 25.0` is recorded with `tokens_source='reported'` and `window_source='reported'`. (Oracle: Evidence detail JSON).
- **PH-T059:** GIVEN OpenAI provider log, WHEN cache tokens are parsed, THEN cached tokens are recognized as a subset of input tokens without double-counting. (Oracle: Token normalization math).
- **PH-T060:** GIVEN valid `last_input_tokens = 45000` with unlisted context window, WHEN receipt commits, THEN `last_input_tokens = 45000`, `context_window = None`, and `context_pct = None`. (Oracle: Evidence detail JSON).
- **PH-T061:** GIVEN an attributable prompt of 105,000 tokens against 100,000 window, WHEN receipt commits, THEN `context_pct = 105.0` is recorded unclamped and labelled `ratio_anomaly`. (Oracle: Evidence detail JSON).
- **PH-T062:** GIVEN ambiguous provider logs or late flush, WHEN extraction runs, THEN telemetry falls back safely to `source='unknown'` with null values. (Oracle: Telemetry source field).

### Slice SR5: Reduced Context Alerts
- **PH-T063:** GIVEN session `context_pct = 69.9%`, WHEN alert machine evaluates, THEN zero alerts are fired. (Oracle: Alert list empty).
- **PH-T064:** GIVEN the proposed critical-only scope and an attributable session at `89.9%`, WHEN alert machine evaluates, THEN no WARN event is created. If an owner later selects a WARN threshold, that behavior requires a separately approved contract. (Oracle: Alert list empty).
- **PH-T065:** GIVEN the owner selects the proposed 90% critical threshold and an attributable session reaches `90.0%`, WHEN alert machine evaluates, THEN `CONTEXT_CRITICAL` is triggered. If the owner selects a different threshold or defers alerts, this RED is revised before TDD. (Oracle: Alert severity CRITICAL; gated by G-ALERT).
- **PH-T066:** GIVEN an alert firing in CLI, WHEN diagnostic status is OK, THEN process exit code is 1 conditional on owner approval of G-ALERT. (Oracle: Process exit code).

### Track & Boundary Verification Contracts
- **PH-T067:** GIVEN prompt record with identical caller intent, WHEN replayed after workspace TOML model default change, THEN original result is returned without spawning process. (Oracle: Zero process spawns).
- **PH-T068:** GIVEN established and unresolved native handles at the retention limit, WHEN allocation cleanup runs, THEN unresolved handles are protected from eviction; original handle identities are compared (not merely count). (Oracle: Preserved native handle identities and count).
- **PH-T069:** GIVEN a session idle for 31 days, WHEN evaluated under the owner-selected retention policy, THEN it remains eligible if count-cap-only was selected and it is within the 20 retained handles; if inactivity expiry was selected, its named expiry rule applies. (Oracle: eligibility under both policy fixtures; G-RETENTION must close before TDD).
- **PH-T070:** GIVEN raw output stream emitting 15MB tool chatter followed by 200-byte answer, WHEN process completes under deadline, THEN process is NOT killed by a drain cap and answer is completely preserved. (Oracle: Answer text and flag).
- **PH-T071:** GIVEN the owner accepts the proposed parsed-answer meaning of `--max-output-bytes` and the answer exceeds the 1,000,000-byte default, WHEN delivered, THEN answer truncation is explicit and `output_truncated=True` is recorded on both Delivery and StreamRecord. If the owner rejects this semantic change, preserve current raw-capture semantics and revise the RED before TDD. (Oracle: byte length, both persisted flags, and selected contract).
- **PH-T072:** GIVEN a confirmed `STARTED` delivery whose process exits code 0 with truncated JSON, WHEN Bridge processes cycle, THEN delivery certainty remains strictly `STARTED`, outcome remains `INCOMPLETE`, and disposition unset (execution remains unresolved). (Oracle: Certainty column and unset disposition).
- **PH-T073:** GIVEN a process killed due to timeout in read-only mode without containment proof, WHEN Bridge processes cycle, THEN delivery remains `STARTED` (does not settle as `FAILED_TERMINAL`; read-only intent is not containment proof). (Oracle: Certainty column).
- **PH-T074:** GIVEN a candidate tested on a dirty git worktree, WHEN `tools/live_gate.py` runs, THEN candidate stamping is rejected with exit code 2. (Oracle: Tool exit code 2).
- **PH-T075:** GIVEN built distribution wheel installed in fresh virtual environment, WHEN `peerhub --version` executes, THEN import origin matches installed package path, not workspace source. (Oracle: Python `__file__` origin).
- **PH-T076:** GIVEN a format 4 store opened by installed v0.15.2 binary, WHEN reader opens store, THEN process fails fast with exit code 6 (`SchemaVersionError`); v0.15.2 accepts format 3 stores. (Oracle: Process exit code 6).
- **PH-T077:** GIVEN an ask issued when monitor reports quota exhausted, WHEN ask dispatches, THEN process spawns normally (monitoring does not block admission). (Oracle: Process spawn count = 1).
- **PH-T078:** GIVEN runtime-configured model, catalogued model ID, selected invocation model and provider-reported responding model, WHEN verification runs, THEN all four values and their provenance remain distinct; only documented aliases normalize, and a catalog refresh alone does not retire a session or change replay identity. (Oracle: fixtures for alias, mismatch, stale catalog and response attribution).
- **PH-T079:** GIVEN an orphan test lacking requirement mapping, WHEN `tools/traceability.py --check-orphans` executes, THEN CI check fails. (Oracle: Traceability tool exit code).
- **PH-T080:** GIVEN concurrent asks competing for same stream claim, WHEN second ask attempts claim, THEN `StaleClaimError` is raised and second ask backs off. (Oracle: Exception type).
- **PH-T081:** GIVEN an unhandled exception inside a section reader during `read_snapshot`, WHEN render evaluates, THEN other sections render successfully and exit code reflects partial failure (exit 4). (Oracle: Exit code 4).
- **PH-T082:** GIVEN a terminal session receipt inserted into `bridge_evidence`, WHEN counter update affects 0 rows, THEN transaction rolls back atomically. (Oracle: Receipt count unchanged).

### Additional independent lifecycle, boundedness and provenance oracles
- **PH-T083:** GIVEN several handles including older compatible, latest LOST, and latest tainted, WHEN default selection runs after abandonment, THEN it chooses the latest compatible non-LOST/non-tainted handle; with none it creates fresh and records `prior_abandoned`. Delta, fresh-suffix, pin, projection and redirect-context paths exclude abandoned actionable bodies while retaining a bounded audit summary. (Oracle: same deterministic fixture across every path).
- **PH-T084:** GIVEN a late completion from an abandoned native request, WHEN its stale fence attempts persistence, THEN only audit evidence may be recorded; no response append, continuity revival, success transition, or dispatch occurs. Replaying the original request returns abandonment. (Oracle: stale-generation write rejection and record/offset snapshot).
- **PH-T085:** GIVEN STARTED delivery and resolver crashes at every commit boundary, WHEN recovery retries or two operators race, THEN fencing, offset, evidence and disposition are all-or-nothing and idempotent. Historical unknown-local-process policy remains an explicit pre-implementation choice. (Oracle: crash matrix and competing resolver schedule).
- **PH-T086:** GIVEN omitted and explicit caller options, ambient model/profile changes, and X→Y→X model selection, WHEN an identical request ID is retried, THEN versioned raw caller intent including omission is checked before ambient resolution; ambient-only change replays the original result, explicit intent change conflicts even if effective config matches, pending accepted identity/config stays sticky, renamed-profile compatibility follows the documented binding, and request ID is never deduplicated by prompt hash. (Oracle: replay matrix over authoritative Core key constraints and migration plan).
- **PH-T087:** GIVEN established and unresolved native handles at the retention limit, WHEN cleanup runs, THEN unresolved and protected handles cannot be evicted; original-handle retention is measured directly and a copied alias cannot satisfy the oracle; vendor and external session IDs are strictly redacted from `session show`, diag JSON, and export. (Oracle: native handle identity, retention ledger, and redaction verification).
- **PH-T088:** GIVEN a 10k-record stream with giant bodies, selective filters, pins and concurrent appends, WHEN catch-up is prepared, THEN it examines at most 50,000 candidate IDs, materializes at most 4 MiB of candidate bodies, rejects a single body above 1 MiB before fetching it, includes at most 50 redirect pins, and emits no more than the existing configured 100-record/24,000-byte answer budget. Exceeding a scan/body/pin budget fails before provider invocation; no partial projection is dispatched. `native_delta` reference: ordered records with `context_watermark < position < current_prompt.position`, redirects addressed to this peer only; omit a response only with exact native attribution; budget overflow is explicit incomplete failure, never silently a suffix. `fresh_suffix_plus_pins` reference: candidates `0 < position < current_prompt.position`; addressed redirects are pins regardless of suffix boundary; reserve eligible pins newest-first within pin/output budgets; fill remaining budget with the longest contiguous newest non-pin suffix; sort output by position and record omissions in the boundary. Limits are proposed initial hard budgets, not benchmark results; calibrate before TDD and reject above them until an intentional limit change is reviewed. (Oracle: instrumented rows/body bytes, exact projection fixtures, overflow-before-spawn, and independent full-history reference implementation).
- **PH-T089:** GIVEN the final candidate response exceeds transport limits, WHEN final Agent sends it, THEN response stays within the configured 30k transport ceiling or returns an explicit bounded refusal; local file writes alone do not prove final delivery. (Oracle: actual final delivery receipt and byte count).
- **PH-T090:** GIVEN a large stream of valid protocol frames followed by a small answer, WHEN execution stays within deadline, THEN frames are continuously drained and parsed, answer capture obeys the documented public cap, and internal tail/state still classifies completion. Discarded, malformed, oversized or incomplete terminal frames remain unresolved; protocol truncation and answer truncation are distinct. UTF-8/sanitization flags persist consistently in Delivery and StreamRecord. (Oracle: framing matrix and persisted flags; current defaults remain CLI 1,000,000 bytes and process 1,048,576 bytes).
- **PH-T091:** GIVEN attributable usage, cached usage, reported window, conflicting newer unfinished call, and wrong-turn/provider-log fixtures, WHEN telemetry extraction runs outside the write transaction and association validates inside it, THEN exactly one versioned `bridge_evidence.kind=session_telemetry` receipt is authoritative; no parallel table/kind bypasses uniqueness. Preserve independently known fields; normalize cache as inclusive; bind source/extractor version, exact turn/delivery, responding model, window and observation time. Newer unfinished calls suppress stale older samples; late flush never mutates historical output. (Oracle: association matrix and immutable result snapshots).
- **PH-T092:** GIVEN unavailable vendor evidence or a context percentage above 100%, WHEN context state is reported, THEN unknown remains unknown and anomaly is labelled without inventing vendor truth or asserting actual occupancy. (Oracle: synthetic anomaly provenance and verified-source distinction).
- **PH-T093:** GIVEN an invocation and its accepted session snapshot, WHEN callback duplication, retry, resolver race or crash occurs, THEN the append-only `bridge_invocations(delivery_id, invocation_no)` marker precedes exactly one provider invocation and its resolution is unique; first confirmed STARTED writes exactly one unique `bridge_evidence(kind='session_call')` receipt. With accepted handle/mode attribution, per-session and per-scope reuse/fresh counters each update exactly one row in that transaction. Without attribution, append one explicit unknown-mode receipt, mark scope/session coverage incomplete, and do not invent a zero or mode count. Each complete projection equals an independent fold of `session_call` evidence from its declared coverage sequence grouped by accepted handle/mode; duplicate STARTED callbacks cannot increment twice. Missing expected projection rows abort rather than silently lose the update. Immutable Core Stream records and offsets remain independently reconcilable. Deletion policy names every consuming scope and applies its retention rule. (Oracle: provider-call spy vs invocation markers, unique resolution, evidence fold vs both counter tables, unknown-attribution fixture, affected-row assertions, Core record/offset comparison and fault injection after every mutation).
- **PH-T094:** GIVEN snapshot, export, monitor and session-inspection operations, WHEN exercised under concurrent WAL writes, filtered pagination, malformed identifiers, oversized frames and sensitive sentinels, THEN each owns and closes one mode=ro/query_only snapshot with bounded query work; cursors bind filter/store/lineage/index and insertion visibility; `quick_check` differs from application recount; custom-runtime eligibility follows its predicate; every NDJSON frame is one valid ASCII line or a bounded explicit error, with vendor/external/account/path sentinels redacted. NO_COLOR and cursor restoration remain covered. (Oracle: connection lifetime, work counters, traversal matrix, parser and sentinel scans).

CLI, monitoring, and profile/session inspection contracts PH-T095–105 are in the
[CLI/Monitoring Addendum](peerhub-cli-monitoring-inspection-2026-10-14.md).

---

## 10. Technical Slices, Product Defaults, and Verification Gates

### A. Independent Implementation Slices (Technical Hardening)
1. **Slice B:** Store-format `user_version` 3→4 using a one-connection atomic migration, with actual old-binary refusal verified before deployment.
2. **Slice A:** Strict store selection (O-1 implicit `./peerhub` removal) and build-time SHA provenance.
3. **Slice P:** TOML config SSOT authority & metacharacter argv safety.
4. **Slice D:** Non-blocking controller cleanup & process supervisor deadlines.
5. **Slice Stuck6:** Audited stream resolution command (`peerhub stream resolve`).
6. **Slice C:** Bounded catch-up candidate selection, pin limits, and self-response omission.

### B. Proposed Technical Behavior (Requiring Versioned Migration)
1. **Caller Intent Replay (X-D07):** Decoupling caller intent from current runtime configuration resolution to allow harmless replay across profile updates. Replay key is `(stream, peer, request_id)`.
2. **Tri-Part Output Budgets (A-D06 / X-D11):** Continuous raw protocol drain under deadline, public answer cap applying to parsed text only, and diagnostic tail ring buffer.
3. **Attributable Telemetry Receipts (A-D04 / X-D14):** Storing measured tokens in `bridge_evidence` independently from window capacity, unclamped percentages, and strict `null` semantics for unknowns.
4. **Immutable Keyset Pagination (A-D21 / X-D18):** Deterministic history cursors over immutable sequence tuples.

### C. Conditional Decisions (Owner Choices and Implementation Gates)
1. **Gate G-EPOCH (Migration Contract):** HEAD/tag v0.15.2 and the selected DB use version 3; a proposed incompatible format is 4. Before implementation, reconcile core and migration version constants and revise/reseal actual `REQ-MIG-001/002`, `MIG-001..003` clauses. Before cutover, stop all old writers and prove the actual old executable rejects v4 without writes. No migration has run. Version 2 is explicitly rejected as the proposed format.
2. **Gate G-PARENT (Parent Directory Discovery Removal):** Owner O-1 approved removing implicit `cwd/peerhub` lookup. Removing all parent directory traversal (`*current.parents`) is an owner choice.
3. **Gate G-ALERT (Alert Exit Code 1):** Elevating active diagnostic alerts to exit code 1 is an owner choice.
4. **Gate G-RETENTION (30-Day Inactivity Timer Removal):** Dropping 30-day inactivity timer in favor of deterministic count cap ($\le 20$) is an owner choice.
5. **Gate G-RELEASE (Scope and Release Timing):** The handoff describes `0.16.0` as the release for the committed PR1–3/session health work; `docs/STATUS.md` records publication pending. That is the current release candidate, subject to the repository's full release gates. This renewal does not assign a version to its unimplemented proposals or require delaying `0.16.0` until they ship. A future breaking migration needs its own version/compatibility decision after its scope is implemented and verified.
6. **Gate G-OUTPUT (Public Capture Semantics):** Preserve the current CLI default cap of 1,000,000 bytes and process default of 1,048,576 bytes. Applying the CLI cap to parsed answer text while draining provider frames continuously changes user-visible behavior; the owner must accept that before TDD. Frame-size and parser-state limits remain separate hard safety limits.

### D. Empirical Verification Gates
1. **Pipe Cleanup Deadlock Reproduction (`PH-T014`):** Confirming whether Windows grandchild pipe inheritance causes buffer lock deadlock on HEAD before deploying platform-specific handle closure.
2. **Codex Rollout Extraction Performance:** Measure tail parsing and writer transaction duration on representative and adversarial JSON rollouts; do not treat the previously floated `<20ms` figure as an accepted or observed result. Set a budget only after the instrumentation and fixture are documented.

### E. Deferred and Dropped Features
1. **Stuck PR4 (Quota Pre-Flight):** Formally dropped. Monitoring is strictly advisory and must never block execution pre-spawn.
2. **Stuck PR5 (Automatic Timeout Settlement):** Deferred indefinitely pending empirical containment proof for external mutation channels.
3. **REUSE_UNUSED Alert & Session Line Suffixes:** Deferred from initial MV2/SR scope to reduce schema bloat.

### Handoff Question Disposition
The handoff's release-order question predates the now-recorded 0.15.2 release and 0.16.0 candidate: treat 0.16.0 as the current candidate and keep publication pending until candidate-matched release evidence exists. The optional Claude status-line consumer of `peerhub diag --line` remains outside this renewal; PH-T044 defines the CLI output only. The remaining handoff questions concern SecondBrain, MCP, or Engram and do not constrain this PeerHub design.
