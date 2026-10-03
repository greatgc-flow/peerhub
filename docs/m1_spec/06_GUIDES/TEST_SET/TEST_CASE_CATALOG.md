# Test Case Catalog

> **210 tests** — SSOT는 `test-catalog.json`입니다.

## ARCH-001 — Core import graph forbids Extension dependency

- Tier/Priority/Type: `architecture` / `P0` / `negative`
- Requirements: REQ-ARCH-001
- Dimensions: architecture, negative
- Setup: Install package with Core and first-party extensions present; expose import graph/static dependency checker.
- Action: Traverse imports reachable from Core package entrypoints.
- Oracle: No Core module imports Session Bridge, Observation, Diag, or future Extension package.
- Failure injection: none
- Live provider: False

## ARCH-002 — Core boots with Extension tables absent

- Tier/Priority/Type: `architecture` / `P0` / `negative`
- Requirements: REQ-ARCH-001, REQ-PERSIST-002
- Dimensions: architecture, negative, positive
- Setup: Create empty SQLite workspace; run only Core migrations; do not create extension tables.
- Action: Open Core store, create two Peers and one Stream, append/read one Record.
- Oracle: All Core operations succeed and no extension table is created implicitly.
- Failure injection: none
- Live provider: False

## ARCH-003 — No vendor Peer cardinality ceiling in Core

- Tier/Priority/Type: `architecture` / `P0` / `negative`
- Requirements: REQ-CARD-001
- Dimensions: architecture, negative
- Setup: Register one adapter kind identifier and generate 32 distinct Peer ids bound to it.
- Action: Persist all Peers and join them to Streams.
- Oracle: All 32 are accepted; behavior is data/DB-capacity bound, not vendor-name bound.
- Failure injection: none
- Live provider: False

## ARCH-004 — Diag dependency surface is read-only

- Tier/Priority/Type: `architecture` / `P0` / `positive`
- Requirements: REQ-DIAG-001
- Dimensions: architecture, positive, read-only
- Setup: Static dependency rule lists allowed Diag ports: query Core/Observation/log readers only.
- Action: Scan Diag imports/constructor dependencies.
- Oracle: No mutation/runtime-control port is reachable from Diag.
- Failure injection: none
- Live provider: False

## ARCH-005 — Core identity is not runtime identity

- Tier/Priority/Type: `architecture` / `P0` / `positive`
- Requirements: REQ-CORE-001
- Dimensions: architecture, identity, positive
- Setup: Prepare one Peer test object and two distinct runtime/session descriptors.
- Action: Associate descriptors sequentially with same peer_id through bridge-side mapping only.
- Oracle: peer_id remains unchanged and Core Peer serialization contains no external_session_id/process id/profile state.
- Failure injection: none
- Live provider: False

## SCH-001 — Valid Peer example validates

- Tier/Priority/Type: `schema` / `P0` / `boundary`
- Requirements: REQ-CORE-001
- Dimensions: boundary, positive, schema
- Setup: Load peer.schema.json and minimal valid Peer.
- Action: Validate with Draft 2020-12 validator.
- Oracle: Validation passes.
- Failure injection: none
- Live provider: False

## SCH-002 — Peer rejects empty id, wrong version, unknown property

- Tier/Priority/Type: `schema` / `P0` / `negative`
- Requirements: REQ-CORE-001
- Dimensions: boundary, negative, schema
- Setup: Generate three invalid Peer objects.
- Action: Validate each.
- Oracle: Each fails at the intended constraint.
- Failure injection: none
- Live provider: False

## SCH-003 — Stream accepts OPEN/CLOSED and unique members only

- Tier/Priority/Type: `schema` / `P0` / `negative`
- Requirements: REQ-CORE-002
- Dimensions: boundary, negative, positive, schema, state-transition
- Setup: Create valid OPEN and CLOSED Streams plus duplicate-member/invalid-state cases.
- Action: Validate all cases.
- Oracle: Valid cases pass; duplicate members and invalid state fail.
- Failure injection: none
- Live provider: False

## SCH-004 — Record validates position/kind/digest constraints

- Tier/Priority/Type: `schema` / `P0` / `boundary`
- Requirements: REQ-CORE-003
- Dimensions: boundary, canonicalization, ordering, positive, schema
- Setup: Create valid Record and variants with position 0, malformed kind, malformed sha256 digest.
- Action: Validate.
- Oracle: Valid passes; all invalid variants fail.
- Failure injection: none
- Live provider: False

## SCH-005 — Record rejects duplicate targets and unknown fields

- Tier/Priority/Type: `schema` / `P0` / `negative`
- Requirements: REQ-CORE-003
- Dimensions: boundary, negative, schema
- Setup: Create variants with duplicate targets and additional property.
- Action: Validate.
- Oracle: Both fail.
- Failure injection: none
- Live provider: False

## SCH-006 — Offset validates read-through >=0 and revision >=1

- Tier/Priority/Type: `schema` / `P0` / `boundary`
- Requirements: REQ-CORE-004, REQ-OFFSET-001
- Dimensions: boundary, positive, schema
- Setup: Create valid offset and negative position/revision-zero variants.
- Action: Validate.
- Oracle: Valid passes; invalids fail.
- Failure injection: none
- Live provider: False

## SCH-007 — Observation vocabulary is closed and honest

- Tier/Priority/Type: `schema` / `P0` / `boundary`
- Requirements: REQ-OBS-001
- Dimensions: boundary, positive, schema, state-transition
- Setup: For each allowed state create valid object; create state HEALTHY and LIMITLESS as invalid examples.
- Action: Validate.
- Oracle: Six allowed states pass; invented states fail.
- Failure injection: none
- Live provider: False

## SCH-008 — Resource Pool kind vocabulary validates

- Tier/Priority/Type: `schema` / `P0` / `boundary`
- Requirements: REQ-OBS-004
- Dimensions: boundary, cardinality, isolation, positive, schema, state-transition
- Setup: Create QUOTA/RATE_LIMIT/ACCOUNT/RUNTIME pools and invalid OTHER.
- Action: Validate.
- Oracle: Declared kinds pass; OTHER fails.
- Failure injection: none
- Live provider: False

## SCH-009 — Published JSON examples validate against their schemas

- Tier/Priority/Type: `schema` / `P0` / `boundary`
- Requirements: REQ-REL-003
- Dimensions: boundary, positive, schema
- Setup: Load every file under 04_SCHEMAS/examples with its declared schema.
- Action: Validate examples.
- Oracle: All examples pass.
- Failure injection: none
- Live provider: False

## SCH-010 — All production schemas are syntactically valid Draft 2020-12

- Tier/Priority/Type: `schema` / `P0` / `boundary`
- Requirements: REQ-REL-003
- Dimensions: boundary, positive, schema
- Setup: Enumerate *.schema.json.
- Action: Run Draft202012Validator.check_schema.
- Oracle: Every schema is valid.
- Failure injection: none
- Live provider: False

## CORE-001 — Peer identity survives adapter/profile/session change

- Tier/Priority/Type: `unit` / `P0` / `positive`
- Requirements: REQ-CORE-001
- Dimensions: identity, positive, unit
- Setup: Create peer cx-review with adapter_ref codex-cli; prepare two model/profile/session mappings.
- Action: Swap bridge mappings without updating Core Peer key.
- Oracle: Peer lookup by peer_id returns same identity and original created_at.
- Failure injection: none
- Live provider: False

## CORE-002 — One adapter kind can back many Peers

- Tier/Priority/Type: `unit` / `P0` / `positive`
- Requirements: REQ-CARD-001
- Dimensions: cardinality, positive, unit
- Setup: Create cx-01/cx-02/cx-review using same adapter_ref.
- Action: Persist/read all.
- Oracle: Three distinct Peer identities coexist.
- Failure injection: none
- Live provider: False

## CORE-003 — Record append assigns position server-side

- Tier/Priority/Type: `unit` / `P0` / `positive`
- Requirements: REQ-ORDER-001, REQ-CORE-003
- Dimensions: ordering, positive, unit
- Setup: Open Stream with two valid members; client append request omits position/record_id/appended_at.
- Action: Append once.
- Oracle: Returned Record has position >=1 and server-assigned durable fields.
- Failure injection: none
- Live provider: False

## CORE-004 — Record is immutable after append

- Tier/Priority/Type: `unit` / `P0` / `positive`
- Requirements: REQ-CORE-003
- Dimensions: immutability, positive, unit
- Setup: Append one Record.
- Action: Attempt supported update/mutation path or mutate returned object then reread.
- Oracle: Store exposes no Record update; reread value is unchanged.
- Failure injection: none
- Live provider: False

## CORE-005 — Read order is canonical position order

- Tier/Priority/Type: `unit` / `P0` / `positive`
- Requirements: REQ-ORDER-001
- Dimensions: canonicalization, ordering, positive, unit
- Setup: Append 5 Records with intentionally shuffled created_at values.
- Action: Read Stream Records.
- Oracle: Result is ordered strictly by position, not created_at/record_id.
- Failure injection: none
- Live provider: False

## CORE-006 — Idempotent retry returns existing Record

- Tier/Priority/Type: `unit` / `P0` / `positive`
- Requirements: REQ-IDEM-001, REQ-IDEM-004
- Dimensions: idempotency, positive, unit
- Setup: Build one canonical append request with key K.
- Action: Append same request twice.
- Oracle: Second call returns same record_id and position; stream Record count is 1.
- Failure injection: none
- Live provider: False

## CORE-007 — Idempotency key conflicts on changed semantic payload

- Tier/Priority/Type: `unit` / `P0` / `negative`
- Requirements: REQ-IDEM-002, REQ-IDEM-004
- Dimensions: idempotency, negative, semantic-boundary, unit
- Setup: Append key K with body A.
- Action: Retry K with body B.
- Oracle: Conflict is returned and Record count remains 1.
- Failure injection: none
- Live provider: False

## CORE-008 — Map key order does not change payload digest

- Tier/Priority/Type: `unit` / `P0` / `negative`
- Requirements: REQ-IDEM-003
- Dimensions: canonicalization, negative, ordering, unit
- Setup: Create semantically equal body/metadata maps with different insertion order.
- Action: Compute canonical payload digest.
- Oracle: Digests are identical.
- Failure injection: none
- Live provider: False

## CORE-009 — Server-assigned fields do not participate in canonical retry digest

- Tier/Priority/Type: `unit` / `P0` / `positive`
- Requirements: REQ-IDEM-003
- Dimensions: canonicalization, idempotency, positive, unit
- Setup: Prepare semantic request then compare with stored Record carrying record_id/position/appended_at.
- Action: Recompute digest from semantic projection.
- Oracle: Digest equals original request digest.
- Failure injection: none
- Live provider: False

## CORE-010 — Changed created_at is a semantic conflict

- Tier/Priority/Type: `unit` / `P0` / `negative`
- Requirements: REQ-IDEM-002, REQ-IDEM-003
- Dimensions: negative, semantic-boundary, unit
- Setup: Append key K with caller-created_at T1.
- Action: Retry K with same fields but created_at T2.
- Oracle: Conflict is returned; no second Record.
- Failure injection: none
- Live provider: False

## CORE-012 — Durable append success does not imply runtime exactly-once delivery

- Tier/Priority/Type: `unit` / `P0` / `negative`
- Requirements: REQ-DELIVERY-001
- Dimensions: negative, persistence, semantic-boundary, unit
- Setup: Append one user Record successfully while no Session Bridge/runtime is running.
- Action: Inspect append result and Core state.
- Oracle: Core reports durable Record only; it does not mark runtime execution/delivery success or exactly-once side effect.
- Failure injection: none
- Live provider: False

## CORE-011 — Control record kinds remain durable data

- Tier/Priority/Type: `unit` / `P0` / `positive`
- Requirements: REQ-CONTROL-001
- Dimensions: persistence, positive, unit
- Setup: Prepare one Record for each control.pause/resume/redirect/cancel/context.boundary.
- Action: Append and reread.
- Oracle: kind/body/targets are preserved exactly; Core executes no runtime action.
- Failure injection: none
- Live provider: False

## OFF-001 — Initial offset is zero through explicit absence/default contract

- Tier/Priority/Type: `unit` / `P0` / `positive`
- Requirements: REQ-CORE-004
- Dimensions: positive, unit
- Setup: Peer belongs to Stream but has no Offset row.
- Action: Read effective offset.
- Oracle: Effective read_through_position is 0 without implying completion.
- Failure injection: none
- Live provider: False

## OFF-002 — Offset CAS succeeds with current revision

- Tier/Priority/Type: `unit` / `P0` / `positive`
- Requirements: REQ-OFFSET-001
- Dimensions: positive, unit
- Setup: Current offset position 2 revision 3.
- Action: CAS expected revision 3 -> position 5.
- Oracle: Position becomes 5 and revision advances exactly once.
- Failure injection: none
- Live provider: False

## OFF-003 — Stale Offset CAS fails without mutation

- Tier/Priority/Type: `unit` / `P0` / `negative`
- Requirements: REQ-OFFSET-001
- Dimensions: concurrency, negative, time, unit
- Setup: Current revision 4.
- Action: CAS with expected revision 3.
- Oracle: Conflict; position and revision unchanged.
- Failure injection: none
- Live provider: False

## OFF-004 — Offset cannot move backward

- Tier/Priority/Type: `unit` / `P0` / `negative`
- Requirements: REQ-OFFSET-002
- Dimensions: negative, unit
- Setup: Current position 10 at current revision.
- Action: CAS to position 9.
- Oracle: Rejected; position/revision unchanged.
- Failure injection: none
- Live provider: False

## OFF-005 — Offset update does not mark task/work complete

- Tier/Priority/Type: `unit` / `P0` / `negative`
- Requirements: REQ-CORE-004
- Dimensions: negative, unit
- Setup: Workspace contains only Core tables and one Offset.
- Action: Advance Offset.
- Oracle: Only offset state changes; no completion/task/governance artifact appears.
- Failure injection: none
- Live provider: False

## PROP-001 — Repeated identical retries collapse to one Record

- Tier/Priority/Type: `property` / `P0` / `boundary`
- Requirements: REQ-IDEM-001
- Dimensions: boundary, positive, property
- Setup: Generate arbitrary valid semantic Record payload P and retry count 1..30.
- Action: Append same key+P N times.
- Oracle: Exactly one durable Record exists and every return identifies it.
- Failure injection: none
- Live provider: False

## PROP-002 — Any semantic field change conflicts under same key

- Tier/Priority/Type: `property` / `P0` / `negative`
- Requirements: REQ-IDEM-002, REQ-IDEM-003
- Dimensions: boundary, negative, property, semantic-boundary
- Setup: Generate valid P then mutate one of author/kind/body/targets/reply_to/refs/metadata/created_at/stream_id.
- Action: Append P then mutated P with same key.
- Oracle: Second append conflicts for every generated mutation.
- Failure injection: none
- Live provider: False

## PROP-003 — Canonical JSON map ordering is stable

- Tier/Priority/Type: `property` / `P0` / `boundary`
- Requirements: REQ-IDEM-003
- Dimensions: boundary, canonicalization, ordering, positive, property
- Setup: Generate nested JSON objects and randomize object key insertion order while preserving list order.
- Action: Digest each equivalent object.
- Oracle: Equivalent objects have identical digest.
- Failure injection: none
- Live provider: False

## PROP-004 — Positions are strictly increasing for committed appends

- Tier/Priority/Type: `property` / `P0` / `boundary`
- Requirements: REQ-ORDER-001
- Dimensions: boundary, ordering, persistence, positive, property
- Setup: Generate sequence of valid unique appends into one Stream.
- Action: Append and collect positions.
- Oracle: positions == sorted(unique positions) and each next > previous.
- Failure injection: none
- Live provider: False

## PROP-005 — Offset revisions increase only on successful CAS

- Tier/Priority/Type: `property` / `P0` / `boundary`
- Requirements: REQ-OFFSET-001
- Dimensions: boundary, positive, property
- Setup: Generate sequence of valid and stale expected revisions.
- Action: Apply CAS operations.
- Oracle: Revision increments exactly once per accepted write and never on rejected writes.
- Failure injection: none
- Live provider: False

## PROP-006 — Offset position never decreases across accepted history

- Tier/Priority/Type: `property` / `P0` / `boundary`
- Requirements: REQ-OFFSET-002
- Dimensions: boundary, ordering, positive, property
- Setup: Generate candidate positions around current value.
- Action: Apply with correct revision.
- Oracle: Only positions >= current can succeed.
- Failure injection: none
- Live provider: False

## SQL-001 — SQLite connection enforces WAL, foreign_keys, busy_timeout

- Tier/Priority/Type: `integration` / `P0` / `positive`
- Requirements: REQ-PERSIST-001
- Dimensions: integration, persistence, positive
- Setup: Initialize fresh workspace store.
- Action: Query PRAGMA journal_mode, foreign_keys, busy_timeout.
- Oracle: WAL enabled; foreign_keys=ON; busy_timeout > 0.
- Failure injection: none
- Live provider: False

## SQL-002 — Ordered migrations bootstrap empty database

- Tier/Priority/Type: `integration` / `P0` / `positive`
- Requirements: REQ-PERSIST-001
- Dimensions: compatibility, integration, ordering, positive
- Setup: Create zero-byte/new database.
- Action: Run migration runner once.
- Oracle: Core tables and migration metadata exist at expected current version.
- Failure injection: none
- Live provider: False

## SQL-003 — Migration runner is repeatable at current version

- Tier/Priority/Type: `integration` / `P0` / `positive`
- Requirements: REQ-PERSIST-001
- Dimensions: compatibility, integration, positive
- Setup: Database already migrated to current version.
- Action: Run migration runner again.
- Oracle: No schema/data drift and success is deterministic.
- Failure injection: none
- Live provider: False

## SQL-004 — Foreign keys reject orphan Record references

- Tier/Priority/Type: `integration` / `P0` / `negative`
- Requirements: REQ-PERSIST-001, REQ-CORE-003
- Dimensions: integration, negative, persistence
- Setup: Fresh Core DB with no matching Stream/Peer.
- Action: Attempt direct/store append referencing missing ids.
- Oracle: Write fails atomically with referential-integrity error.
- Failure injection: none
- Live provider: False

## SQL-005 — Append transaction publishes Record and position atomically

- Tier/Priority/Type: `integration` / `P0` / `positive`
- Requirements: REQ-PERSIST-001, REQ-ORDER-001
- Dimensions: integration, ordering, persistence, positive
- Setup: Valid Peer/Stream.
- Action: Inject observer connection between allocation and commit barrier.
- Oracle: Observer sees neither partial Record nor reserved visible position before commit; after commit sees complete Record.
- Failure injection: none
- Live provider: False

## SQL-006 — Read-only UoW rejects writes

- Tier/Priority/Type: `integration` / `P0` / `negative`
- Requirements: REQ-PERSIST-001, REQ-DIAG-001
- Dimensions: integration, negative, read-only
- Setup: Open read-only/query_only UoW.
- Action: Attempt INSERT/UPDATE through raw connection or mutation repository.
- Oracle: SQLite/write boundary rejects mutation.
- Failure injection: none
- Live provider: False

## SQL-007 — Committed state survives new process/store instance

- Tier/Priority/Type: `integration` / `P0` / `positive`
- Requirements: REQ-CONT-001
- Dimensions: integration, persistence, positive, state-transition
- Setup: Append Records and advance Offset, close all connections.
- Action: Open fresh store instance against same workspace.
- Oracle: Peers/Streams/Records/Offset are identical and unread calculation is correct.
- Failure injection: none
- Live provider: False

## SQL-008 — Core starts with only Core migrations after extension schema deletion

- Tier/Priority/Type: `integration` / `P0` / `positive`
- Requirements: REQ-PERSIST-002
- Dimensions: compatibility, integration, positive
- Setup: Create then remove/not-install extension tables.
- Action: Restart Core and perform basic operations.
- Oracle: Core remains operational.
- Failure injection: none
- Live provider: False

## SQL-009 — Workspace generation is durable

- Tier/Priority/Type: `integration` / `P0` / `positive`
- Requirements: REQ-FAULT-006
- Dimensions: generation, integration, persistence, positive
- Setup: Initialize workspace and read generation identity.
- Action: Restart store.
- Oracle: Generation is unchanged across normal restart.
- Failure injection: none
- Live provider: False

## SQL-010 — Restore/generation replacement is observable

- Tier/Priority/Type: `integration` / `P0` / `fault`
- Requirements: REQ-FAULT-006
- Dimensions: crash, generation, integration, persistence, positive, recovery, restart
- Setup: Create workspace snapshot identity G1 then simulate supported restore/replacement to G2.
- Action: Reopen bridge/store views.
- Oracle: Generation differs and pre-G2 owner/session tokens are considered stale.
- Failure injection: none
- Live provider: False

## CON-001 — Concurrent append produces unique ordered positions

- Tier/Priority/Type: `concurrency` / `P0` / `positive`
- Requirements: REQ-ORDER-001
- Dimensions: concurrency, ordering, positive
- Setup: Use two independent SQLite connections and barrier-start 100 appends to one Stream.
- Action: Run writers concurrently.
- Oracle: 100 Records commit; positions are unique and strictly ordered when read.
- Failure injection: none
- Live provider: False

## CON-002 — High-contention append remains corruption-free

- Tier/Priority/Type: `concurrency` / `P0` / `positive`
- Requirements: REQ-ORDER-001, REQ-PERSIST-001
- Dimensions: concurrency, positive
- Setup: 8 writers, 50 appends each, deterministic busy timeout.
- Action: Run 400 appends.
- Oracle: Every success is durable/unique; any bounded busy failure is explicit/retryable; DB integrity_check passes.
- Failure injection: none
- Live provider: False

## CON-003 — Concurrent identical idempotency retries create one Record

- Tier/Priority/Type: `concurrency` / `P0` / `positive`
- Requirements: REQ-IDEM-001, REQ-IDEM-004
- Dimensions: concurrency, idempotency, positive
- Setup: Two connections share same key+payload and synchronize before append.
- Action: Append simultaneously.
- Oracle: One logical Record/position exists; both callers resolve to it.
- Failure injection: none
- Live provider: False

## CON-004 — Concurrent same key different payload has one winner and one conflict

- Tier/Priority/Type: `concurrency` / `P0` / `negative`
- Requirements: REQ-IDEM-002, REQ-IDEM-004
- Dimensions: concurrency, negative
- Setup: Two connections share key K but payload A/B.
- Action: Append simultaneously.
- Oracle: Exactly one Record commits; other result is conflict; never two Records.
- Failure injection: none
- Live provider: False

## CON-005 — Offset CAS race has exactly one winner

- Tier/Priority/Type: `concurrency` / `P0` / `positive`
- Requirements: REQ-OFFSET-001
- Dimensions: concurrency, positive
- Setup: Two writers read same offset revision.
- Action: CAS to two different forward positions at barrier.
- Oracle: Exactly one succeeds; loser sees stale revision; final revision increments once.
- Failure injection: none
- Live provider: False

## CON-006 — Bridge claim race elects one active owner

- Tier/Priority/Type: `concurrency` / `P0` / `positive`
- Requirements: REQ-BRIDGE-002
- Dimensions: concurrency, fencing, positive
- Setup: Two Bridge instances contend for same workspace+stream+peer with no current owner.
- Action: Acquire claim concurrently.
- Oracle: Exactly one active claim is granted for the generation.
- Failure injection: none
- Live provider: False

## CON-007 — Expired Bridge claim takeover increments fencing generation

- Tier/Priority/Type: `concurrency` / `P0` / `boundary`
- Requirements: REQ-BRIDGE-002, REQ-FAULT-005
- Dimensions: boundary, concurrency, fencing, generation, positive
- Setup: Owner A claim expires under manual clock.
- Action: Owner B acquires.
- Oracle: B owns new generation > A; A token is invalid.
- Failure injection: none
- Live provider: False

## CON-008 — Stale owner heartbeat/ack is fenced

- Tier/Priority/Type: `concurrency` / `P0` / `positive`
- Requirements: REQ-FAULT-005
- Dimensions: concurrency, fencing, positive, time
- Setup: A token from old generation, B current owner.
- Action: A heartbeats and attempts delivery finalization/offset ack.
- Oracle: All stale operations fail and current state remains owned by B.
- Failure injection: none
- Live provider: False

## CON-009 — Different Streams progress independently

- Tier/Priority/Type: `concurrency` / `P0` / `positive`
- Requirements: REQ-ORDER-001, REQ-CARD-001
- Dimensions: concurrency, positive
- Setup: Concurrent writers target two Streams.
- Action: Append high volume to both.
- Oracle: Each Stream has its own ordered positions; contention does not merge sequences.
- Failure injection: none
- Live provider: False

## FLT-001 — Crash before append commit leaves no Record

- Tier/Priority/Type: `fault` / `P0` / `fault`
- Requirements: REQ-FAULT-001, REQ-ORDER-001
- Dimensions: crash, fault, negative, ordering, persistence, recovery, restart
- Setup: Crash injector barrier after validation/allocation work but before transaction commit.
- Action: Terminate operation at barrier and reopen store.
- Oracle: No uncommitted Record is visible. A subsequent clean append commits exactly once with a unique position greater than every previously committed position; a numeric gap is allowed.
- Failure injection: raise/terminate before commit barrier
- Live provider: False

## FLT-002 — Lost client response after commit recovers by idempotency

- Tier/Priority/Type: `fault` / `P0` / `fault`
- Requirements: REQ-FAULT-002, REQ-IDEM-001
- Dimensions: crash, fault, idempotency, positive, recovery, restart
- Setup: Injector commits append then drops/raises before returning result.
- Action: Retry exact request after reconnect.
- Oracle: Retry returns committed Record; count remains 1.
- Failure injection: raise after commit before client success
- Live provider: False

## FLT-003 — Runtime spawn uncertainty records MAY_HAVE_STARTED

- Tier/Priority/Type: `fault` / `P0` / `fault`
- Requirements: REQ-FAULT-003, REQ-CERTAINTY-001
- Dimensions: execution-certainty, fault, positive, recovery
- Setup: Fake runtime starts process then transport dies before start receipt is conclusive.
- Action: Bridge handles ambiguous result.
- Oracle: Execution evidence state is MAY_HAVE_STARTED; delivery is not auto-replayed.
- Failure injection: disconnect immediately after process start
- Live provider: False

## FLT-004 — MAY_HAVE_STARTED blocks automatic retry loop

- Tier/Priority/Type: `fault` / `P0` / `fault`
- Requirements: REQ-CERTAINTY-001
- Dimensions: execution-certainty, fault, idempotency, negative, recovery
- Setup: Persist ambiguous delivery evidence for unread Record.
- Action: Restart/schedule Bridge automatically.
- Oracle: No second runtime start occurs until explicit reconciliation/operator/control decision.
- Failure injection: none
- Live provider: False

## FLT-005 — Crash after terminal evidence before Offset ack resumes safely

- Tier/Priority/Type: `fault` / `P0` / `fault`
- Requirements: REQ-FAULT-004, REQ-BRIDGE-001
- Dimensions: crash, fault, positive, recovery, restart
- Setup: Fake runtime completes; response/evidence is durable; crash occurs before offset CAS.
- Action: Restart Bridge.
- Oracle: Bridge recognizes terminal evidence/idempotency and advances/reconciles without re-executing runtime.
- Failure injection: raise after evidence commit before offset CAS
- Live provider: False

## FLT-006 — Crash after runtime start with no terminal evidence never assumes NOT_STARTED

- Tier/Priority/Type: `fault` / `P0` / `fault`
- Requirements: REQ-FAULT-003
- Dimensions: crash, execution-certainty, fault, negative, recovery, restart
- Setup: Persist STARTED or ambiguous start evidence; terminate Bridge process.
- Action: Recover.
- Oracle: Recovered certainty is STARTED/MAY_HAVE_STARTED per evidence, never downgraded to NOT_STARTED.
- Failure injection: terminate bridge after start evidence
- Live provider: False

## FLT-007 — Claim expires mid-execution and stale owner cannot finalize

- Tier/Priority/Type: `fault` / `P0` / `fault`
- Requirements: REQ-FAULT-005, REQ-BRIDGE-002
- Dimensions: fault, fencing, negative, recovery, time
- Setup: A starts runtime; manual clock expires claim; B takes ownership.
- Action: A attempts terminal finalize with old token.
- Oracle: Finalize is fenced; system records uncertainty/evidence without allowing stale Offset advancement.
- Failure injection: advance manual clock beyond lease expiry
- Live provider: False

## FLT-008 — Restore generation change invalidates owner and session mapping

- Tier/Priority/Type: `fault` / `P0` / `fault`
- Requirements: REQ-FAULT-006
- Dimensions: crash, fault, generation, persistence, positive, recovery, restart
- Setup: A owns claim and has resumable session under workspace generation G1.
- Action: Restore/replacement switches to G2.
- Oracle: G1 claim/session cannot be used; fresh claim/session generation is required.
- Failure injection: workspace generation switch
- Live provider: False

## FLT-009 — Busy/locked database never produces partial append

- Tier/Priority/Type: `fault` / `P0` / `fault`
- Requirements: REQ-PERSIST-001, REQ-ORDER-001
- Dimensions: fault, positive, recovery
- Setup: Hold write lock longer than one competing attempt but within/around configured timeout variants.
- Action: Attempt append.
- Oracle: Either append commits fully after wait or explicit busy failure occurs; never partial row/offset corruption.
- Failure injection: database write lock
- Live provider: False

## FLT-010 — Observation probe error remains ERROR/UNKNOWN, never synthetic healthy zero

- Tier/Priority/Type: `fault` / `P0` / `fault`
- Requirements: REQ-OBS-001
- Dimensions: fault, honesty, negative, recovery
- Setup: Fake source raises timeout/parse error.
- Action: Capture Observation.
- Oracle: State is ERROR or UNAVAILABLE with evidence; numeric quota/health is not fabricated.
- Failure injection: source timeout/malformed payload
- Live provider: False

## BRG-001 — Bridge delivers only unread Records in position order

- Tier/Priority/Type: `integration` / `P0` / `positive`
- Requirements: REQ-BRIDGE-001, REQ-CORE-004
- Dimensions: integration, ordering, persistence, positive
- Setup: Stream positions 1..5, peer Offset=2, fake runtime call log.
- Action: Bridge performs one delivery cycle.
- Oracle: Only 3,4,5 are offered in order subject to control semantics.
- Failure injection: none
- Live provider: False

## BRG-002 — No session mapping creates fresh runtime session generation 1

- Tier/Priority/Type: `integration` / `P0` / `negative`
- Requirements: REQ-BRIDGE-003
- Dimensions: generation, integration, negative, time
- Setup: No mapping for peer+stream.
- Action: Deliver first unread Record.
- Oracle: Fake runtime create called; durable mapping stores external id and generation 1.
- Failure injection: none
- Live provider: False

## BRG-003 — Resumable matching fingerprint uses existing session

- Tier/Priority/Type: `integration` / `P0` / `positive`
- Requirements: REQ-BRIDGE-003
- Dimensions: generation, integration, positive
- Setup: Durable mapping resumable=true and adapter fingerprint matches.
- Action: Deliver next Record.
- Oracle: Runtime resume path uses same external_session_id and generation.
- Failure injection: none
- Live provider: False

## BRG-004 — Lost provider session triggers fresh catch-up generation

- Tier/Priority/Type: `integration` / `P0` / `fault`
- Requirements: REQ-BRIDGE-004
- Dimensions: crash, generation, integration, positive, recovery, restart, time
- Setup: Stored resumable mapping exists but fake runtime reports session missing.
- Action: Deliver next unread Record.
- Oracle: Bridge creates new session generation and reconstructs from durable Stream/Offset context; no lost Record.
- Failure injection: none
- Live provider: False

## BRG-005 — Fingerprint change forces fresh generation

- Tier/Priority/Type: `integration` / `P0` / `boundary`
- Requirements: REQ-BRIDGE-005
- Dimensions: boundary, generation, integration, positive, time
- Setup: Mapping fingerprint F1; current adapter/model/session fingerprint F2.
- Action: Bridge cycles.
- Oracle: Existing external session is not blindly resumed; new generation is stored.
- Failure injection: none
- Live provider: False

## BRG-006 — Response is appended as durable Record before delivery completion ack

- Tier/Priority/Type: `integration` / `P0` / `positive`
- Requirements: REQ-BRIDGE-001
- Dimensions: integration, persistence, positive
- Setup: Fake runtime returns deterministic response.
- Action: Bridge delivers one Record.
- Oracle: Response Record/evidence commit precedes Offset completion in event log; restart can recover.
- Failure injection: none
- Live provider: False

## BRG-007 — Execution evidence records certainty and runtime identifiers

- Tier/Priority/Type: `integration` / `P0` / `positive`
- Requirements: REQ-BRIDGE-001, REQ-CERTAINTY-001
- Dimensions: execution-certainty, integration, positive
- Setup: Script runtime lifecycle NOT_STARTED->STARTED->TERMINAL.
- Action: Deliver.
- Oracle: Evidence captures transitions and external session/process correlation sufficient for recovery.
- Failure injection: none
- Live provider: False

## BRG-008 — Pause durability precedes runtime interrupt

- Tier/Priority/Type: `integration` / `P0` / `positive`
- Requirements: REQ-BRIDGE-006, REQ-CONTROL-001
- Dimensions: integration, positive, state-transition
- Setup: Fake runtime logs interrupt invocation; store logs commit sequence.
- Action: Append/handle control.pause.
- Oracle: pause Record commit sequence is earlier than interrupt call; failure of interrupt does not erase intent.
- Failure injection: none
- Live provider: False

## BRG-009 — Unsupported interrupt remains durable intent with explicit evidence

- Tier/Priority/Type: `integration` / `P0` / `fault`
- Requirements: REQ-CONTROL-001, REQ-OBS-001, REQ-BRIDGE-006
- Dimensions: fault, integration, persistence, positive
- Setup: Fake RuntimeTarget declares interrupt unsupported.
- Action: Process control.cancel/pause requiring interrupt.
- Oracle: Control Record persists; runtime capability outcome is explicit unsupported/unavailable, not success.
- Failure injection: none
- Live provider: False

## BRG-010 — Two Peers on same adapter keep independent sessions

- Tier/Priority/Type: `integration` / `P0` / `positive`
- Requirements: REQ-CARD-001, REQ-BRIDGE-003
- Dimensions: cardinality, identity, integration, positive
- Setup: cx-01 and cx-02 share adapter kind and Stream.
- Action: Bridge both peers.
- Oracle: Distinct peer-scoped mappings/offsets/claims; no session id bleed.
- Failure injection: none
- Live provider: False

## OBS-001 — Measured quota uses quota observation and optional resource pool

- Tier/Priority/Type: `unit` / `P0` / `positive`
- Requirements: REQ-OBS-003, REQ-OBS-004
- Dimensions: cardinality, honesty, isolation, positive, unit
- Setup: Fake source returns quota usage for shared account.
- Action: Capture.
- Oracle: kind=quota, state=MEASURED, resource_pool_ref points to shared pool.
- Failure injection: none
- Live provider: False

## OBS-002 — Rate limit is not stored as quota

- Tier/Priority/Type: `unit` / `P0` / `negative`
- Requirements: REQ-OBS-003
- Dimensions: honesty, negative, persistence, positive, semantic-boundary, unit
- Setup: Fake source returns requests-per-minute limit event.
- Action: Capture.
- Oracle: Observation kind/semantic is rate_limit, not quota.
- Failure injection: none
- Live provider: False

## OBS-003 — Absent telemetry does not become zero/unlimited

- Tier/Priority/Type: `unit` / `P0` / `negative`
- Requirements: REQ-OBS-001
- Dimensions: negative, unit
- Setup: Source reachable but field not exposed.
- Action: Capture.
- Oracle: state=ABSENT or UNKNOWN with no fabricated numeric remaining value.
- Failure injection: none
- Live provider: False

## OBS-004 — Unavailable source is explicit

- Tier/Priority/Type: `unit` / `P0` / `positive`
- Requirements: REQ-OBS-001
- Dimensions: honesty, positive, unit
- Setup: Executable/auth/source is unavailable.
- Action: Capture.
- Oracle: state=UNAVAILABLE and evidence identifies source condition.
- Failure injection: none
- Live provider: False

## OBS-005 — Freshness is derived at read time without rewriting evidence

- Tier/Priority/Type: `unit` / `P0` / `negative`
- Requirements: REQ-OBS-002
- Dimensions: negative, time, unit
- Setup: Store MEASURED observation observed_at=T0; manual clock before and after configured TTL.
- Action: Read at T0+fresh then T0+expired.
- Oracle: First view fresh; second view STALE-derived; stored original evidence row/hash is unchanged.
- Failure injection: none
- Live provider: False

## OBS-006 — Multiple peers share one Resource Pool without copying quota into Peer

- Tier/Priority/Type: `integration` / `P0` / `negative`
- Requirements: REQ-OBS-004, REQ-CARD-001
- Dimensions: cardinality, honesty, integration, isolation, negative
- Setup: cx-01/cx-02 reference pool openai-account-X.
- Action: Store/read observations for both.
- Oracle: Pool is shared reference; Core Peer objects have no quota property.
- Failure injection: none
- Live provider: False

## OBS-007 — Independent Resource Pools do not leak observations

- Tier/Priority/Type: `integration` / `P0` / `positive`
- Requirements: REQ-OBS-004
- Dimensions: cardinality, integration, isolation, positive
- Setup: Peers bound to pool A and B.
- Action: Query each.
- Oracle: Evidence is attributed to correct pool only.
- Failure injection: none
- Live provider: False

## OBS-008 — Observation append is immutable evidence

- Tier/Priority/Type: `integration` / `P0` / `positive`
- Requirements: REQ-OBS-001, REQ-OBS-002
- Dimensions: immutability, integration, positive
- Setup: Capture one observation.
- Action: Attempt overwrite instead of append/new observation.
- Oracle: Existing evidence is unchanged; refresh creates a new observation identity.
- Failure injection: none
- Live provider: False

## DIA-001 — Diag opens SQLite in query-only/read-only mode

- Tier/Priority/Type: `integration` / `P0` / `positive`
- Requirements: REQ-DIAG-001
- Dimensions: integration, persistence, positive, read-only
- Setup: Instrument connection factory.
- Action: Run diag against populated workspace.
- Oracle: Connection is read-only/query_only and no write transaction begins.
- Failure injection: none
- Live provider: False

## DIA-002 — Diag cannot append Record

- Tier/Priority/Type: `integration` / `P0` / `negative`
- Requirements: REQ-DIAG-001
- Dimensions: integration, negative, read-only
- Setup: Expose only Diag public surface; snapshot DB hash/counts.
- Action: Request normal diagnostic report.
- Oracle: No Record/Offset/Observation count or DB content changes.
- Failure injection: none
- Live provider: False

## DIA-003 — Diag does not refresh observations or call provider runtime

- Tier/Priority/Type: `integration` / `P0` / `negative`
- Requirements: REQ-DIAG-001
- Dimensions: integration, negative, read-only, time
- Setup: Fake observation source/runtime would fail test if invoked.
- Action: Run diag.
- Oracle: No source/runtime invocation occurs; only stored evidence is read.
- Failure injection: none
- Live provider: False

## DIA-004 — Diag cannot interrupt/resume/repair/restart/route

- Tier/Priority/Type: `integration` / `P0` / `fault`
- Requirements: REQ-DIAG-001
- Dimensions: crash, integration, negative, recovery, restart
- Setup: Mutation/control fakes raise on access.
- Action: Run all supported diag views.
- Oracle: No forbidden port called.
- Failure injection: none
- Live provider: False

## DIA-005 — One malformed observation is isolated

- Tier/Priority/Type: `fault` / `P0` / `fault`
- Requirements: REQ-DIAG-002
- Dimensions: fault, isolation, positive, read-only, recovery
- Setup: Workspace contains valid sections plus one corrupted/unsupported observation payload.
- Action: Render diag.
- Oracle: Affected section reports explicit error/unknown while unrelated sections remain readable; DB is unchanged.
- Failure injection: malformed observation payload
- Live provider: False

## DIA-006 — Store read failure does not trigger repair

- Tier/Priority/Type: `fault` / `P0` / `fault`
- Requirements: REQ-DIAG-002, REQ-DIAG-001
- Dimensions: fault, isolation, negative, persistence, read-only, recovery
- Setup: Read-only store raises simulated I/O error.
- Action: Run diag.
- Oracle: Diag exits/reports failure; no migration/repair/restart/write is attempted.
- Failure injection: read I/O error
- Live provider: False

## DIA-007 — Authoritative logical state is unchanged after Diag

- Tier/Priority/Type: `integration` / `P0` / `positive`
- Requirements: REQ-DIAG-001
- Dimensions: integration, positive, read-only, state-transition
- Setup: Capture canonical Core/Observation logical-state digest and hashes of immutable input/config files; ignore SQLite transient lock/shm read-coordination artifacts.
- Action: Run Diag and close all read handles.
- Oracle: Logical state digest and immutable input/config hashes are identical; no authoritative table row/revision or provider/runtime call changes.
- Failure injection: none
- Live provider: False

## E2E-001 — Two peers using same adapter collaborate through one Stream

- Tier/Priority/Type: `e2e` / `P0` / `positive`
- Requirements: REQ-CARD-001, REQ-BRIDGE-001, REQ-ORDER-001
- Dimensions: cardinality, e2e, positive
- Setup: Fresh workspace, fake runtime adapter, two Peers, one Stream.
- Action: Append user Record, bridge peer A response, bridge peer B response.
- Oracle: Durable ordered transcript contains all Records; per-peer offsets/sessions remain independent.
- Failure injection: none
- Live provider: False

## E2E-002 — Client retry plus restart yields one logical append

- Tier/Priority/Type: `e2e` / `P0` / `fault`
- Requirements: REQ-IDEM-001, REQ-CONT-001, REQ-FAULT-002
- Dimensions: crash, e2e, idempotency, positive, recovery, restart
- Setup: Fresh workspace; inject lost response after commit.
- Action: Restart process and retry same idempotency key.
- Oracle: One Record exists and client obtains it.
- Failure injection: none
- Live provider: False

## E2E-003 — Restart catches up unread records

- Tier/Priority/Type: `e2e` / `P0` / `fault`
- Requirements: REQ-CONT-001, REQ-BRIDGE-004
- Dimensions: crash, e2e, positive, recovery, restart
- Setup: Bridge peer through position 2, append positions 3..5, stop process.
- Action: Start new process/Bridge.
- Oracle: Unread 3..5 delivered in order with no loss/duplicate logical response.
- Failure injection: none
- Live provider: False

## E2E-004 — MAY_HAVE_STARTED survives restart and blocks blind replay

- Tier/Priority/Type: `e2e` / `P0` / `fault`
- Requirements: REQ-CERTAINTY-001, REQ-FAULT-003
- Dimensions: crash, e2e, execution-certainty, negative, recovery, restart
- Setup: Inject ambiguous runtime start and persist evidence.
- Action: Restart and run scheduler.
- Oracle: No automatic second runtime start; status exposes uncertainty.
- Failure injection: none
- Live provider: False

## E2E-005 — Pause-before-interrupt ordering holds end to end

- Tier/Priority/Type: `e2e` / `P0` / `positive`
- Requirements: REQ-BRIDGE-006, REQ-CONTROL-001
- Dimensions: e2e, ordering, positive, state-transition
- Setup: Fake runtime supports interrupt and records monotonic event sequence.
- Action: Issue pause control while session active.
- Oracle: Durable pause commit precedes runtime interrupt event.
- Failure injection: none
- Live provider: False

## E2E-006 — Lost session creates new generation and reconstructs durable context

- Tier/Priority/Type: `e2e` / `P0` / `fault`
- Requirements: REQ-BRIDGE-004, REQ-BRIDGE-003
- Dimensions: crash, e2e, generation, persistence, positive, recovery, restart
- Setup: Fake session generation 1 processes earlier records then disappears.
- Action: Append new Record and run Bridge.
- Oracle: Generation 2 created; context comes from durable records/offset, not inaccessible provider memory.
- Failure injection: none
- Live provider: False

## E2E-007 — Shared resource pool is visible without contaminating Core Peer

- Tier/Priority/Type: `e2e` / `P0` / `negative`
- Requirements: REQ-OBS-004, REQ-DIAG-001
- Dimensions: cardinality, e2e, isolation, negative
- Setup: Two peers share pool; capture quota observation.
- Action: Run read-only diag.
- Oracle: Report relates both peers/pool while serialized Core Peers remain quota-free; no writes.
- Failure injection: none
- Live provider: False

## E2E-008 — Restore generation invalidates old claim/session and safely resumes

- Tier/Priority/Type: `e2e` / `P0` / `fault`
- Requirements: REQ-FAULT-006, REQ-BRIDGE-002
- Dimensions: crash, e2e, fencing, generation, persistence, positive, recovery, restart
- Setup: Create G1 claim/session, snapshot, advance state, restore/replace to G2.
- Action: Attempt G1 operations then acquire fresh G2.
- Oracle: G1 rejected; G2 can safely resume from durable state.
- Failure injection: none
- Live provider: False

## E2E-009 — Core remains useful with all first-party extensions disabled

- Tier/Priority/Type: `e2e` / `P0` / `positive`
- Requirements: REQ-ARCH-001, REQ-PERSIST-002
- Dimensions: e2e, positive
- Setup: Install/launch Core-only mode.
- Action: Create Peers/Stream, append/read Records, CAS Offset, restart.
- Oracle: All Core acceptance behaviors pass.
- Failure injection: none
- Live provider: False

## LIVE-CC-001 — Claude discovery and minimal canary

- Tier/Priority/Type: `live` / `P0` / `positive`
- Requirements: REQ-REL-002, REQ-BRIDGE-001
- Dimensions: live, positive
- Setup: Clean temporary workspace; authenticated Claude CLI available; test prompt has fixed tiny response contract.
- Action: Discover cc, create one Peer mapping, send one minimal canary through Session Bridge.
- Oracle: Provider response is captured as a durable response Record with TERMINAL evidence; secrets/prompts are sanitized from retained evidence.
- Failure injection: none
- Live provider: True

## LIVE-CX-001 — Codex discovery and minimal canary

- Tier/Priority/Type: `live` / `P0` / `positive`
- Requirements: REQ-REL-002, REQ-BRIDGE-001
- Dimensions: live, positive
- Setup: Clean temporary workspace; authenticated Codex CLI available; test prompt has fixed tiny response contract.
- Action: Discover cx, create one Peer mapping, send one minimal canary through Session Bridge.
- Oracle: Provider response is captured as a durable response Record with TERMINAL evidence; secrets/prompts are sanitized from retained evidence.
- Failure injection: none
- Live provider: True

## LIVE-AG-001 — Agy discovery and minimal canary

- Tier/Priority/Type: `live` / `P0` / `positive`
- Requirements: REQ-REL-002, REQ-BRIDGE-001
- Dimensions: live, positive
- Setup: Clean temporary workspace; authenticated Agy CLI available; test prompt has fixed tiny response contract.
- Action: Discover ag, create one Peer mapping, send one minimal canary through Session Bridge.
- Oracle: Provider response is captured as a durable response Record with TERMINAL evidence; secrets/prompts are sanitized from retained evidence.
- Failure injection: none
- Live provider: True

## LIVE-004 — Live capability mismatch is reported, never synthesized

- Tier/Priority/Type: `live` / `P1` / `positive`
- Requirements: REQ-OBS-001, REQ-BRIDGE-003
- Dimensions: live, positive
- Setup: Use provider/runtime where an optional capability (resume/interrupt) is absent or deliberately disabled by fake wrapper around real discovery.
- Action: Discover capability and attempt only through capability-aware path.
- Oracle: Capability is reported unavailable/unsupported; no false success and fallback behavior is explicit.
- Failure injection: none
- Live provider: True

## LIVE-005 — Observed CLI version is captured as evidence, not hardcoded truth

- Tier/Priority/Type: `live` / `P1` / `positive`
- Requirements: REQ-OBS-001, REQ-REL-003
- Dimensions: live, positive
- Setup: Authenticated installed provider CLIs.
- Action: Run version/discovery capture.
- Oracle: Observed versions are timestamped evidence/catalog input; mismatch fails consistency gate only where policy says pinned.
- Failure injection: none
- Live provider: True

## LIVE-006 — Live gate runs in isolated workspace and leaves no project mutation

- Tier/Priority/Type: `live` / `P0` / `negative`
- Requirements: REQ-REL-002
- Dimensions: live, negative
- Setup: Snapshot project tree; allocate temporary workspace.
- Action: Run all advertised provider canaries.
- Oracle: Only designated sanitized evidence artifact changes; source/project workspace remains unchanged.
- Failure injection: none
- Live provider: True

## REL-001 — Build distributable artifacts from clean checkout

- Tier/Priority/Type: `package` / `P0` / `positive`
- Requirements: REQ-REL-001
- Dimensions: clean-install, package, positive
- Setup: Clean source checkout with locked/declared build dependencies.
- Action: Build wheel and sdist (or project-selected equivalents).
- Oracle: Build succeeds; artifact metadata/version are consistent.
- Failure injection: none
- Live provider: False

## REL-002 — Fresh environment install and import/CLI smoke

- Tier/Priority/Type: `package` / `P0` / `positive`
- Requirements: REQ-REL-001
- Dimensions: clean-install, package, positive, time
- Setup: New virtual environment with no editable source path.
- Action: Install built wheel then run version/help/minimal Core import.
- Oracle: Installed artifact works without repository checkout.
- Failure injection: none
- Live provider: False

## REL-003 — Fresh workspace smoke plus restart

- Tier/Priority/Type: `package` / `P0` / `fault`
- Requirements: REQ-REL-004, REQ-CONT-001
- Dimensions: clean-install, crash, e2e, package, positive, recovery, restart, time
- Setup: Installed artifact in clean temp directory.
- Action: Initialize, create peers/stream, append/read/CAS, close process, reopen/read.
- Oracle: All minimal operations pass after restart.
- Failure injection: none
- Live provider: False

## REL-004 — Docs/version/schema/catalog consistency gate

- Tier/Priority/Type: `package` / `P0` / `positive`
- Requirements: REQ-REL-003
- Dimensions: compatibility, consistency, package, positive
- Setup: Built source tree/package.
- Action: Run manifest/schema/doc generation drift checks.
- Oracle: No stale generated view, version mismatch, invalid schema, or catalog drift.
- Failure injection: none
- Live provider: False

## REL-005 — Deterministic CI does not require real provider quota

- Tier/Priority/Type: `package` / `P0` / `negative`
- Requirements: REQ-REL-002
- Dimensions: honesty, negative, package
- Setup: CI environment has no provider credentials/CLIs except explicit fakes.
- Action: Run architecture through fake-runtime E2E suite.
- Oracle: Suite passes without network/provider quota; live tests are deselected.
- Failure injection: none
- Live provider: False

## REL-006 — Publish job is causally gated by live-provider job

- Tier/Priority/Type: `package` / `P0` / `positive`
- Requirements: REQ-REL-002
- Dimensions: package, positive
- Setup: Parse CI/release workflow DAG.
- Action: Inspect needs/dependencies and simulate failed live gate.
- Oracle: Publish cannot run/succeed when required live gate fails or is missing.
- Failure injection: none
- Live provider: False

## REL-007 — Release evidence bundle has checksums and coverage summary

- Tier/Priority/Type: `package` / `P0` / `positive`
- Requirements: REQ-REL-003
- Dimensions: package, positive
- Setup: Completed release candidate test outputs.
- Action: Assemble evidence manifest.
- Oracle: Manifest includes commit/version, deterministic suite, live canaries, package hashes and requirement coverage with no uncovered P0 requirement.
- Failure injection: none
- Live provider: False

## SCH-011 — All required fields reject omission/null and unsupported major schema version

- Tier/Priority/Type: `schema` / `P0` / `negative`
- Requirements: REQ-BOUND-001
- Dimensions: boundary, negative, no-partial-mutation, schema
- Setup: Load one valid object for each public schema and create mutations that omit each required field, set non-null fields to null, and change schema_version.
- Action: Validate every mutant and attempt API persistence through a transaction fixture.
- Oracle: Every invalid mutant is rejected before mutation; database row counts and revisions remain unchanged.
- Failure injection: none
- Live provider: False

## SCH-012 — Required collection fields distinguish null from empty arrays

- Tier/Priority/Type: `schema` / `P0` / `negative`
- Requirements: REQ-BOUND-003
- Dimensions: boundary, negative, schema
- Setup: Prepare valid Stream/Record fixtures.
- Action: For members/targets/refs, replace the required array with null and then with [] where allowed.
- Oracle: null is rejected; [] is accepted only where schema permits; no implicit null-to-empty coercion occurs.
- Failure injection: none
- Live provider: False

## SCH-013 — Malformed dates, digest, enum and unknown properties fail closed

- Tier/Priority/Type: `schema` / `P0` / `negative`
- Requirements: REQ-BOUND-001
- Dimensions: boundary, negative, no-partial-mutation, schema
- Setup: Prepare valid Peer/Stream/Record/Observation fixtures.
- Action: Mutate date-time, sha256 digest, closed enums and add unknown fields.
- Oracle: Validation rejects each invalid object and no persistence side effect occurs.
- Failure injection: none
- Live provider: False

## PROP-007 — Unicode/newline/special free-text round-trips losslessly

- Tier/Priority/Type: `property` / `P0` / `boundary`
- Requirements: REQ-BOUND-002, REQ-SEC-001
- Dimensions: boundary, persistence, property, security
- Setup: Generate Unicode text including Korean, emoji, combining marks, CR/LF, quotes, semicolons and SQL-like tokens.
- Action: Append as body/metadata then reopen store and read.
- Oracle: Exact code-point/string value is preserved and surrounding rows/schema are unchanged.
- Failure injection: none
- Live provider: False

## PROP-008 — Canonical digest preserves array order and scalar types

- Tier/Priority/Type: `property` / `P0` / `boundary`
- Requirements: REQ-IDEM-003
- Dimensions: boundary, canonicalization, property
- Setup: Generate semantically distinct payloads differing only by array order, 1 vs "1", true vs 1, null vs missing optional field.
- Action: Compute canonical payload digest.
- Oracle: Semantically distinct values produce distinct digests while map key ordering remains irrelevant.
- Failure injection: none
- Live provider: False

## SEC-001 — SQL/control-like payload is treated only as data

- Tier/Priority/Type: `security` / `P0` / `negative`
- Requirements: REQ-SEC-001, REQ-BOUND-002
- Dimensions: negative, persistence, property, security
- Setup: Use body/metadata containing SQL DDL/DML, shell-looking strings and markup.
- Action: Append/read through real SQLite store using normal API.
- Oracle: Payload round-trips literally; tables, row counts and schema remain intact; no command execution hook is invoked.
- Failure injection: none
- Live provider: False

## STR-001 — OPEN Stream closes with current revision and increments revision

- Tier/Priority/Type: `unit` / `P0` / `positive`
- Requirements: REQ-STREAM-001
- Dimensions: positive, state-transition, unit
- Setup: Create OPEN Stream revision N.
- Action: Close with expected revision N.
- Oracle: State becomes CLOSED and revision becomes N+1 exactly once.
- Failure injection: none
- Live provider: False

## STR-002 — Stale Stream revision update loses without mutation

- Tier/Priority/Type: `unit` / `P0` / `negative`
- Requirements: REQ-STREAM-001
- Dimensions: concurrency, negative, state-transition, unit
- Setup: Create Stream; retain stale revision after one successful mutation.
- Action: Attempt member/state mutation with stale revision.
- Oracle: Operation returns conflict; state/members/revision remain at committed winner.
- Failure injection: none
- Live provider: False

## STR-003 — CLOSED Stream rejects Record append atomically

- Tier/Priority/Type: `integration` / `P0` / `negative`
- Requirements: REQ-STREAM-001, REQ-BOUND-001
- Dimensions: integration, negative, no-partial-mutation, persistence, state-transition
- Setup: Persist CLOSED Stream with valid member Peers.
- Action: Attempt Record append using fresh idempotency key.
- Oracle: Append is rejected; no Record/idempotency/position mutation is visible.
- Failure injection: none
- Live provider: False

## STR-004 — Concurrent Stream CAS updates have one winner

- Tier/Priority/Type: `concurrency` / `P0` / `positive`
- Requirements: REQ-STREAM-001
- Dimensions: concurrency, persistence, state-transition
- Setup: Two writers read same Stream revision.
- Action: Synchronize at barrier and submit different legal mutations.
- Oracle: Exactly one mutation commits; loser receives stale revision; revision increments once.
- Failure injection: none
- Live provider: False

## STR-005 — Stream members reject duplicates and null; empty follows declared policy

- Tier/Priority/Type: `schema` / `P0` / `negative`
- Requirements: REQ-STREAM-002, REQ-BOUND-003
- Dimensions: boundary, negative, schema
- Setup: Use Stream schema fixture.
- Action: Validate duplicate members, null members, and empty members.
- Oracle: Duplicates/null reject; empty behavior exactly matches schema/default decision and is documented.
- Failure injection: none
- Live provider: False

## STR-006 — Member references enforce Peer integrity without becoming delivery order

- Tier/Priority/Type: `integration` / `P0` / `negative`
- Requirements: REQ-STREAM-002
- Dimensions: integration, negative, persistence, semantic-boundary
- Setup: Create Peers A/B and Stream members [B,A].
- Action: Persist Stream then append ordered Records and read them.
- Oracle: Unknown member ids are rejected by persistence; Record delivery/read order follows position, never member array order.
- Failure injection: none
- Live provider: False

## OFF-006 — Offset cannot advance past committed Stream head

- Tier/Priority/Type: `unit` / `P0` / `negative`
- Requirements: REQ-OFFSET-003
- Dimensions: boundary, negative, unit
- Setup: Stream head position H with Offset <=H.
- Action: CAS Offset to H+1.
- Oracle: Rejected without revision or position change.
- Failure injection: none
- Live provider: False

## OFF-007 — Empty Stream Offset resolves to zero and cannot jump positive

- Tier/Priority/Type: `integration` / `P0` / `negative`
- Requirements: REQ-OFFSET-003
- Dimensions: boundary, integration, negative, persistence
- Setup: Persist empty Stream and Peer.
- Action: Read absent Offset then try CAS/read-through 1.
- Oracle: Read-through resolves as 0; positive advance is rejected until a Record commits.
- Failure injection: none
- Live provider: False

## OFF-008 — Offsets are isolated by exact peer+stream key

- Tier/Priority/Type: `integration` / `P0` / `negative`
- Requirements: REQ-OFFSET-004
- Dimensions: integration, isolation, negative, persistence
- Setup: Create Peers A/B and Streams X/Y with independent offsets.
- Action: Advance A/X; query all other combinations.
- Oracle: Only A/X changes; nonexistent Peer/Stream references are rejected.
- Failure injection: none
- Live provider: False

## CON-010 — Offset head check and append race never skips unseen Record

- Tier/Priority/Type: `concurrency` / `P0` / `boundary`
- Requirements: REQ-OFFSET-003, REQ-OFFSET-001
- Dimensions: boundary, concurrency, persistence
- Setup: Offset at H; coordinate writer append H+1 and offset advancer.
- Action: Race append commit and CAS attempt using deterministic barriers.
- Oracle: Accepted Offset never exceeds committed head visible in the same serialization order; stale attempt retries/conflicts explicitly.
- Failure injection: none
- Live provider: False

## FLT-011 — Disk-full during append is atomic and restart-readable

- Tier/Priority/Type: `fault` / `P0` / `fault`
- Requirements: REQ-PERSIST-003
- Dimensions: fault, no-partial-mutation, persistence, recovery
- Setup: Store with injectable VFS/write fault after transaction begins.
- Action: Raise disk-full/IO error before durable commit.
- Oracle: Append reports explicit failure; after reopen there is either complete prior state or complete new commit, never partial rows/idempotency/offset.
- Failure injection: inject SQLITE_FULL / write failure before commit
- Live provider: False

## FLT-012 — Read-only filesystem rejects mutation without database replacement

- Tier/Priority/Type: `fault` / `P0` / `fault`
- Requirements: REQ-PERSIST-003
- Dimensions: fault, negative, persistence, recovery
- Setup: Open existing valid store then make DB/directory write-protected via fixture/VFS.
- Action: Attempt append and Stream mutation.
- Oracle: Explicit read-only failure; original database bytes/content remain readable after reopening read-only.
- Failure injection: inject readonly VFS/filesystem
- Live provider: False

## FLT-013 — Corrupt SQLite header/page fails explicit without auto-recreate

- Tier/Priority/Type: `fault` / `P0` / `fault`
- Requirements: REQ-PERSIST-004
- Dimensions: fault, negative, read-only, recovery
- Setup: Copy fixture DB and corrupt header/page in disposable copy.
- Action: Open Core store and run Diag.
- Oracle: Core open/Diag reports corruption; no empty replacement DB, migration, repair or write occurs.
- Failure injection: corrupt disposable DB bytes
- Live provider: False

## MIG-001 — Interrupted migration does not bump schema version early

- Tier/Priority/Type: `migration` / `P0` / `fault`
- Requirements: REQ-MIG-001
- Dimensions: compatibility, crash, migration, recovery
- Setup: Create N-version fixture and migration N→N+1 with injectable mid-step barrier.
- Action: Terminate/raise before migration transaction commit.
- Oracle: After reopen, schema reports N and data is valid for N; rerunning migration can complete once.
- Failure injection: terminate/raise mid-migration
- Live provider: False

## MIG-002 — Committed migration preserves Core records and invariants

- Tier/Priority/Type: `migration` / `P0` / `positive`
- Requirements: REQ-MIG-002
- Dimensions: compatibility, migration, persistence, positive
- Setup: Seed prior supported schema with Peer/Stream/Record/Offset fixtures.
- Action: Run supported migration to current version.
- Oracle: All logical data/digests/order/offset invariants remain valid and schema version advances exactly once.
- Failure injection: none
- Live provider: False

## MIG-003 — Future major schema and unsupported downgrade fail explicitly

- Tier/Priority/Type: `migration` / `P0` / `negative`
- Requirements: REQ-MIG-002, REQ-BOUND-001
- Dimensions: compatibility, migration, negative
- Setup: Prepare metadata indicating future major version and separately a newer DB opened by older code.
- Action: Open/migrate with current runtime.
- Oracle: Runtime refuses destructive guess/downgrade and emits actionable version error without writes.
- Failure injection: none
- Live provider: False

## MP-001 — Multi-process concurrent append preserves unique positions

- Tier/Priority/Type: `concurrency` / `P0` / `positive`
- Requirements: REQ-PERSIST-005, REQ-ORDER-001
- Dimensions: concurrency, multi-process, persistence, recovery
- Setup: Spawn multiple OS processes, each independent SQLite connection, same Stream.
- Action: Release process barrier, append unique keys concurrently, then close every process/connection and reopen the database in a fresh verifier process.
- Oracle: All successful commits are unique and strictly ordered by position; fresh verifier process reads an integrity-valid store with the same logical records; no corruption.
- Failure injection: none
- Live provider: False

## MP-002 — Multi-process identical idempotency key collapses to one Record

- Tier/Priority/Type: `concurrency` / `P0` / `positive`
- Requirements: REQ-PERSIST-005, REQ-IDEM-001
- Dimensions: concurrency, idempotency, multi-process
- Setup: Spawn multiple processes with same canonical append request.
- Action: Start concurrently.
- Oracle: Exactly one logical Record exists and all success responses identify it; no duplicate positions.
- Failure injection: none
- Live provider: False

## MP-003 — Multi-process Offset CAS has single winner

- Tier/Priority/Type: `concurrency` / `P0` / `positive`
- Requirements: REQ-PERSIST-005, REQ-OFFSET-001
- Dimensions: concurrency, multi-process, persistence
- Setup: Multiple processes read same Offset revision.
- Action: Race CAS to legal new positions.
- Oracle: At most one writer with a given expected revision succeeds; losers report stale revision.
- Failure injection: none
- Live provider: False

## BRG-011 — Pre-spawn adapter failure stays NOT_STARTED and retryable

- Tier/Priority/Type: `integration` / `P0` / `fault`
- Requirements: REQ-BRIDGE-007, REQ-CERTAINTY-001
- Dimensions: execution-certainty, fault, integration, negative, state-transition
- Setup: Fake runtime configured to fail before process/session start.
- Action: Bridge attempts delivery.
- Oracle: Evidence is NOT_STARTED with explicit failure; Offset unchanged; retry policy may safely retry.
- Failure injection: adapter pre-spawn failure
- Live provider: False

## BRG-012 — Post-start timeout becomes MAY_HAVE_STARTED, not NOT_STARTED

- Tier/Priority/Type: `integration` / `P0` / `fault`
- Requirements: REQ-BRIDGE-007, REQ-FAULT-003
- Dimensions: execution-certainty, fault, integration, negative
- Setup: Fake runtime signals started then never terminal.
- Action: Trigger execution timeout.
- Oracle: Evidence is MAY_HAVE_STARTED/STARTED according to durable start evidence; automatic replay remains blocked; Offset unchanged.
- Failure injection: timeout after runtime-start barrier
- Live provider: False

## BRG-013 — Partial output then crash does not fabricate terminal Record

- Tier/Priority/Type: `integration` / `P0` / `fault`
- Requirements: REQ-BRIDGE-008
- Dimensions: crash, execution-certainty, integration, recovery, streaming
- Setup: Fake runtime emits partial chunks and durable STARTED evidence.
- Action: Crash bridge before terminal callback.
- Oracle: No terminal response Record is appended; partial/start evidence remains; recovery does not downgrade certainty.
- Failure injection: terminate after partial output
- Live provider: False

## BRG-014 — Duplicate terminal callbacks append/finalize once

- Tier/Priority/Type: `concurrency` / `P0` / `positive`
- Requirements: REQ-BRIDGE-009, REQ-BRIDGE-015
- Dimensions: concurrency, idempotency, persistence
- Setup: Same delivery receives duplicate identical terminal callback concurrently.
- Action: Process both callbacks through bridge finalizer.
- Oracle: One response Record/finalization wins; duplicate resolves idempotently; Offset advances once.
- Failure injection: none
- Live provider: False

## BRG-015 — Restart after terminal callback before final response stays idempotent

- Tier/Priority/Type: `e2e` / `P0` / `positive`
- Requirements: REQ-BRIDGE-009, REQ-FAULT-004, REQ-BRIDGE-015
- Dimensions: e2e, idempotency, recovery, restart
- Setup: Fake runtime terminal evidence/response persisted; terminate process before client/final ack.
- Action: Restart and resume delivery cycle.
- Oracle: Existing terminal response is recognized; no runtime restart or duplicate response; Offset safely reconciles.
- Failure injection: process restart after terminal persistence
- Live provider: False

## BRG-016 — Resume failure falls back once to fresh generation

- Tier/Priority/Type: `integration` / `P0` / `negative`
- Requirements: REQ-BRIDGE-010
- Dimensions: generation, integration, negative, recovery
- Setup: Persist resumable compatible mapping; fake provider rejects resume.
- Action: Run delivery cycle.
- Oracle: Bridge records resume failure, creates exactly one fresh generation and catch-up; no infinite resume loop.
- Failure injection: provider resume rejects session id
- Live provider: False

## BRG-017 — Repeated resume/fresh-session failures terminate boundedly

- Tier/Priority/Type: `fault` / `P0` / `fault`
- Requirements: REQ-BRIDGE-010
- Dimensions: capacity, fault, negative, recovery
- Setup: Fake provider rejects resume and fresh session creation according to bounded retry policy.
- Action: Execute delivery.
- Oracle: Bridge exits with explicit failure/uncertainty after configured attempts; no unbounded loop or duplicate Records.
- Failure injection: all session acquisition attempts fail
- Live provider: False

## CTX-001 — Catch-up projection obeys injected record/byte budget in order

- Tier/Priority/Type: `unit` / `P0` / `boundary`
- Requirements: REQ-BRIDGE-011
- Dimensions: boundary, capacity, ordering, unit
- Setup: Stream with many Records and deterministic CatchUpBudget.
- Action: Build fresh-session projection after Offset.
- Oracle: Projection contains canonical ordered suffix fitting budget, marks truncation/boundary explicitly, and does not reorder.
- Failure injection: none
- Live provider: False

## CTX-002 — Catch-up projection never exceeds configured budget

- Tier/Priority/Type: `property` / `P0` / `boundary`
- Requirements: REQ-BRIDGE-011
- Dimensions: boundary, capacity, property
- Setup: Generate Streams/Record sizes and budgets including 0/1/exact boundary.
- Action: Build projection.
- Oracle: Reported/serialized projection never exceeds the declared budget and always preserves included Record order.
- Failure injection: none
- Live provider: False

## CTX-003 — Context compaction/loss recovers with bounded fresh catch-up

- Tier/Priority/Type: `e2e` / `P0` / `positive`
- Requirements: REQ-BRIDGE-011, REQ-BRIDGE-004
- Dimensions: capacity, e2e, recovery
- Setup: Fake session loses context after several Records; durable Stream/Offset intact.
- Action: Trigger next delivery.
- Oracle: Fresh session generation receives bounded catch-up projection and continues without full transcript requirement.
- Failure injection: provider reports context/session loss
- Live provider: False

## CLM-001 — Claim renew before expiry extends same generation

- Tier/Priority/Type: `unit` / `P0` / `boundary`
- Requirements: REQ-BRIDGE-012
- Dimensions: boundary, fencing, positive, time, unit
- Setup: ManualClock, active claim generation G expiring at T.
- Action: Owner renews at T-epsilon.
- Oracle: Renew succeeds, generation remains G, expiry extends deterministically.
- Failure injection: none
- Live provider: False

## CLM-002 — Takeover before expiry is rejected

- Tier/Priority/Type: `concurrency` / `P0` / `negative`
- Requirements: REQ-BRIDGE-012
- Dimensions: boundary, concurrency, negative, time
- Setup: ManualClock at T-epsilon; owner A claim active.
- Action: Owner B attempts takeover.
- Oracle: B is rejected; A remains owner; generation unchanged.
- Failure injection: none
- Live provider: False

## CLM-003 — Exact expiry boundary has deterministic takeover rule

- Tier/Priority/Type: `concurrency` / `P0` / `boundary`
- Requirements: REQ-BRIDGE-012
- Dimensions: boundary, concurrency, fencing, time
- Setup: ManualClock exactly at expiry T.
- Action: Owner A heartbeat and owner B takeover race under documented expiry comparison.
- Oracle: Outcome matches TD-19 exactly; only current generation can ack/finalize afterward.
- Failure injection: none
- Live provider: False

## CTL-001 — Cancel intent commits before terminate/kill

- Tier/Priority/Type: `integration` / `P0` / `fault`
- Requirements: REQ-CONTROL-002
- Dimensions: fault, integration, ordering, persistence
- Setup: Running fake runtime and Stream.
- Action: Issue control.cancel; instrument commit and terminate call order.
- Oracle: cancel Record commit happens-before terminate/kill invocation.
- Failure injection: none
- Live provider: False

## CTL-002 — Terminate failure leaves cancel intent durable

- Tier/Priority/Type: `fault` / `P0` / `fault`
- Requirements: REQ-CONTROL-002
- Dimensions: fault, persistence, recovery
- Setup: Fake runtime terminate raises.
- Action: Issue cancel.
- Oracle: Cancel Record remains committed; failure evidence explicit; no rollback of durable history/Offset.
- Failure injection: terminate/kill raises
- Live provider: False

## CTL-003 — Pause/cancel do not roll back Record or Offset history

- Tier/Priority/Type: `unit` / `P0` / `negative`
- Requirements: REQ-CONTROL-003
- Dimensions: negative, persistence, state-transition, unit
- Setup: Stream with Records and advanced Offset.
- Action: Append pause/cancel then resume intent.
- Oracle: Prior Records/Offset remain unchanged; controls are additional Records.
- Failure injection: none
- Live provider: False

## CTL-004 — Same-goal redirect stays in Stream and preserves history

- Tier/Priority/Type: `e2e` / `P1` / `positive`
- Requirements: REQ-CONTROL-004
- Dimensions: e2e, persistence, state-transition
- Setup: Active Stream with goal metadata and history.
- Action: Append redirect for same goal using decision helper.
- Oracle: Redirect is another Record in same Stream; old history remains addressable.
- Failure injection: none
- Live provider: False

## CTL-005 — New goal opens new Stream referencing prior Stream

- Tier/Priority/Type: `e2e` / `P1` / `positive`
- Requirements: REQ-CONTROL-004
- Dimensions: e2e, persistence, state-transition
- Setup: Existing Stream and explicit new-goal decision.
- Action: Create new direction.
- Oracle: Old Stream remains preserved/closed per policy; new Stream id differs and carries explicit prior reference.
- Failure injection: none
- Live provider: False

## OBS-009 — Freshness exact TTL boundary is deterministic

- Tier/Priority/Type: `unit` / `P0` / `boundary`
- Requirements: REQ-OBS-005, REQ-OBS-002
- Dimensions: boundary, honesty, time, unit
- Setup: ManualClock, observation capture time C, TTL D.
- Action: Read at C+D-epsilon and exactly C+D.
- Oracle: Fresh/stale result matches TD-13 boundary rule exactly without mutating stored evidence.
- Failure injection: none
- Live provider: False

## OBS-010 — Out-of-order source observed_at cannot make older capture newest

- Tier/Priority/Type: `property` / `P0` / `positive`
- Requirements: REQ-OBS-005
- Dimensions: honesty, property, time
- Setup: Generate observations where source observed_at order differs from local captured_at order.
- Action: Select latest effective observation.
- Oracle: Selection follows declared capture-time/order policy; source clock skew is retained as evidence but does not reorder local capture history incorrectly.
- Failure injection: none
- Live provider: False

## OBS-011 — Future-skewed source timestamp remains evidence, not synthetic freshness

- Tier/Priority/Type: `unit` / `P0` / `negative`
- Requirements: REQ-OBS-005
- Dimensions: boundary, honesty, negative, time, unit
- Setup: Observation source observed_at far in future but local captured_at now.
- Action: Evaluate freshness/latest.
- Oracle: Policy uses local capture basis; no negative-age unlimited freshness is invented.
- Failure injection: none
- Live provider: False

## OBS-012 — Probe failure cannot mutate Core or trigger routing

- Tier/Priority/Type: `fault` / `P0` / `fault`
- Requirements: REQ-OBS-006
- Dimensions: fault, isolation, negative, read-only
- Setup: Snapshot Core hashes/row counts and fake probe failure.
- Action: Capture observation.
- Oracle: At most explicit Observation evidence changes; Peer/Stream/Record/Offset and routing/runtime invocations remain unchanged.
- Failure injection: probe timeout/exception
- Live provider: False

## OBS-013 — Quota exhaustion leaves Core state and peer choice unchanged

- Tier/Priority/Type: `e2e` / `P0` / `negative`
- Requirements: REQ-OBS-007
- Dimensions: e2e, honesty, negative, semantic-boundary
- Setup: Active Stream/Peer; fake provider reports quota exhausted.
- Action: Run observation/bridge cycle without routing extension.
- Oracle: Quota observation records exhausted/unavailable; Core history/Offset unchanged except valid execution evidence; no automatic alternate Peer dispatch.
- Failure injection: quota exhausted response
- Live provider: False

## OBS-014 — Rate limit does not masquerade as account quota exhaustion

- Tier/Priority/Type: `e2e` / `P0` / `positive`
- Requirements: REQ-OBS-007, REQ-OBS-003
- Dimensions: e2e, honesty, semantic-boundary
- Setup: Fake runtime returns rate-limit event with separate account quota state.
- Action: Capture and render Diag.
- Oracle: Rate-limit and quota remain distinct; no synthesized quota value or reroute.
- Failure injection: none
- Live provider: False

## DIA-008 — Diag reads one consistent snapshot during concurrent append

- Tier/Priority/Type: `concurrency` / `P0` / `positive`
- Requirements: REQ-DIAG-003
- Dimensions: concurrency, isolation, persistence, read-only
- Setup: Reader begins transaction; writer appends/advances after barrier.
- Action: Render Diag from reader snapshot.
- Oracle: Report is internally consistent to one SQLite snapshot (old or new), never mixed cross-table state.
- Failure injection: none
- Live provider: False

## DIA-009 — Unreadable optional log source is isolated

- Tier/Priority/Type: `fault` / `P0` / `fault`
- Requirements: REQ-DIAG-004
- Dimensions: fault, isolation, negative, read-only
- Setup: Core DB valid; optional log source permission/error injected.
- Action: Render Diag.
- Oracle: Core/Observation sections render; log section reports explicit unavailable/error; no chmod/repair/write attempted.
- Failure injection: log open raises permission/IO error
- Live provider: False

## DIA-010 — Diag has zero provider/runtime calls even when data is stale

- Tier/Priority/Type: `e2e` / `P0` / `negative`
- Requirements: REQ-DIAG-001, REQ-OBS-006
- Dimensions: e2e, negative, read-only
- Setup: Stale observations and fake provider spies installed.
- Action: Run full Diag.
- Oracle: No provider refresh, resume, route or process invocation occurs; stale state is reported honestly.
- Failure injection: none
- Live provider: False

## SOAK-001 — Large Stream preserves ordering/idempotency across restart

- Tier/Priority/Type: `soak` / `P1` / `positive`
- Requirements: REQ-RES-001, REQ-CONT-001
- Dimensions: capacity, persistence, restart, soak
- Setup: Configurable test envelope with thousands of Records and mixed retries.
- Action: Append, restart, scan, retry sampled keys.
- Oracle: No hidden truncation/corruption; positions/order/idempotency remain valid. Runtime threshold is evidence-only, not a hard product SLA.
- Failure injection: none
- Live provider: False

## SOAK-002 — Many Peers and Streams have no vendor-specific Core ceiling

- Tier/Priority/Type: `soak` / `P1` / `positive`
- Requirements: REQ-RES-001, REQ-CARD-001
- Dimensions: capacity, cardinality, soak
- Setup: Create configurable large N Peers across same adapter kind and M Streams.
- Action: Persist/read mappings and offsets.
- Oracle: Correctness holds; any resource failure is explicit/configured, not a hardcoded provider cardinality rejection.
- Failure injection: none
- Live provider: False

## PROP-009 — Large valid Record body is never silently truncated

- Tier/Priority/Type: `property` / `P1` / `positive`
- Requirements: REQ-RES-001, REQ-BOUND-002
- Dimensions: capacity, data-integrity, property
- Setup: Generate valid bodies around chosen test-envelope boundaries.
- Action: Append/read/restart.
- Oracle: Exact body length/content survives or explicit configured size rejection occurs before mutation; never silent truncation.
- Failure injection: none
- Live provider: False

## IMP-001 — Legacy importer dry-run is source and target read-only

- Tier/Priority/Type: `migration` / `P1` / `negative`
- Requirements: REQ-IMP-001
- Dimensions: migration, negative, read-only
- Setup: Copy legacy v0.x fixture and empty M1 target.
- Action: Run importer dry-run/report.
- Oracle: Source and target hashes/rows are unchanged; report lists only declared mappings/unmapped items.
- Failure injection: none
- Live provider: False

## IMP-002 — Legacy apply imports only declared mappings and leaves source untouched

- Tier/Priority/Type: `migration` / `P1` / `positive`
- Requirements: REQ-IMP-001
- Dimensions: e2e, migration, persistence
- Setup: Legacy fixture with mapped and unmapped entities.
- Action: Run explicit apply.
- Oracle: Only declared M1 entities appear; source bytes unchanged; unmapped items reported, not guessed.
- Failure injection: none
- Live provider: False

## IMP-003 — Repeated legacy import is idempotent

- Tier/Priority/Type: `migration` / `P1` / `positive`
- Requirements: REQ-IMP-002
- Dimensions: idempotency, migration, persistence
- Setup: Apply same legacy fixture once.
- Action: Apply again.
- Oracle: No duplicate Peer/Stream/Record; counts/digests stable; report marks already imported items.
- Failure injection: none
- Live provider: False

## IMP-004 — Malformed legacy row is isolated without corrupting prior imports

- Tier/Priority/Type: `fault` / `P1` / `fault`
- Requirements: REQ-IMP-002
- Dimensions: fault, migration, recovery
- Setup: Legacy fixture contains valid rows then malformed/unmapped row.
- Action: Apply import with per-item/report boundary.
- Oracle: Previously committed valid imports remain consistent according to declared transaction granularity; malformed item reported; no partial malformed object.
- Failure injection: malformed legacy record
- Live provider: False

## REL-008 — Wheel/sdist include required runtime schemas catalogs and skills

- Tier/Priority/Type: `package` / `P0` / `positive`
- Requirements: REQ-REL-005
- Dimensions: clean-install, consistency, package
- Setup: Build wheel and sdist from clean checkout.
- Action: Inspect artifact file list and install each.
- Oracle: All declared runtime schema/catalog/skill files are present and loadable; prohibited dev/test junk is absent per packaging allowlist.
- Failure injection: none
- Live provider: False

## REL-009 — Installed artifact runs without undeclared dev dependency

- Tier/Priority/Type: `package` / `P0` / `negative`
- Requirements: REQ-REL-005, REQ-REL-001
- Dimensions: clean-install, negative, package
- Setup: Fresh venv installs runtime artifact only, excluding dev extras.
- Action: Import package, initialize workspace, validate schemas/catalogs.
- Oracle: All advertised runtime paths work; missing pytest/pyright/dev tools do not break runtime import.
- Failure injection: none
- Live provider: False

## REL-010 — Declared Python support matrix is exercised or fails metadata gate

- Tier/Priority/Type: `package` / `P0` / `positive`
- Requirements: REQ-REL-006
- Dimensions: compatibility, matrix, package
- Setup: Read Requires-Python/classifiers and CI matrix configuration.
- Action: Compare declared interpreter range against required CI jobs.
- Oracle: Every declared mandatory interpreter family has a deterministic gate, or package metadata narrows support explicitly.
- Failure injection: none
- Live provider: False

## REL-011 — Declared OS support includes Windows and path/newline smoke

- Tier/Priority/Type: `package` / `P0` / `positive`
- Requirements: REQ-REL-006
- Dimensions: compatibility, matrix, package
- Setup: CI metadata declares supported OSes; Windows temp paths include spaces/non-ASCII.
- Action: Run minimal install/workspace/append/restart smoke per supported OS matrix.
- Oracle: Every declared OS passes or is removed from declared support; Windows path/newline handling is lossless.
- Failure injection: none
- Live provider: False

## REL-012 — 109 legacy dispositions exactly match frozen call-map

- Tier/Priority/Type: `package` / `P0` / `positive`
- Requirements: REQ-LEGACY-001
- Dimensions: compatibility, consistency, package
- Setup: Load 109_COMMAND_DISPOSITION.csv and CURRENT_REPO_COMMAND_MAP_20261003.json.
- Action: Compare count, command path, order/effect and uniqueness.
- Oracle: Exactly 109 unique rows match frozen evidence 1:1; no legacy command is unclassified.
- Failure injection: none
- Live provider: False

## META-001 — Requirement dimension coverage is complete

- Tier/Priority/Type: `meta` / `P0` / `meta`
- Requirements: REQ-META-001
- Dimensions: consistency, meta, traceability
- Setup: Load requirements.json and test-catalog.json.
- Action: For each requirement, union linked test dimensions and compare with required_dimensions.
- Oracle: No required dimension is uncovered; no test references unknown requirement; declared test lists match reverse index.
- Failure injection: none
- Live provider: False

## META-002 — State-machine transition matrix has no uncovered required transition

- Tier/Priority/Type: `meta` / `P0` / `meta`
- Requirements: REQ-META-002
- Dimensions: meta, state-transition, traceability
- Setup: Load STATE_MACHINE_COVERAGE.json and test catalog.
- Action: Validate every required/forbidden transition references existing tests.
- Oracle: Every transition is COVERED or explicitly DEFERRED with non-empty trigger; no OPEN row.
- Failure injection: none
- Live provider: False

## META-003 — Exception catalog has no open or unclassified case

- Tier/Priority/Type: `meta` / `P0` / `meta`
- Requirements: REQ-META-003
- Dimensions: exception-space, meta, traceability
- Setup: Load EXCEPTION_CATALOG.json.
- Action: Validate taxonomy/category uniqueness/status/test references.
- Oracle: Every case is terminal COVERED/DEFERRED/N_A/OUT_OF_SCOPE with rationale and trigger where needed; COVERED cases have existing tests.
- Failure injection: none
- Live provider: False

## META-004 — High-risk interaction matrix is fully covered

- Tier/Priority/Type: `meta` / `P0` / `meta`
- Requirements: REQ-META-004
- Dimensions: interaction, meta, traceability
- Setup: Load INTERACTION_MATRIX.json.
- Action: Validate unique pair/interaction ids and named test references.
- Oracle: Every REQUIRED interaction has one or more existing tests; no duplicate or OPEN interaction row.
- Failure injection: none
- Live provider: False

## E2E-010 — Provider resume rejection falls back to one fresh generation end to end

- Tier/Priority/Type: `e2e` / `P0` / `negative`
- Requirements: REQ-BRIDGE-010, REQ-BRIDGE-004
- Dimensions: e2e, recovery, generation, negative
- Setup: Persist compatible resumable mapping and unread Stream Record; fake provider rejects resume but accepts a fresh session.
- Action: Run complete Bridge delivery cycle through fake runtime.
- Oracle: Exactly one resume attempt is recorded, then one fresh generation is created, bounded catch-up occurs, response is appended once, and delivery completes without loop.
- Failure injection: provider resume rejects external_session_id
- Live provider: False

## E2E-011 — Cancel intent remains durable when runtime termination fails end to end

- Tier/Priority/Type: `e2e` / `P0` / `fault`
- Requirements: REQ-CONTROL-002, REQ-CONTROL-003
- Dimensions: e2e, ordering, fault, persistence, recovery
- Setup: Running fake runtime, durable Stream/Offset snapshot, terminate hook configured to fail.
- Action: Issue control.cancel through public surface and restart process after termination failure.
- Oracle: Cancel Record is present after restart, prior Records/Offset are unchanged, failure evidence is explicit, and no rollback/destructive rewrite occurred.
- Failure injection: runtime terminate/kill hook raises
- Live provider: False

## IDEM-001 — Same key in different Streams is independent

- Tier/Priority/Type: `unit` / `P0` / `positive`
- Requirements: REQ-IDEM-004
- Dimensions: isolation, positive, unit
- Setup: Create Streams X/Y and same author A; choose idempotency key K.
- Action: Append payload P with K to X and different payload Q with K to Y.
- Oracle: Both Records append successfully and have independent positions/digests; no cross-stream conflict.
- Failure injection: none
- Live provider: False

## IDEM-002 — Same key from different authors in one Stream is independent

- Tier/Priority/Type: `unit` / `P0` / `positive`
- Requirements: REQ-IDEM-004
- Dimensions: isolation, positive, unit
- Setup: Create Stream X with authors A/B and key K.
- Action: Append A/K/P then B/K/Q.
- Oracle: Both append independently because idempotency scope includes author_peer_id.
- Failure injection: none
- Live provider: False

## IDEM-003 — Same key races independently across idempotency scopes

- Tier/Priority/Type: `concurrency` / `P0` / `positive`
- Requirements: REQ-IDEM-004
- Dimensions: concurrency, isolation
- Setup: Prepare same K across X/A and X/B and Y/A.
- Action: Race all scopes concurrently.
- Oracle: Each scope resolves independently; duplicates collapse only within identical scope; no false conflicts across scopes.
- Failure injection: none
- Live provider: False

## CORE-013 — Append request cannot set server-owned Record fields

- Tier/Priority/Type: `unit` / `P0` / `negative`
- Requirements: REQ-CORE-005
- Dimensions: boundary, negative, unit
- Setup: Build valid append request plus variants attempting record_id/position/payload_digest/appended_at injection.
- Action: Submit through public append harness/API.
- Oracle: Server-owned fields are rejected/ignored according to request schema; caller cannot force canonical stored values; no partial mutation on invalid injection.
- Failure injection: none
- Live provider: False

## SQL-011 — Server computes payload digest and stored value matches canonical request

- Tier/Priority/Type: `integration` / `P0` / `positive`
- Requirements: REQ-CORE-005, REQ-IDEM-003
- Dimensions: canonicalization, integration, persistence, positive
- Setup: Create Stream/Peer and append request without server-owned fields.
- Action: Append through real SQLite store, reopen, and recompute canonical digest from semantic request fields.
- Oracle: Stored payload_digest equals server-computed canonical digest; record_id/position/appended_at are server assigned and durable.
- Failure injection: none
- Live provider: False

## READ-001 — Read after_position boundary returns strict suffix

- Tier/Priority/Type: `unit` / `P0` / `boundary`
- Requirements: REQ-READ-001
- Dimensions: boundary, ordering, positive, unit
- Setup: Stream positions 1..5.
- Action: Read after_position 0, 3, 5.
- Oracle: Results are [1..5], [4,5], [] respectively in canonical order.
- Failure injection: none
- Live provider: False

## READ-002 — Read limit exact boundary and invalid limit are explicit

- Tier/Priority/Type: `unit` / `P0` / `negative`
- Requirements: REQ-READ-001
- Dimensions: boundary, negative, unit
- Setup: Stream with >3 Records.
- Action: Read limit 1, exact remaining count, 0 and negative.
- Oracle: Positive limits return at most limit without reorder; zero/negative are rejected explicitly and do not mutate state.
- Failure injection: none
- Live provider: False

## PROP-010 — Paged catch-up has no duplicates or skips

- Tier/Priority/Type: `property` / `P0` / `boundary`
- Requirements: REQ-READ-001
- Dimensions: boundary, ordering, property
- Setup: Generate Stream length N and positive page sizes.
- Action: Repeatedly call read_records(after_position=last_seen, limit=page_size) until empty.
- Oracle: Concatenated positions equal exactly all committed positions once and in increasing order.
- Failure injection: none
- Live provider: False

## CON-011 — Append during paged catch-up preserves monotonic no-duplicate reads

- Tier/Priority/Type: `concurrency` / `P0` / `boundary`
- Requirements: REQ-READ-001, REQ-ORDER-001
- Dimensions: boundary, concurrency, ordering
- Setup: Reader consumes page 1; writer barrier appends new Records before next page.
- Action: Continue catch-up from last seen position.
- Oracle: Reader never repeats/skips any Record <= final observed head; newly committed Records may appear only after their position and remain ordered.
- Failure injection: none
- Live provider: False

## OBS-015 — Unknown Resource Pool reference is rejected atomically

- Tier/Priority/Type: `integration` / `P0` / `negative`
- Requirements: REQ-OBS-008
- Dimensions: integration, isolation, negative, persistence
- Setup: Valid Peer/Observation but nonexistent resource_pool_ref.
- Action: Capture/persist observation.
- Oracle: Referential-integrity error; no observation row or Core mutation is committed.
- Failure injection: none
- Live provider: False

## OBS-016 — Resource Pool evidence remains isolated by pool and subject

- Tier/Priority/Type: `integration` / `P0` / `positive`
- Requirements: REQ-OBS-008, REQ-OBS-004
- Dimensions: integration, isolation, persistence, positive
- Setup: Pools P1/P2; Peers A/B with observations in each.
- Action: Query latest/list by pool and subject.
- Oracle: Only matching evidence is returned; no pool/subject cross-leak.
- Failure injection: none
- Live provider: False

## OBS-017 — Missing captured_at falls back to observed_at for freshness

- Tier/Priority/Type: `unit` / `P0` / `boundary`
- Requirements: REQ-OBS-009, REQ-OBS-005
- Dimensions: boundary, positive, time, unit
- Setup: Observation has captured_at absent and source observed_at O; ManualClock and TTL.
- Action: Evaluate before/at TTL.
- Oracle: Freshness uses O exactly under fallback policy and obeys the same TTL boundary.
- Failure injection: none
- Live provider: False

## OBS-018 — Equal capture timestamps have deterministic latest tie-breaker

- Tier/Priority/Type: `property` / `P0` / `positive`
- Requirements: REQ-OBS-009
- Dimensions: persistence, property, time
- Setup: Generate multiple observations for same subject/kind with equal captured_at and deterministic persisted capture sequence.
- Action: Reopen store repeatedly and call latest.
- Oracle: The same last-captured observation wins every time independent of source observed_at or query plan order.
- Failure injection: none
- Live provider: False

## REL-013 — Tampered package file fails checksum/manifest validation

- Tier/Priority/Type: `package` / `P0` / `negative`
- Requirements: REQ-REL-007
- Dimensions: consistency, evidence, negative, package
- Setup: Copy a validated package to temp and change one manifest-covered byte.
- Action: Run package validator/checksum verifier.
- Oracle: Validation fails and names the mismatched file; it never rewrites baseline hashes to make tampering pass.
- Failure injection: none
- Live provider: False

## REL-014 — Validator stdout and PACKAGE_VALIDATION evidence are identical

- Tier/Priority/Type: `package` / `P0` / `positive`
- Requirements: REQ-REL-007
- Dimensions: evidence, package, positive
- Setup: Run validator in clean package capturing stdout bytes/text.
- Action: Read PACKAGE_VALIDATION.txt after run.
- Oracle: Normalized line-for-line validation content is identical to console output, including RESULT and every ERROR when a negative fixture is used.
- Failure injection: none
- Live provider: False

## SCH-014 — Record body accepts every JSON value class but rejects non-JSON runtime values

- Tier/Priority/Type: `schema` / `P0` / `negative`
- Requirements: REQ-CANON-001
- Dimensions: boundary, canonicalization, negative, schema
- Setup: Prepare body variants: null, bool, finite number, string, array, object; plus bytes/set/custom object.
- Action: Pass through append serialization/validation boundary.
- Oracle: All JSON value classes are accepted; non-JSON runtime values are rejected before digest/persistence.
- Failure injection: none
- Live provider: False

## SCH-015 — NaN and Infinity are rejected as non-canonical JSON numbers

- Tier/Priority/Type: `schema` / `P0` / `negative`
- Requirements: REQ-CANON-001
- Dimensions: boundary, canonicalization, negative, schema
- Setup: Create payload bodies/metadata containing NaN, +Infinity, -Infinity.
- Action: Canonicalize/append using strict JSON mode.
- Oracle: Every non-finite number fails explicitly before idempotency row or Record mutation.
- Failure injection: none
- Live provider: False

## PROP-011 — Canonical digest is stable across strict JSON round trip

- Tier/Priority/Type: `property` / `P0` / `positive`
- Requirements: REQ-CANON-001, REQ-IDEM-003
- Dimensions: canonicalization, positive, property
- Setup: Generate valid JSON payload trees with bounded depth and Unicode strings.
- Action: Canonicalize directly, serialize strict JSON, parse, canonicalize again.
- Oracle: Digests are identical after round trip; no serializer-specific whitespace/key order affects digest.
- Failure injection: none
- Live provider: False

## BRG-018 — Session mapping survives store/process reopen exactly

- Tier/Priority/Type: `integration` / `P0` / `positive`
- Requirements: REQ-BRIDGE-013, REQ-BRIDGE-003
- Dimensions: generation, identity, integration, persistence, restart
- Setup: Persist mapping with external_session_id, generation G, fingerprint, binding, resumable/state/last_seen.
- Action: Close store/process and reopen via fresh harness instance.
- Oracle: All mapping fields are identical; next compatible delivery resumes generation G rather than recreating generation 1.
- Failure injection: none
- Live provider: False

## MP-004 — Multi-process Bridge claim race elects one owner

- Tier/Priority/Type: `concurrency` / `P0` / `positive`
- Requirements: REQ-BRIDGE-002, REQ-BRIDGE-014, REQ-PERSIST-005
- Dimensions: concurrency, fencing, multi-process, persistence
- Setup: Spawn independent Bridge processes/connections for same workspace+stream+peer.
- Action: Release barrier and acquire claim simultaneously.
- Oracle: Exactly one active generation/owner exists; losers cannot append response/finalize/ack.
- Failure injection: none
- Live provider: False

## FLT-014 — Stale owner cannot append late response after takeover

- Tier/Priority/Type: `fault` / `P0` / `fault`
- Requirements: REQ-BRIDGE-014, REQ-FAULT-005
- Dimensions: concurrency, fault, fencing, persistence
- Setup: Owner A starts runtime; claim expires; owner B takes generation G+1.
- Action: A receives a late terminal response and attempts response append/finalize using stale token.
- Oracle: A is fenced before any response Record/Offset mutation; uncertainty/late evidence may be recorded only through safe non-authoritative evidence path.
- Failure injection: late terminal callback to stale generation
- Live provider: False

## E2E-012 — Two Bridges recovering after crash still deliver once

- Tier/Priority/Type: `e2e` / `P0` / `positive`
- Requirements: REQ-BRIDGE-014, REQ-BRIDGE-002, REQ-CONT-001
- Dimensions: concurrency, e2e, fencing, restart
- Setup: Crash previous owner with unread Record; start two recovery Bridge processes against same store.
- Action: Both attempt recovery/delivery.
- Oracle: One claim generation wins and exactly one runtime delivery/response append occurs; loser remains fenced.
- Failure injection: simultaneous recovery processes
- Live provider: False

## BRG-019 — Conflicting duplicate terminal callbacks do not overwrite terminal truth

- Tier/Priority/Type: `concurrency` / `P0` / `negative`
- Requirements: REQ-BRIDGE-015
- Dimensions: concurrency, idempotency, negative, persistence
- Setup: One delivery receives terminal response R1 then concurrent/delayed different semantic response R2 with same delivery identity.
- Action: Process both terminal callbacks.
- Oracle: First committed terminal truth remains immutable; identical repeats are idempotent, differing repeat is explicit conflict/evidence and cannot replace response/Offset result.
- Failure injection: none
- Live provider: False

## CERT-001 — MAY_HAVE_STARTED cannot be cleared by time or restart metadata alone

- Tier/Priority/Type: `unit` / `P1` / `negative`
- Requirements: REQ-CERTAINTY-002, REQ-CERTAINTY-001
- Dimensions: negative, restart, state-transition, unit
- Setup: Persist MAY_HAVE_STARTED evidence without reconcile control.
- Action: Advance ManualClock and reopen store.
- Oracle: Original delivery remains MAY_HAVE_STARTED and automatic retry stays blocked.
- Failure injection: none
- Live provider: False

## CERT-002 — Durable reconcile.retry authorizes a new linked attempt without rewriting old evidence

- Tier/Priority/Type: `integration` / `P1` / `positive`
- Requirements: REQ-CERTAINTY-002
- Dimensions: integration, persistence, positive, state-transition
- Setup: Delivery D has MAY_HAVE_STARTED; Stream receives control.reconcile with decision RETRY referencing D.
- Action: Bridge consumes control and creates next attempt generation.
- Oracle: D evidence remains immutable MAY_HAVE_STARTED; a distinct new attempt starts with its own identity/certainty and audit link to reconciliation Record.
- Failure injection: none
- Live provider: False

## CERT-003 — Reconcile retry survives restart and does not double-authorize

- Tier/Priority/Type: `e2e` / `P1` / `positive`
- Requirements: REQ-CERTAINTY-002
- Dimensions: e2e, idempotency, persistence, restart
- Setup: Persist MAY_HAVE_STARTED and one reconcile.retry; crash after reconciliation commit before new attempt starts.
- Action: Restart two recovery cycles.
- Oracle: Exactly one new attempt is authorized/claimed from the reconciliation decision; old evidence remains unchanged and duplicate consumption is idempotent.
- Failure injection: restart after reconcile commit
- Live provider: False
