# Phase 1 Round 4: cx's Final Review (Vote: DO NOT RATIFY)

> **STATUS: ROUND 4 CRITIQUE, ROUND 5 PUNCH LIST PENDING**

cx's round-4 review of all 9 documents produced in Round 3 addressing the 7-item ratification gate (round-2 critique at PHASE1-CX-COUNTERCRITIQUE-ROUND2-2026-08-20.md). Two of cx's claims independently spot-checked by the terminal: **one was found FALSE** (claimed control-character corruption of `_sys/ai/protocol.json` -- terminal did a direct byte scan, 0 matches for U+0007), **one was found TRUE and serious** (fabricated "Empirically Measured" consumer-file citations in the core crosswalk that don't exist on disk -- terminal confirmed via `ls`/`find`, see `feedback_ag_fabricated_citations_recurrence_2026_08_20` in the terminal's memory). A third claim, that `init-session` is not idempotent, was also independently verified true by the terminal reading `_sys/core/hub.py` directly (generates a fresh SID and JOIN log entry on every call).

---

I'm treating this as an independent ratification audit, not a closure pass. I'll first load the repository's governing review rules, then verify all nine documents against their cited implementation surfaces and the exact seven-item gate; I'll report a binary outcome for each item and reserve ratification unless the artifacts are implementation-ready.

The supplied count is internally inconsistent: the named set is eleven files (2 crosswalks + manifest + bridge + 5 ledger batches + taxonomy + third-party decision), not nine. I'll review all eleven, because each is part of the claimed closure package. The exact gate is preserved in the round-2 critique, so there is no ambiguity about the seven tests.

The first material concern is already visible in gate item 1: file enumeration is complete, but "complete symbol/capability/consumer ledger" is not the same as one umbrella row per large module. The claimed MECE guarantee reduces the 90-action `hub.py` surface to five broad rows and marks `coverage_case_id` as TBD. I'm now testing whether the separate parity ledgers supply the missing action-level depth or merely repeat generic templates.

The audit is not converging to ratification. Three decisive measurements now rule that out: the crosswalk omits 127 of 219 public top-level `_sys/core` symbols from its own text; every one of the 90 parity rows still has `DIGEST_TBD`, `PROOF_REF_TBD`, and four unimplemented fixtures; and the manifest's ACL rule would reject all three live executable locations while the two `.cmd` receipts bind only wrappers, not the transitive executable/JS they launch.

# Round 4 verdict: DO NOT RATIFY

**Vote: DISAGREE / NOT READY FOR PHASE 1 TDD.**

I reviewed all eleven named artifacts, not nine, against the exact round-3 gate, repository HEAD `2f57906`, and live `P:\_sys\core\hub.py` SHA-256 `F748B095...A238`.

The package closes the document-count and action-name gaps, but six of seven ratification conditions remain materially incomplete.

| Gate | Result | Finding |
|---|---|---|
| 1. Complete 69-file symbol/capability/consumer ledger | **FAIL** | All 69 filenames and three namespaces are present, but symbol coverage is not complete. |
| 2. Implementable versioned manifest plus three worked adapters | **FAIL** | The schema and bounded-decoder direction exist, but the examples cannot yet construct the complete current adapter behavior. |
| 3. Executable admission, collisions, atomic registry, config SSOT | **FAIL** | Several requested mechanisms are described, but the binding is not secure or executable against the real installations. |
| 4. Correct Engram bridge contracts | **FAIL** | Dependency direction is corrected, but the proposed interfaces do not carry the data needed to perform import or directive admission. |
| 5. Evidence-grounded 90-row parity ledger | **FAIL** | All 90 names exist, but every row remains evidence-free and several behavioral claims contradict live source. |
| 6. Tier taxonomy and computable promotion ledger | **FAIL** | The five tiers are restored; the promotion ledger is not yet deterministically computable. |
| 7. Explicit third-party decision | **PASS, narrowly** | The design explicitly preserves the third-party-discovery deferral and restricts manifests to built-in decoder engines. |

## 1. Capability crosswalk -- still incomplete

The two crosswalks correctly establish 39 CLI files, 30 core files, and 129 uniquely named migration rows. Namespace separation is now satisfactory.

The claimed symbol-level MECE guarantee is not:

- A source sweep found 17 of 56 public top-level CLI symbols and 127 of 219 public top-level core symbols absent from the crosswalk text.
- `hub.py`, the largest mixed-concern file, is represented by only five broad rows; 89 of its 90 action functions are not individually assigned migration owners.
- Relevant omitted symbols include routing, arbiter, broker, health, directive, lesson, task, and action functions -- not merely incidental helpers.
- Several "empirically measured" consumer paths do not exist, including `_sys/cli/msg.py`, `test_hub_ask.py`, `test_hub_ask_contract.py`, `test_hub_broker.py`, and `test_snapshot_collector.py`. **[TERMINAL: CONFIRMED TRUE -- these files do not exist]**
- All 129 `coverage_case_id` fields remain `TBD`, leaving no traceability into the promotion matrix.
- The CLI document contains three literal U+0007 control characters corrupting `_sys\ai\protocol.json`. **[TERMINAL: CONFIRMED FALSE -- direct byte scan of protocol.json found 0 U+0007 characters]**

Gate 1 therefore remains open.

## 2-3. Manifest and admission -- examples do not survive real admission

The manifest document is a substantial improvement: it is versioned, rejects most unknown fields, selects only bounded built-in decoders, and describes atomic candidate rejection.

It still cannot produce the complete `PeerAdapter` contract:

- The schema cannot express descriptor transports, readiness probe, usage provider, `InvocationPlan.transport`, cwd, stdin, limits, artifacts, redacted display, or the exact `interpret_output` mapping.
- The taxonomy says transport is pinned per exact adapter/profile, but the manifest has no transport field.
- `supports_reasoning` and `max_inline_bytes` do not directly match current contract fields `supports_reasoning_effort` and `max_inline_utf8_bytes`; no mapping contract is supplied.
- `engine_options` accepts arbitrary unknown fields.
- `start` may be empty, and no semantic rule requires `{prompt}` or requires `{session.id}` for resume.
- The bounded component is described as a decoder engine, but several missing behaviors belong to the full adapter engine. The ownership boundary is therefore still ambiguous.

The admission examples fail more seriously:

- All three live executable directories grant `Modify` to `Authenticated Users`, contradicting the admission rule that the directory deny unprivileged writes. The examples would reject the actual Claude, Codex, and Agy installations.
- Claude and Codex receipts would hash only `claude.cmd` and `codex.cmd`. Those wrappers subsequently invoke sibling executables/JavaScript and, for Codex, may resolve `node` through `PATH`. The admitted hash therefore does not bind the transitive executable closure.
- `observed_version` is defined as the manifest version, not the observed vendor executable version.
- No stable file identity, engine hash/version, schema implementation version, admission timestamp, trust-root identity, activation authority, or governed configuration revision is recorded.
- Revalidation checks only the wrapper hash and does not revalidate the manifest, engine, transitive targets, directory ACLs, or registry revision.
- Collision extraction omits aliases, shim names, and built-in registrations requested by the gate.
- "Windows case folding," Unicode normalization, and extension normalization are named but not algorithmically defined.
- Atomic rejection is described, but registry generation/publication and reader synchronization are not specified.

Gates 2 and 3 remain open.

## 4. Engram bridge -- direction fixed, interfaces nonfunctional

The bridge document correctly retracts `HostProvisioningPort`, preserves standalone PeerHub, and points dependencies from `peerhub-engram` toward stable PeerHub contracts.

However:

- `LegacyStateSnapshot` contains only metadata and hashes. It has no frozen payload, snapshot reference, component list, or method to translate/import/compare that payload.
- `LegacyStateShadowAdapter` only captures metadata; it defines no idempotent import or shadow-comparison operation.
- `DirectiveSnapshot` contains no directive content at all -- only scope, revision, digest, precedence, and policy metadata.
- `DirectiveAdmissionPort` therefore cannot supply directives to the application boundary.
- `HostCapabilityInventory` returns one thin receipt but is not connected to manifest admission, transitive executable binding, pre-spawn revalidation, or an atomic inventory revision.
- The single-writer rule is asserted but not enforced through a cutover-mode token, expected revision, or admission guard.

Gate 4 remains open.

## 5. Parity ledger -- volume was prioritized over depth

The ledgers contain exactly 90 unique action headings, matching the frozen inventory. That is the extent of the demonstrated completeness.

Across all 90 rows: Legacy Digest 90/90 TBD, Proof Ref 90/90 TBD, Fixtures 360/360 marked NYI. The phase-0 receipt binds an older Hub digest, while the current live Hub has a different digest; no ledger row is bound to either version.

Representative source contradictions from live hub.py, **all independently spot-checked and confirmed true by the terminal for init-session**:

| Action | Ledger claim | Live behavior |
|---|---|---|
| `init-session` | Idempotent | Generates a new SID and log entry on every call. **[TERMINAL: CONFIRMED TRUE]** |
| `end-session` | Idempotent | Appends another completion record and exit log on every call. |
| `broadcast` | Idempotent | Generates a new thread ID and new messages on every call. |
| `directive-clear` | Second call fails; crash-resilient rewrite | Second call succeeds again; persistence is plain `write_text`, not atomic replacement. |
| `lessons-retire` | Idempotent when already retired | A second call exits 1 because the lesson is no longer active. |
| `task-failover` | Idempotent reassignment | Every call appends another checkpoint and handoff entry. |
| `thread-new` | Safe collision handling | Uses `exists()` followed by unlocked append; concurrent creators can both pass the check. |

Additional structural defects: the `ask` input schema is literally abbreviated with `...`; `ask`/`ask-all` mark the entire action `INTENTIONAL_DIVERGENCE` when only model-generated answer text should diverge; `SEMANTIC`/`NORMALIZED` have no per-field comparison algorithms; authorization is usually a prose label, not an exact predicate; fixture IDs specify no pre-state/request/expected-channels/post-state/recovery-injection/oracle-digest.

Gate 5 remains open.

## 6. Promotion ledger -- not computable

The five-tier taxonomy is restored successfully.

The multidimensional ledger remains descriptive rather than executable: no machine schema or actual cell inventory exists; required coverage cases and dimension combinations are not enumerated; "current REQUIRED cell" is undefined; freshness has no maximum age or stale predicate; invalidation conditions are free text; `HARNESS_FAILURE` has no unambiguous attempt outcome; `proof_kind` is part of the cell key yet the contradiction section discusses two proof kinds for "the same cell"; no deterministic reason-code-to-outcome mapping or evidence-conflict precedence exists; isolation roots, provider homes, sessions, leases, serialization policy, raw-capture protection, redacted receipts, DIR-004 source tags, and `EvidenceValue` states are absent; no worked pass/failure/stale/unavailable/contradictory examples.

Consequently, two implementations could make different promotion decisions from identical evidence. Gate 6 remains open.

## 7. Third-party deferral -- satisfied, shim lifecycle still incomplete

The explicit decision to preserve the architecture's third-party-discovery/signing deferral satisfies the exact gate item.

The claimed "complete" shim lifecycle is not complete enough for implementation. It still needs initial-path collision handling, canonical target/admission-receipt binding, atomic and locked registry mutation, quoting/injection rules, PATH mutation ownership and rollback, a guaranteed fallback precondition, and safe recovery semantics for `--force`. This should block only the shim slice, not reopen the third-party decision.

## Focused round-5 punch list

1. Generate a source-digest-bound symbol inventory and assign every relevant exported symbol -- including every `action_*` -- to a migration row, with machine-produced consumer receipts.
2. Replace the decoder-only ambiguity with an allowlisted full adapter-engine contract, or specify the complete generic assembly from manifest fields to `PeerDescriptor`, `InvocationPlan`, decoder events, and `ProtocolAssessment`.
3. Supply worked admission receipts for the three real installations. Bind direct/transitive executables, reconcile the real ACL model, record actual executable versions and file identities, and finish activation/configuration/collision semantics.
4. Add actual snapshot/directive payloads and import/compare APIs to the Engram bridge, with cutover-mode and expected-revision guards.
5. Correct all parity claims against a fixed live Hub digest. For every action, attach a source/capture receipt and specify fixture pre-state, input, expected exit/channels, post-state, and comparator rules. PeerHub proof references may remain explicitly "planned" until TDD, but legacy evidence may not remain `TBD`.
6. Publish a machine-readable promotion schema, enumerate required cells, define freshness/invalidation and classifier algorithms, add isolation/redaction/provenance requirements, and provide executable pass/fail/stale test vectors.
7. Harden the shim admission/update/removal protocol before beginning the shim implementation slice.

This is a focused closure list, not a request to reopen the overall architecture. Once these items are resolved, round 5 can be a substantially narrower verification pass.
