# Round 14 re-review: consensus-engine replacement

Author: cx.astra. Date: 2026-09-25. **Verdict: RATIFY.**

The Round 13 revision closes the specified Round 12 findings: both HIGH items and the MEDIUM item. No amendment is required. This design is ready to close the design phase and proceed to implementation.

Reviewed all 219 lines of [the Round 13 design](CONSENSUS-REPLACEMENT-R1-ag-deepthink-2026-09-25.md), SHA-256 `5faebe2f9747f897fd1ee5e6cb11971570dd2c85fa8593be2e5d3dbd109d7d00`. Source HEAD: `c0f3eefad05510204e8fd51581c23487ea1ccbdc`.

| Round 12 item | Round 14 disposition |
|---|---|
| HIGH: partial activation and crash recovery | **CLOSED.** Section 8.1, line 183, requires a single atomic transaction containing schema rename, epoch increment, and activation metadata. The names are correctly `governed_targets` and `governed_targets_v1`. Interrupted activation recovers intact V1; committed activation retains the persistent old-writer fence. |
| HIGH: contradictory effect counts | **CLOSED.** Section 8.4, lines 209-210 and 217-218, separates approval/intent creation from execution. All four requested rows now carry the correct counts. |
| MEDIUM: fixed-count validation and migration scope | **CLOSED.** Section 7.2, lines 168-172, rejects invalid types/ranges, retains the independent agreement floor, and explicitly separates native eligible-voter counting from migrated legacy required-set counting. |

**Activation evidence [empirical_probe]**

The probe created isolated disk-backed databases using the production `SqliteStateStore.initialize()` migrations and inserted a target through the production governance repository. Activation itself was modeled with `BEGIN IMMEDIATE`, the exact proposed table rename, an increment of the real `workspace_identity.activation_epoch`, and an illustrative activation metadata table, followed by one `COMMIT`. An exclusive production `WorkspaceGuard` surrounded activation. Production source and tests were not patched.

Child processes used `os._exit(73)` to bypass cleanup, explicit rollback, and connection close. Both WAL and DELETE journal modes used `synchronous=FULL`.

| Experiment | Measured result |
|---|---|
| Abrupt exit after BEGIN, after rename, after epoch update, or after metadata write, before COMMIT; both journal modes | **8/8 passed.** Reopening recovered the exact original schema and every table's data, original epoch, and absence of activation metadata. The actual V1 repository could still perform CAS. A subsequent complete activation succeeded. |
| Abrupt exit after COMMIT; both journal modes | **2/2 passed.** Renamed table, incremented epoch, and activation metadata were all present; target data remained intact. |
| Injected exception after rename, epoch update, or metadata write, followed by rollback; both journal modes | **6/6 passed.** Exact V1 recovery, working V1 CAS, and successful activation retry. |
| Deliberately incorrect separate COMMIT after rename or after epoch update; both journal modes | **4/4 negative controls detected partial activation.** These left the renamed table with absent metadata and either the old or incremented epoch. This demonstrates why the newly required single commit closes the prior counterexample. |
| Production V1 access after committed activation | **Passed.** A previously pinned store raised `WorkspaceMaintenanceError`. A fresh store's real initialization did not recreate `governed_targets`. The actual legacy repository's SELECT, UPDATE/CAS, and INSERT all raised `OperationalError: no such table: governed_targets`. Renamed data remained intact. |
| Concurrent observation while activation was paused after metadata write, before COMMIT | **Passed.** Another connection saw the complete original V1 state; a competing `BEGIN IMMEDIATE` failed with a lock error. After commit, reopening saw complete activation. |

Database integrity checks passed after recovery and complete activation. These are process-crash and transaction-boundary measurements on the real V1 schema, not power-loss or SQLite VFS fault injection, and not a completed V2 implementation test.

**Coverage matrix verification**

The columns explicitly distinguish applied effects from durable outbox intents:

| Case | Applied | Intents | Design line |
|---|---:|---:|---:|
| Rejection before approval | 0 | 0 | 209 |
| Authority change before claim, after approval/intent creation | 0 | 1 | 210 |
| Terminal approval plus arbiter attachment, pending execution | 0 | 1 | 217 |
| Terminal approval plus arbiter attachment, completed execution | 1 | 1 | 218 |

These now agree with sections 5.2, 6.2, and 8.2: refusing a claim does not erase a previously committed intent, and attaching arbiter evidence does not prove execution. Completed execution requires completion evidence.

**Fixed-count verification**

Line 168 explicitly rejects booleans, other non-integer values, fractional values, strings, zero, and negative thresholds. Line 172 rejects a threshold exceeding the applicable counting pool. The independent two-agreement floor remains mandatory.

Lines 170-171 explicitly scope the counting identities. With eligible voters `{a,b,c}`, legacy required voters `{a,b}`, threshold `2`, and agreements `{a,c}`, a new V2-native round counts **2**, while a migrated/upcast V1 round counts **1** and cannot reach quorum. Agreements `{a,b}` satisfy both. This preserves the current production evaluator's required-set counting at `peerhub/governance/consensus.py:595-605` while permitting the specified native V2 behavior.

**[empirical_probe]** A small executable interpretation of section 7.2 passed 26 invalid-type/range checks, two unattainable-pool checks, and five identity/floor checks, including the distinguishing example above, the single-voter floor, and duplicate identity counting. These checks validate the specified contract's consistency; they do not claim that the V2 adapter already exists.

**Existing checks [empirical_probe]**

`python -B -m pytest -q -p no:cacheprovider tests/unit/dispatch/test_orchestrator_consultation.py tests/unit/governance/test_consensus_policy_integration.py` returned **42 passed in 1.32s**.

Reproduction script: `../../../../_sys/data/temp/ask_ask-2890/round14_probe.py`. Machine-readable evidence: `../../../../_sys/data/temp/ask_ask-2890/round14-evidence-c7323fa7/results.json`. The script reports 23 successful case groups: 22 storage/activation groups and one fixed-count group containing 33 checks. Negative controls are counted as successful detection of intentionally broken activation.

Only this review and isolated probe artifacts were added. The design, production source, tests, live databases, configuration, and governance state were not changed. No additional peers were dispatched.

**Final vote: RATIFY. The design is ready; no further design amendment is requested.**
