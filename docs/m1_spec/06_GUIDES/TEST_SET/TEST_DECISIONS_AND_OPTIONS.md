# Test Decisions / Options

모호한 계약을 테스트가 임의로 발명하지 않도록, RED 작성 전에 사용할 기본 결정을 명시합니다. 각 항목은 향후 명시적 설계 결정으로 교체 가능하지만 M1 구현 중 묵시적으로 바꾸지 않습니다.

## TD-01 — Record position scope
- **M1 default:** Per Stream; unique and strictly increasing among committed Records; gaps allowed.
- **Why:** Avoid global bottleneck and avoid requiring gapless allocation semantics after rollback/crash.
- **Option:** Alternative: global sequence (rejected for M1 complexity).

## TD-02 — Canonical payload digest
- **M1 default:** Canonical JSON of client semantic fields: stream_id, author_peer_id, kind, body, targets, reply_to, refs, metadata, created_at, schema_version. Exclude record_id, position, payload_digest, appended_at. Object keys sorted; array order preserved.
- **Why:** Makes retries deterministic without server-field contamination.
- **Option:** Alternative: include server fields (rejected because retry cannot know them).

## TD-03 — Offset monotonicity
- **M1 default:** read_through_position may stay equal or move forward; moving backward is rejected. Successful state change increments revision.
- **Why:** Read-through cursor should not silently forget delivery progress.
- **Option:** Alternative: backward seek as mutation (defer to future explicit rewind extension/control).

## TD-04 — Bridge fencing
- **M1 default:** Claim token includes workspace generation + claim generation + owner id. Expired takeover increments generation; stale heartbeat/finalize/offset-ack fails.
- **Why:** Prevents stale process from finalizing duplicate work after takeover.
- **Option:** Alternative: timestamp-only lease (rejected due stale-owner race).

## TD-05 — Pause ordering
- **M1 default:** Durably append/commit control.pause before invoking runtime interrupt/steer. Interrupt failure is evidence, not rollback of user intent.
- **Why:** Preserves intent across crash and unsupported provider capability.
- **Option:** Alternative: interrupt first (rejected because crash can lose intent).

## TD-06 — Freshness
- **M1 default:** Persist source evidence immutable; derive STALE at read time from kind/policy TTL and manual/system clock. Do not rewrite old observation just to mark stale.
- **Why:** Evidence history remains auditable and freshness naturally changes with time.
- **Option:** Alternative: background rewrite/daemon (rejected for M1).

## TD-07 — Diag isolation
- **M1 default:** Render independent sections; one malformed/missing observation becomes section-level ERROR/UNKNOWN while other readable sections continue. Never repair as part of diag.
- **Why:** Read-only diagnostics should maximize evidence without creating side effects.
- **Option:** Alternative: fail entire report at first bad row (allowed only for corrupted Core store that prevents safe reading).

## TD-08 — Live release gate
- **M1 default:** Normal push/PR CI is deterministic and quota-free. A release/publish candidate has a separate required live-canary job for each advertised provider, and publish depends on that job.
- **Why:** Combines reproducible CI with empirical provider proof.
- **Option:** Alternative: live on every PR (rejected due quota/flakiness).

## TD-09 — Stream revision / close
- **M1 default:** Stream state/member mutation is revision-CAS. `OPEN -> CLOSED` is allowed; append to `CLOSED` is rejected. Reopen is not an M1 API.
- **Why:** `revision` must have concurrency meaning, and `CLOSED` must be semantically observable.
- **Option:** Explicit reopen control may be introduced later; tests mark it DEFERRED until then.

## TD-10 — Offset upper bound
- **M1 default:** `read_through_position <= committed Stream head`; empty Stream head is 0.
- **Why:** Advancing beyond durable history would silently skip future Records.
- **Option:** Administrative skip/seek belongs to a future explicit control/repair extension.

## TD-11 — Execution certainty monotonicity
- **M1 default:** durable certainty never downgrades (`STARTED` cannot become `NOT_STARTED`; `TERMINAL` stays terminal). Pre-spawn failure is `NOT_STARTED`; any ambiguous post-start failure is at least `MAY_HAVE_STARTED`.
- **Why:** Recovery must prefer uncertainty over duplicate side effects.

## TD-12 — Catch-up budget
- **M1 default:** Catch-up projection receives an injected budget (record/byte/token abstraction); it returns an ordered bounded projection plus explicit truncation/boundary metadata.
- **Why:** Context limits are normal lifecycle, not a reason to re-inject unbounded transcript.
- **Option:** Exact token estimator remains RuntimeTarget-specific.

## TD-13 — Observation freshness clock
- **M1 default:** Local `captured_at` is the freshness/latest-selection clock when present; `observed_at` remains source evidence. `age >= TTL` is STALE. Source clock skew must not create negative-age indefinite freshness.
- **Why:** Freshness must be deterministic under provider clock skew.

## TD-14 — Schema compatibility
- **M1 default:** Unknown/future major schema is rejected explicitly. Supported migrations are ordered and transactional; automatic destructive downgrade is prohibited.
- **Why:** Compatibility guesses corrupt durable truth.

## TD-15 — Storage fault policy
- **M1 default:** Busy/full/readonly/corrupt storage fails closed. Core/Diag never silently recreate or auto-repair authoritative state.
- **Why:** Visible failure is safer than false recovery.

## TD-16 — Legacy importer
- **M1 default:** Explicit side-by-side importer, dry-run/report first, source read-only, apply idempotent, unknown legacy semantics reported rather than guessed.
- **Why:** Existing v0.x is evidence/source material, not the new Core schema.

## TD-17 — Free-text / canonicalization
- **M1 default:** Free text is preserved byte/string-semantically by the chosen UTF-8 serialization; no silent trim/normalization. Canonical JSON sorts object keys but preserves arrays/scalar types.
- **Why:** Human/AI content and retry identity must remain stable.

## TD-18 — Support matrix truthfulness
- **M1 default:** Package metadata may advertise only Python/OS combinations exercised by required CI or explicitly documented as unverified/unsupported.
- **Why:** A developer-host-only PASS is not portable evidence.

## TD-19 — Lease exact-expiry rule
- **M1 default:** A claim is expired when `now >= expires_at`. At the exact boundary, takeover may win; any token from the prior generation is fenced.
- **Why:** Boundary behavior must be deterministic and testable without sleeps.

## TD-20 — Idempotency key scope
- **M1 default:** uniqueness scope is `(stream_id, author_peer_id, idempotency_key)`.
- **Why:** Retries for one author in one Stream collapse, while independent Peers/Streams may safely reuse client-generated keys.
- **Option:** A future client/request identity may replace author scope if append-on-behalf-of semantics are introduced.

## TD-21 — Server-owned Record fields
- **M1 default:** `record_id`, `position`, `payload_digest`, `appended_at` are assigned/computed by the server/store boundary and are absent from append requests.
- **Why:** Clients must not forge durable order or retry digest.

## TD-22 — Record read/catch-up boundary
- **M1 default:** `read_records(stream, after_position, limit)` is exclusive of `after_position`; supplied limit must be positive. Repeated paging from last returned position yields no duplicates/skips. New commits may appear in later pages.
- **Why:** Catch-up should be simple and monotonic without snapshot-token complexity.

## TD-23 — Observation equal-time ordering
- **M1 default:** freshness/latest uses local `captured_at` when present, else `observed_at`. Equal effective capture times are broken by a deterministic persisted capture ordinal (implementation-private; not source timestamp).
- **Why:** SQLite query order and provider clocks must not make `latest()` nondeterministic.

## TD-24 — Strict JSON domain
- **M1 default:** Record semantic payload is strict JSON data. Non-JSON runtime objects and non-finite numbers (`NaN`, `±Infinity`) are rejected before canonicalization/persistence.
- **Why:** Cross-runtime canonical digest must not depend on permissive serializer extensions.

## TD-25 — Fencing applies to all authoritative delivery writes
- **M1 default:** current claim token/generation is checked before response Record append, terminal finalization and Offset acknowledgement. Late/stale runtime callbacks may become evidence, but cannot mutate authoritative delivery completion state.
- **Why:** Fencing only the final Offset while allowing a stale response append still permits duplicate/conflicting durable output.

## TD-26 — Uncertain execution reconciliation
- **M1 default:** `MAY_HAVE_STARTED` is never cleared by time/restart. A durable `control.reconcile` Record may explicitly authorize `RETRY`; this creates a **new attempt identity/generation** and leaves the original evidence immutable.
- **Why:** This keeps blind replay forbidden without turning uncertainty into a permanent dead end.
- **Deferred:** Additional decisions such as ACCEPT/ABANDON can be added only with explicit Offset/effect semantics.

