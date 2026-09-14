# HR-03 Policy-Action Extension R1

Status: ratified CANDIDATE-tier evidence extension. Produced by an
unlimited unanimous adversarial mutual-critique process between
ag.deepthink and cx.deepthink (2 rounds + Final Call ACK, 2026-07-31),
reconciled by cc, unanimous ACK from both peers. Extends (does not amend)
`HR-01-03-HEALTH-RECOVERY-CLASSIFICATION-SPEC-R1.md`, whose classification
logic and OPEN backlog items 2-4 are unaffected.

## Why this document exists

`CONTRACT.md`'s HR-03 one-liner requires "measured integrity/provider
failure reaches the correct degradation/quarantine policy." The original
`HR-01-03-HEALTH-RECOVERY-CLASSIFICATION-SPEC-R1.md` built the failure
*classification* but explicitly left the classification -> policy-action
mapping OPEN, having found no grounding for it in the document set that
round consulted. This left `fixture-status-v1.json`'s HR-03 entry the only
one of 54 not `SPEC_FAITHFUL` after the 2026-07-30 final cross-review
(downgraded to `PENDING_FAITHFUL_MAPPING_REVIEW`).

Re-investigating found that round's document list incomplete: two
same-dated, directly relevant frozen docs existed and were never
consulted -- `RUNTIME-HEALTH-RECOVERY-DECISIONS-2026-07-28.md` and
`RUNTIME-HEALTH-RECOVERY-ADDENDUM-R3-2026-07-28.md` -- describing an
evidence-scope taxonomy (root/profile/quota_family/environment), a
circuit state machine, and quarantine authority metadata. Critically,
`tools/phase0_fixture_runner/domain/health.py` (HR-04/05/06, already
shipped) implements part of this vocabulary in real code
(`_CIRCUIT_STATES`, `_AUTHORITY_CLASSES`, receipt fields). This is the
same class of gap as DP-06/CJ/RT-03 earlier in the session: an
already-frozen doc nobody had checked, not a genuinely unresolvable
ambiguity.

## Process summary

Round 1: ag and cx independently derived a policy-action design from the
two newly-found docs, `CONTRACT.md`, and `health.py`'s shipped vocabulary
(barred from reading `health_recovery.py`). ag proposed a near-total
classification -> scope lookup (all 7 classifications get a direct
scope). cx argued this over-claims: the frozen text states conditional
rules ("a root gate *requires* root adapter/auth/transport evidence"),
not classification-level universal rules, and proposed fact-injecting an
explicit `evidence_subject` per fixture row instead. cx also correctly
used the shipped field name `quarantine_authority_class` (ag mistakenly
used `authority_class`, the internal Python variable name) and the full
4-field receipt shape; both confirmed by cc via direct `health.py`
inspection before Round 2.

Round 2: ag fully conceded cx's core argument after being shown the
citation-accuracy corrections. cx, asked to steelman ag's position
(could HR-03's canonical stage order make root-scope a *structural*
consequence of stage position, needing no injected fact?), checked
`HR-01-03-HEALTH-RECOVERY-CLASSIFICATION-SPEC-R1.md` and found it
genuinely silent on this -- the steelman does not hold. cx also proposed
dropping `kind`/`opened_by`/`required_clearer` from the schema; cc
independently confirmed via `grep` that none of these three fields exist
anywhere in shipped `health.py`, only in the ADDENDUM-R3 doc's prose --
confirming cx's simplification was correct, not merely preferred.

Final Call: cc synthesized the converged design (below) and sent it to
both peers; both gave unconditional ACK with no further findings.

## Authority tagging

Same framework as every prior spec this session: `MUST` (directly stated
by the frozen recovery-decision docs or shipped `health.py` vocabulary),
`OBS` (directly observed in the legacy `HR-03.json` capture), `CANDIDATE`
(this fixture's specific, fact-grounded proposed scenario -- not a
ratified universal rule), `OPEN` (genuinely unresolved).

## Ratified design

### Schema addition

Each of HR-03's 8 scenario rows gains a `policy_action` field in its
output (nullable):

```
policy_action:
    null
  | {
      scope: "root" | "profile" | "quota_family" | "environment",
      subject: <non-empty string>,
      circuit_state: "CIRCUIT_OPEN",
      quarantine_authority_class: "AUTOMATIC",
      receipt: {
        incident: <non-empty string>,
        gate_generation: <nonnegative int>,
        timestamp: <nonnegative int>,
        fingerprint: <non-empty string>
      }
    }
```

`scope`/`subject`/`circuit_state`/`quarantine_authority_class` match
`health.py`'s shipped vocabulary exactly (`_CIRCUIT_STATES`,
`_AUTHORITY_CLASSES`, `quarantine_authority_class` field name).
`kind`/`opened_by`/`required_clearer` are explicitly NOT included --
verified absent from shipped `health.py`, deferred to a future
clearance-focused extension rather than invented ahead of that need.

Scope is never derived from the classification enum alone. Each
non-legacy scenario row fact-injects an `evidence_subject: {scope,
subject}` (external observed evidence, not an answer) plus a
`policy_receipt` (also fact-injected, matching the receipt-as-external-fact
pattern already used for SL-06). The oracle and subject each
independently copy `evidence_subject`/`policy_receipt` into the output
and independently decide null-vs-circuit from a separate fact
(`admission_only` for rate-limited); this is fact injection, not
answer injection, because the null/circuit branch and the eventual
non-escalation invariant are independently computed, not echoed.
Independence between oracle (`_derived_policy_action`) and subject
(`_subject_policy_action`) is structural: the oracle checks the
truthiness of `admission_only` and builds the result via a plain dict
literal; the subject checks for the presence of the `evidence_subject`
key and builds the result via a dict-merge.

### Per-row assignment (this fixture's specific proven scenarios)

| Scenario | `evidence_subject` | Extra fact | Result | Tag |
|---|---|---|---|---|
| `executable-unavailable` | `{scope: root, subject: adapter-root}` | -- | `CIRCUIT_OPEN`/root | `CANDIDATE` |
| `environment-unavailable` | `{scope: environment, subject: env-sandbox-v1}` | -- | `CIRCUIT_OPEN`/environment | `CANDIDATE` |
| `auth-unavailable` | `{scope: root, subject: auth-root}` | -- | `CIRCUIT_OPEN`/root | `CANDIDATE` |
| `network-unavailable` | `{scope: root, subject: net-transport-main}` | -- | `CIRCUIT_OPEN`/root | `CANDIDATE` |
| `provider-unavailable` | `{scope: profile, subject: ag.gptoss}` | `http_status: 500` | `CIRCUIT_OPEN`/profile | `MUST` (generic 500 opens only the failing profile circuit) |
| `quota-exhausted` | `{scope: quota_family, subject: family-gemini}` | `verified_family_evidence: true` | `CIRCUIT_OPEN`/quota_family | `MUST` (verified family evidence gates the family) |
| `rate-limited` | -- | `admission_only: true` | `policy_action: null` | `MUST` (pacing/reserve denial never opens a health circuit) |
| `legacy-operational-timeout` | -- (unchanged) | -- | no `policy_action` key | `OBS` |

The 4 `CANDIDATE`-tagged rows are concrete, fact-grounded scenarios for
*this* fixture, not ratified universal classification-level rules --
see OPEN backlog below.

`oracle_version` advances 1 -> 2 for HR-03 specifically (via a per-instance
override in `HealthRecoveryOracle.__init__`, not a shared class bump --
HR-01/HR-02 stay at version 1, unaffected).

### Verification strategy

`HR-03-NEG-01`'s existing short-circuit-stage fault is unchanged (a
distinct, still-valid concern). The non-escalation/no-hardcoding
invariant is proven by two direct unit tests in `test_health_recovery.py`
(`test_hr03_policy_action_scope_follows_evidence_subject`,
`test_hr03_policy_action_null_for_admission_only`) rather than a new
`-NEG-02` fixture -- no precedent for multiple negatives per base ID
exists in this codebase, and a targeted unit test is lighter-weight.

## OPEN backlog (recorded, non-blocking)

1. Universal classification-level scope for `EXECUTABLE_UNAVAILABLE`,
   `ENVIRONMENT_UNAVAILABLE`, `AUTH_UNAVAILABLE`, `NETWORK_UNAVAILABLE`
   when `evidence_subject` is unstated (i.e. outside this specific
   fixture's proven scenarios) remains unratified.
2. Provider failure modes other than a generic 500 (502/503/504,
   upstream vs. gateway distinctions) are unmodeled.
3. `kind`/`opened_by`/`required_clearer` clearance-authority semantics
   are deferred entirely, not attempted -- they belong to a future
   extension actually modeling circuit *clearance*, which HR-03 (an
   opening-only proof) does not need.
4. `gate_generation` increment and backoff/jitter mechanics belong to
   `health.py`'s state machine, not HR-03.
5. All items already carried in `HR-01-03-HEALTH-RECOVERY-CLASSIFICATION-SPEC-R1.md`'s
   own OPEN backlog (HR-02's automatic-revalidation branch, HR-01/02
   clock-skew semantics, HR-03's own remaining classification-scope
   items) are unaffected and remain open.
