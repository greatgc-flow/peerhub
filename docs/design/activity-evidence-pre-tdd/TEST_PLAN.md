# Pre-TDD execution and release plan

Status: design only. 12 requirement groups and 77 acceptance cases in REQUIREMENTS.csv / TEST_CATALOG.csv, every case NOT_RUN. Existing frozen M1/M2/M3 IDs, tests, requirements and owner rows remain unchanged. New identifiers PHR/PHT are this proposal's cross-reference, not replacements for existing catalog IDs or final R-slug requirements. Register new durable R-slug rows and mapped catalog_id as required by AGENTS.md only during implementation. No production tests or real provider run performed.

## 1. Gates, evidence and stop conditions

| Gate | Required before | Evidence | State |
|---|---|---|---|
| G0 | RED | re-fetch main, open PRs/issues, inspect current schemas/contracts/Engram Rev06; approve draft interfaces, scope and stop conditions | NOT_RUN |
| G1 | P0 implementation | prove bounded read-only snapshot/export channels on disposable fixture with WAL, late settlement/outcome and restore; identify schema versions and gaps | NOT_RUN |
| G2 | P0 merge candidate | existing regression suite + targeted catalog + cross-repo synthetic interoperability; docs, command inventory, typing, no core coupling | NOT_RUN |
| G3 | P1 implementation | pin adapter versions and actual event grammar; safe observer seam, resource and privacy proof; Git before/after fixture; existing CAS/eval feasibility | NOT_RUN |
| G4 | P1 candidate | fault/negative/security/Windows tests and measured overhead; default OFF, no hidden reasoning/secret leak, source preserved | NOT_RUN |
| G5 | P2 implementation | owner confirms manual Web value, applicable provider terms and rights, exact UX and data boundary; synthetic fixtures only until approved | NOT_RUN |
| G6 | P2 candidate | local-only handoff, no browser/network side effect, explicit consent, API separation, duplicate/expiry tests; if scope rejected defer | NOT_RUN |
| G7 | release candidate | existing project gates, candidate-matched CI, owner review; optional features disabled or documented with unsupported state; release separate approval | NOT_RUN |

WP0 pre-TDD feasibility matrix: F01 schema/lineage/read UoW and cursor proof; F02 exact old/new telemetry receipt normalization and current issue triage; F03 known fake argv/primary parser/AG transport safe observer isolation; F04 native CLI capability matrix by exact installed versions and documented payload shapes; F05 bounded file delta dirty/concurrent Windows/Unix fixture; F06 artifact/eval ownership, private paths, atomicity and lifecycle; F07 offline Web packet and provider terms/account restrictions, no live account work; F08 Engram fixture mapping and consumer agreement. All pending. A missing actual runtime, provider, OS, permissions, or GPU is BLOCKED/NOT_RUN, not PASS. No requirement to complete P1/P2 feasibility to implement P0. Do not run a live authenticated command, cloud coding job, browser or model to satisfy a documentation check.

## 2. Work packages and branch boundaries

| Work package | Dependency | Candidate change |
|---|---|---|
| PH-W0 | none | read baseline/contract, refine decisions, synthetic fixture design; prove read-only coverage and no default change |
| PH-W1 | G0/G1 | source-owned activity read model, immutable record/delivery/outcome joins, channel cursors, limits, metadata-only, no mutation |
| PH-W2 | PH-W1 | expose existing normalized usage and measured deltas; targeted fixes only if independently reproduced; Engram golden fixture |
| PH-W3 | G2, G3 | opt-in CLI structured trace observer; no primary parser policy change; bounded writer, provider-specific capability coverage |
| PH-W4 | PH-W3 or independent observer seam | scoped Git/file delta, attach evidence; command and test reports; no auto-run |
| PH-W5 | G5 and useful manual workflow | packet/receipt local Web handoff or leave to Engram if duplication; no automatic browser/API substitution |
| PH-W6 | applicable earlier gates | combined offline/Windows tests, resource evidence, docs and release review |

Suggested branch names feat/activity-export, fix/usage-evidence (only if necessary), feat/optional-execution-evidence, feat/manual-web-handoff. Each PR independently reviewable; no large umbrella PR. P0 first; release P0 alone if appropriate, P1/P2 only if value demonstrated. No changes to Engram from this track; notify Engram Work with exact versioned envelope and synthetic fixture; keep Rev06 unchanged. Design baseline immutable; record amendments separately. Do not modify NOW.md or frozen specs in a subordinate lane; lead updates NOW, CHANGELOG, requirements in actual implementation PR per AGENTS. No version bump, publish, main merge or force-push without explicit owner approval.

## 3. RED order and oracles

1. PHR01, PHR05, PHR02: dependency/disabled state, read-only missing DB, current fixture and exact records, content defaults and bounds. Test no provider invocation with fake tripwire. Compare logical database, schema and offset/claim state; understand SQLite WAL sidecars without mislabeling transient read artifacts as authority writes. Assert exact reply_to and attempt linkage.
2. PHR03: fault injection at existing bridge seams, independent feedback seq, restore/clone and invalid cursor; compare complete expected event set, not just last result. Synthetic fixture with late writes and concurrent new streams. If impossible, contract partial/resnapshot, report gap. No migration merely for export.
3. PHR04: fixture-based counter/receipt oracle per provider; comparable generation and reset cases, cache components, partial coverage, no replay double count. Preserve current source truth and run existing ask metrics/telemetry regressions.
4. PHR12: publish versioned sample page to Engram consumer; assert same source event key, scope, lineage and durable checkpoint semantics, no source writes. No Hindsight dependency. P0 can stop here.
5. PHR06 and PHR09: fake adapter event observer with fault/overflow/disabled modes; prove primary parser/response/claims invariant before writing capture code. Test native event type allowlists and never copy thinking. Test secret sentinel on disk/stdout/stderr and queue/disk faults; optional storage ownership and recovery.
6. PHR07/08: isolated Git fixture, pre-dirty/cross-actor paths, actual test reports vs claims, zero tests, malformed XML, no command execution. Windows path tests in actual Windows environment when available; no shim to claim success.
7. PHR10/11: pure manual packet and receipt, offline synthetic approved exports, negative policy/automation guard. If redundant with Engram or policy uncertain, defer without blocking P0/P1.
8. Cross-verify PHR01–12 and existing CI. Freeze behavior before release; actual test run ID and commit evidence, no fabricated counts.

Use tests/communication for activity/adapter and invariants, tests/collaboration for manual evidence/eval interoperability where appropriate, tests/continuity for recovery and optional owned artifacts. Follow actual existing folder classification and test_arch_extension_graph / test_m2_m3_exit_gates. Use test fakes and synthetic SQLite DB, git repo, transcript and JUnit fixtures; never real private history, credentials or actual model API calls. No tests that rely on the user's home directory or logged-in browser. Test Windows reparse permissions and path behavior in a real runner; record missing runtime NOT_RUN. Run actual `python -m pytest -q` (or project-supported parallel variant), targeted subsets, Pyright and parser inventory check with correct dev extras and exact commands. Inspect actual latest AGENTS/CONTRIBUTING/CI before running. Release-only gates separate, no automatic publish.

## 4. Horizontal interactions and risk checks

| Interaction | Required outcome |
|---|---|
| Export × provider active × late ack | No blocked task or missed revision; page bounded |
| Export × restore × old cursor × same IDs | resnapshot; exact immutable dedup/conflict |
| Usage × replay × cumulative × reset | no fabricated per-ask counts |
| Capture × huge stdout × parser error × slow sink | primary parser independent, explicit trace gap |
| Capture × writable/read-only × ag fail-closed | no permission elevation or transport fallback change |
| File delta × dirty state × concurrent editor × path escape | actual observation, ambiguous actor, safe scope |
| Test evidence × accepted response × conflicting report | no verified success without independent proof |
| Content × scope revoke × retention × backup | no further disclosure; source preserved; downstream invalidation |
| Manual Web × expired packet × API quota | no auto provider call, no alias or fallback |
| Shared project × multi-source trace × same task | one task result, multiple evidence refs; exact identities |
| Disabled feature × existing CLI/monitor/packaging | no new default side effects or dependencies |

Risk coverage: RSK01/02 PHR02/03; RSK03/08 PHR05/06/08; RSK04 PHR02/05; RSK05/09 PHR06/09; RSK06 PHR04; RSK07 PHR07; RSK10 PHR06/11/12; RSK11/12/15 PHR10/11; RSK13 PHR12; RSK14/17 PHR01/12; RSK16 PHR09; RSK18 PHR08/12. This is targeted interaction coverage, not a claim that all Cartesian products are tested.

## 5. Handoff reporting

Each work package ledger: branch/base/head, changes, requirements and test IDs, RED/ GREEN/REFACTOR evidence with command and actual result, blockers/decisions, unchanged invariants, source docs and compatibility version, next action. Report measured usage coverage, delta correctness and optional overhead with denominators, not a made-up 100 score. User-facing Korean summary may accompany English artifacts. Stop at blocked feature or safe minimal P0 if excessive; do not add a dependency or core modification merely to reach a speculative feature count.
