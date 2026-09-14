# HR-01..03 Health-Recovery Classification Spec R1

Status: ratified CANDIDATE-tier evidence-scoping record. Produced by an
unlimited unanimous adversarial mutual-critique process between ag.deepthink
and cx.deepthink (2 rounds + a 2-part Final Call ACK, 2026-07-29), reconciled
by cc, unanimous ACK from both peers (cx's first Final Call response was
conditional, flagging 3 concrete schema corrections; both incorporated below
and confirmed ACK by both peers in a follow-up round). Does not amend
`RUNTIME-HEALTH-SEMANTICS-R1.md`, authorize real package implementation, or
convert any status this document does not explicitly name.

## Why this document exists

`CONTRACT.md` allocates exactly one fixture ID each to HR-01 ("fresh
readiness evidence produces the defined open/admission projection"), HR-02
("expired evidence becomes stale, never silently healthy"), and HR-03
("measured integrity/provider failure reaches the correct
degradation/quarantine policy"). But `RUNTIME-HEALTH-SEMANTICS-R1.md`'s
"Required controlled fixtures" section demands the whole HR family cover ten
distinct scenarios, and its outcome model names at least five distinct
"measured failure" categories (`EXECUTABLE_UNAVAILABLE`,
`ENVIRONMENT_UNAVAILABLE`, `AUTH_UNAVAILABLE`, `NETWORK_UNAVAILABLE`,
`PROVIDER_UNAVAILABLE`) plus two usage/admission outcomes (`QUOTA_EXHAUSTED`,
`RATE_LIMITED`) that are not covered by HR-04/05/06's already-committed scope
(cooldown/quarantine authority-clearance, one-probe-grant, CAS-fingerprint
transition). The real HR-03 legacy capture only directly observes one of
these categories. This is a genuine document-vs-document scope conflict,
compounded by `RUNTIME-HEALTH-SEMANTICS-R1.md`'s own header ("No package
implementation is authorized") raising a real question about whether Phase 0
fixture work is even in scope at all.

## Process summary

Round 1: ag and cx independently derived a classification from `CONTRACT.md`,
`RUNTIME-HEALTH-SEMANTICS-R1.md`, `RUNTIME-HEALTH-DRIFT-ADDENDUM-2026-07-28.md`,
`RUNTIME-HEALTH-DRIFT-2026-07-28.md`, and the three real legacy captures
(`HR-01/02/03.json`), barred from reading `runner.py` or `domain/health.py`'s
implementation. Both independently converged on the two biggest structural
questions: (a) "No package implementation is authorized" blocks real health
package source, not Phase 0 controlled-fake fixture evidence -- consistent
with how every other fixture group this session treated draft-status contract
prose as binding scope without it authorizing real implementation; (b) HR-03
must be a multi-scenario fixture under one `fixture_id`, not a single
representative case. They diverged on implementation detail: ag's draft
baked an unratified auto-revalidation assumption into HR-02, fabricated
per-failure-class policy-action values in HR-03 with no grounding, covered
only 3 of the needed failure categories, and relabeled the real legacy
timeout capture as `NETWORK_UNAVAILABLE` without stage-specific evidence.

Round 2 (cross-critique): ag conceded fully on four of five identified
divergences. On the fifth (whether HR-04/05/06 already cover 3 of
`RUNTIME-HEALTH-SEMANTICS-R1.md`'s ten required items), ag incorrectly
claimed none were covered; cx more cautiously proposed a conditional answer
pending an "evidence audit." cc independently verified directly against the
actual committed fixture files (not trusting either peer's claim) that HR-05
already exercises single-flight contention (two probe attempts, exactly one
grant) and HR-06-NEG-01 already exercises a fenced late result (a
stale-fingerprint probe result expected to be a no-op) -- confirming cx's
more careful position and correcting ag's. Only "revalidation unsupported by
an adapter" remained genuinely uncovered.

Final Call, part 1: cc proposed folding the one genuinely uncovered item into
HR-02's own positive case (the OBS-grounded "adapter does not declare this
probe safe" branch, per `RUNTIME-HEALTH-SEMANTICS-R1.md`'s Revalidation
protocol step 1) rather than inventing a new ID or an ungrounded
auto-probe-succeeds branch. ag ACKed unconditionally. cx ACKed conditionally,
flagging three concrete schema corrections: (1) HR-01/02 need an explicit
`observed_at` field to make freshness/expiry derivable at all; (2) HR-01's
negative fixture needs its own independently-correct oracle answer, not just
the faulty one; (3) HR-03's `expected_classification` field was itself
oracle-answer injection -- exactly the self-reported-script risk the
DomainOracle framework exists to prevent -- and needed replacing with
factual stage-outcome data the oracle derives a classification from.

Final Call, part 2: cc incorporated all three corrections and both peers
ACKed unconditionally.

## Authority tagging (adopted, both peers unanimous, same framework as DP-06/SL)

- `MUST` -- stated directly by `CONTRACT.md`'s three one-liners, or by
  `RUNTIME-HEALTH-SEMANTICS-R1.md`'s explicit rules (e.g. "never `READY`" for
  unclassifiable failures).
- `OBS` -- directly observed in a legacy capture record (`HR-01/02/03.json`).
- `CANDIDATE` -- a reasoned net-new proposal, explicitly unratified as *the*
  only correct schema, adopted here as the implementation baseline.
- `OPEN` -- genuinely unresolved; recorded as backlog, does not block a
  scoped `SPEC_FAITHFUL` fixture for one concrete scenario (or, for HR-03, a
  concrete finite scenario matrix) per ID.

## Ratified design

### Authorization-status resolution

`RUNTIME-HEALTH-SEMANTICS-R1.md`'s "No package implementation is authorized"
header blocks writing real runtime health-package source code. It does not
block Phase 0 controlled-fake fixture evidence, which is explicitly
evidence-gathering infrastructure under `CONTROLLED-FAKE-RUNNER-CONTRACT-R2`,
not "package implementation" -- the same reading this session has applied to
every other draft-status doc governing a fixture group.

### HR-04/05/06 coverage correction (verified against the actual committed code)

Of `RUNTIME-HEALTH-SEMANTICS-R1.md`'s ten required scenarios, three
("probe single-flight contention," "fenced late result," "revalidation
unsupported by an adapter") are not obviously covered by HR-04/05/06's
`CONTRACT.md`-stated scope. Direct inspection of the committed fixtures
confirms: `HR-05.json` already exercises single-flight contention (two
`probe_attempts` against `grant_remaining_probes: 1`); `HR-06-NEG-01.json`
already exercises a fenced late result (`reported.fingerprint:
"fingerprint-stale"` against `current.fingerprint: "fingerprint-current"`,
expected to be a no-op). Only "revalidation unsupported by an adapter"
remained genuinely uncovered, resolved below as part of HR-02.

### HR-01 (fresh readiness evidence -> open/admission projection)

Inputs: `peer_id`, `sealed_runtime_revision`, `readiness_evidence: {receipt_id,
runtime_revision, issued_at, observed_at, valid_until, integrity_verified}`.
Positive fixture: `runtime_revision == sealed_runtime_revision`,
`issued_at < observed_at < valid_until`, `integrity_verified == true`.
Positive output: `{readiness_state: "READY", gate_state: "OPEN", admission:
{decision: "ADMITTED", provider_effect_permitted: true}}`.
Negative fixture (`integrity_verified == false`): the independently-correct
oracle answer is `{readiness_state: "PROBE_INCONCLUSIVE", gate_state:
"CLOSED", admission: {decision: "REJECTED"}}`, grounded directly in
`RUNTIME-HEALTH-SEMANTICS-R1.md`'s rule that an unclassifiable failure is
`PROBE_INCONCLUSIVE`, never `READY`. The fault adapter incorrectly returns
`READY`/`OPEN`/`ADMITTED` instead, ignoring the integrity fact.
The exact-equality boundary (`observed_at == valid_until`) is OPEN, asserted
neither fresh nor expired by these fixtures.

### HR-02 (expired evidence -> stale, never silently healthy; folds in the uncovered revalidation-unsupported item)

Inputs: as HR-01's evidence shape, plus `observed_at` such that `issued_at <
valid_until < observed_at` (expired), plus `adapter_declares_probe_safe:
bool`. The positive fixture sets `adapter_declares_probe_safe = false` -- the
only OBS-grounded case (the real legacy capture shows outright rejection,
never an attempted revalidation).
Positive output: `{readiness_state: "READINESS_STALE", gate_state: "CLOSED",
admission: {decision: "REJECTED", reason_code: "READINESS_STALE"},
revalidation_action: "REVALIDATION_REQUIRED", zero_dispatch_calls: true}` --
directly implementing `RUNTIME-HEALTH-SEMANTICS-R1.md`'s Revalidation
protocol step 1 ("Otherwise it returns `REVALIDATION_REQUIRED` without
dispatching anything").
Fault: the adapter ignores the expiry and projects `READY`/`OPEN`/`ADMITTED`
anyway -- the literal "never silently healthy" MUST violation.
The opposite branch (`adapter_declares_probe_safe = true` -> an automatic,
no-provider-effect revalidation attempt -> `REVALIDATING`) is explicit OPEN
backlog, not built: no OBS evidence grounds it, and asserting it here would
repeat the exact unratified-policy-invention mistake rejected for HR-03's
policy-action mapping.

### HR-03 (measured failure -> correct degradation/quarantine policy; multi-scenario under one ID)

Canonical stage order (fixed lookup table, not fixture-injected):
`resolve_executable -> validate_environment -> authenticate ->
connect_network -> call_provider -> check_usage_admission`.

Each non-legacy scenario row injects only `attempted_stages`: an ordered list
of `{stage, outcome}` pairs, where a correct trace contains every stage up to
and including the first `FAILED` one and nothing after it (short-circuit).
`check_usage_admission`'s failure additionally carries a fact-injected
`usage_failure_reason` of `QUOTA_EXHAUSTED` or `RATE_LIMITED` (a genuine
external fact, not derivable from stage order alone -- same category as
SL-06's fact-injected trigger/decision, not the oracle-answer-injection
problem this replaces).

The oracle independently derives, never accepts as injected: (a)
`classification`, via a fixed stage-name -> enum lookup
(`resolve_executable -> EXECUTABLE_UNAVAILABLE`, `validate_environment ->
ENVIRONMENT_UNAVAILABLE`, `authenticate -> AUTH_UNAVAILABLE`,
`connect_network -> NETWORK_UNAVAILABLE`, `call_provider ->
PROVIDER_UNAVAILABLE`, `check_usage_admission -> usage_failure_reason`); (b)
`forbidden_downstream_stages`, as every canonical stage strictly after the
failed one.

Per-row expected output: `{classification, admission: "REJECTED",
attempted_trace matches attempted_stages exactly, zero forbidden_downstream
stages present}`. No `policy_action` field anywhere -- the failure-class to
degradation/quarantine-policy mapping is explicit OPEN backlog, since neither
`CONTRACT.md` nor the frozen prose supplies it, and fabricating one under a
`CANDIDATE` label was rejected in round 2 as an ungrounded normative
assertion.

The real legacy capture (`HR-03.json`: `failure_class:
"operational_error:timeout"`, `health: "RED"`, `gate: "closed"`, `admission:
"rejected"`) is preserved verbatim as its own compatibility row, with no
synthetic stage list and no classification-enum assignment -- it is never
relabeled as `NETWORK_UNAVAILABLE` or any other enum value, since the real
capture does not establish which stage actually timed out.

Fault (`HR-03-NEG-01`): the `EXECUTABLE_UNAVAILABLE` row's `attempted_stages`
wrongly includes a stage after the failed `resolve_executable` (e.g.
`connect_network`) -- a single isolated short-circuit violation the
oracle-derived `forbidden_downstream_stages` check catches.

## OPEN backlog (recorded, non-blocking)

1. Failure-class -> degradation/quarantine policy-action mapping (e.g.
   `QUARANTINE`/`DEGRADED`/`COOLDOWN`) is unratified; no HR-03 row asserts
   one.
2. HR-02's "adapter declares probe safe -> automatic no-effect revalidation
   attempt -> `REVALIDATING`" branch is not built; no OBS evidence grounds it.
3. Exact clock-skew and expiry-boundary-equality semantics
   (`observed_at == valid_until`) for HR-01/HR-02 are unresolved.
4. Evidence-receipt signer/issuer authority and revision-sealing mechanics
   are unresolved (same class of gap as SL's "evidence validity authority"
   backlog item).

## Documentation-hygiene notes (non-blocking, not open questions)

- `RUNTIME-HEALTH-SEMANTICS-R1.md`'s "review draft" status and "No package
  implementation is authorized" header do not block this fixture work, per
  the authorization-status resolution above -- but the document itself
  remains unratified as a governing production spec.
- `RUNTIME-HEALTH-DRIFT-2026-07-28.md` and `RUNTIME-HEALTH-DRIFT-ADDENDUM-
  2026-07-28.md` are both marked "open design issue" / "open design input";
  their content was used here only as OBS-adjacent context for the required-
  fixture-coverage question, not treated as independently ratified rules
  beyond what `RUNTIME-HEALTH-SEMANTICS-R1.md` and `CONTRACT.md` already
  state.
