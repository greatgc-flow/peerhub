# Post-Phase-0 Roadmap R1

Status: updated planning document, reflecting live Phase 1 implementation
(Slices 1-4). Originally drafted 2026-07-31 after Phase 0's closure;
updated 2026-08-01. Does not itself authorize any of the paths below --
each remains gated by its own future decision.

## Where things stand

Path B from the original roadmap ("Begin real TDD implementation") has
already been taken. Phase 1 implementation is actively underway, heavily
documented, and rigorously tested (160/160 passing at HEAD). The
following slices are built:

- **Slices 1-3:** Dispatch, session lease, request/attempt reducers, and
  command idempotency.
- **Slice 4:** Health/admission + routing (HR+RT), completed through
  Step 7, including migrations, telemetry projection, and full
  fault-boundary tests.

See `SLICE4-KICKOFF-R1.md` and prior phase-kickoff documents for the
full ledger of decisions, reducers, and schemas built. Everything below
represents remaining forward work or explicitly deferred backlog, not a
punch list of currently-broken features.

## Remaining backlog (named, not started)

1. **R3 backlog #2-15** (`DP06-DT01-DT06-CLASSIFICATION-SPEC-R1.md`) --
   13 edge-case items: out-of-order stream events, terminal-event
   precedence, `TREE_STATE`/`CANCEL_ACK` schemas, idempotency binding,
   unterminated-script classification, parse/version/schema-negotiation
   failure classification, exit-code domain, clock validation, enum
   closure, `MAY_HAVE_STARTED` vs `UNKNOWN`, multiple `CLEANUP_ERROR`
   handling, cleanup-degrading-success, identity-reuse.
2. **SL-01-06 spec's own 5 OPEN items** -- full SL-06 recovery-policy
   matrix (trigger->decision mapping), `lease_kind` taxonomy,
   process-bound vs principal-bound leases, SL-03's reject-vs-replan
   default, session-binding self-corruption (needs its own quarantine
   path).
3. **Health/Routing (HR/RT) items explicitly deferred by Slice 4:**
   - **HR-03's classification-level scope mapping & clearance-authority:**
     `SLICE4-KICKOFF-R1.md` explicitly deferred the failure-class to
     degradation/quarantine-policy mapping and authority semantics
     (`kind`/`opened_by`/`required_clearer`).
   - **HR-02 automatic safe-revalidation:** out of scope for Phase 1
     (no real adapter probe exists yet).
   - **RT-06 admission-snapshot drift detection:** narrowed to
     configuration-revision only; full drift detection is deferred
     until a Phase 0 fixture vector is separately ratified.
   - **RT-04 general weighting policy:** Slice 4 implements only
     boolean eligibility and deterministic equal-weight selection;
     cost/latency/terminal-tier weighting is deferred to a future
     versioned `RoutingPolicy`.
   - **Backoff-jitter canonicalization:** the exact byte/modulo
     derivation for the frozen ±20% jitter ladder remains deferred.
4. **Named-but-unratified items in the AC track**: the static 90-action
   inventory ratification, action-fixture linkage, public error codes,
   safe-abort classification, rollback vocabulary, real-OS integration
   (`authority-proof-status-v1.json`).
5. **Slice 4 implementation gaps** (found by an independent two-peer
   cross-review after Slice 4's Step 7 completion, 2026-08-01 addendum;
   both left deliberately open, not fixed, pending direction):
   - **Configuration-digest binding is ratified but unenforced.** The
     Step 6B pre-service addendum requires `HealthScopeMembershipSnapshot`
     bound to the same `configuration_revision`/`configuration_digest`
     as `ConfigurationSnapshot` -- but only revision equality is checked
     anywhere; `AdmissionSnapshot` doesn't persist a digest field at
     all. Fixing this needs a schema change plus a new migration.
   - **`peerhub/runtime.py` was never wired up for Slice 4.**
     `create_runtime()` still constructs only the state store,
     governance broker, and dispatch service. `TelemetryProjector`,
     `HealthService`, `RoutingService`, and `ApplicationWorkflows` are
     fully built and fully tested, but have no production-reachable
     path -- every test in Slice 4 constructs them directly, and
     nothing else does.

Item 5's original entries from the pre-Slice-4 draft of this roadmap --
"no real package implementation authorized" and "Phase 0 exit/cutover
execution" -- are superseded: both are resolved by Slices 1-4 actually
existing now, and have been removed from this list.

## Three paths forward

### Path A -- Begin Slice 5

Scope and design the next architectural slice in the Phase 1 sequence.
Advances implementation momentum but leaves both Slice 4 gaps (item 5
above) and the older Phase-0-level backlog (items 1-4) open.

### Path B -- Close the 2 Slice-4-specific gaps first

Fix the configuration-digest binding (schema change + migration) and
wire Slice 4 into `runtime.py`, before adding new architectural surface
area. Bounded, already-diagnosed work; the user has separately decided
(2026-08-01) to leave both open as backlog for now rather than fix them
in the same pass that found them -- revisit that decision here before
choosing this path.

### Path C -- Clear the older Phase-0-level backlog

Work through items 1-4 (R3 edge cases, SL-01-06's open items, HR/RT
deferrals, the AC track). Bounded, already-scoped design questions,
similar in size to Phase 0's own HR-03/SL-01 closure work. Reduces
technical debt without adding new implementation surface.

## Recommendation

No strong recommendation is made here -- this is a genuine user decision
(altitude: direction, not implementation detail), not something to
default without asking. None of the three paths are mutually exclusive;
any can start whenever, and partial progress on one doesn't block
another. Each represents a real scope commitment that should be entered
deliberately.

## Process note for whichever path is chosen

This project's arc -- Phase 0's closure and Phase 1's Slices 1-4 --
repeatedly found that "the original round already checked, nothing more
to find" undersold what a fresh document sweep or an independent second
peer audit could still catch (DP-06, CJ-01..06, RT-03, HR-03's policy
gap, and Phase 0's final stale-capture defect were each found this way).

Slice 4's own closing rounds are a fresh, concrete confirmation of the
same pattern: an admission idempotency crash found by one independent
review, then a follow-up round dispatched to two peers in parallel
turned up 2 further real regressions in that very fix (one peer found
them, the other reported nothing further -- exactly why asking both
mattered) plus the 2 open items 5 above. Whichever path is chosen next
should keep using two independent peer passes for any genuinely
ambiguous design decision, plus direct verification of the most
consequential claims before committing -- this discipline is what
caught every real defect in this arc, including in its own closing
rounds.
