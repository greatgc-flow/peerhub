# PeerHub holistic renewal — final ratification

**Status:** RATIFIED as the superseding renewal direction and final opinion for this commissioned review. Design only; this dispatch authorizes and performs no implementation.
**Date:** 2026-09-13
**Ratifier:** cx.astra, explicitly commissioned by the user
**Reviewed baseline:** main at d25e26491cc52618f4905f3f02826ca8f6469e33
**Scope:** PeerHub's repository, installed package, CLI/application boundary, and the lifecycle of its own state.

**Boldest ruling: PeerHub must stop treating possession of a peer name as governance authority. This renewal includes the complete path from dispatch identity to authorized mutation and recovered state. The prior exemption for “already converged core architecture” is withdrawn.** Better help and a credential file cannot compensate for a bypassable application boundary.

The product promise is simple: **ask peers to help in this project; PeerHub carries the context, explains effective permissions, and keeps one coherent project history.** Users should not reconstruct workspace, actor, room, lease, or migration history for every command.

## 1. Authority and evidence

This document supersedes conflicting PeerHub rulings in [the September 12 UX ratification](peerhub-ux-simplification-RATIFIED-2026-09-12.md), including its amendment. It extends the [September 9 dotdir ratification](dotdir-consolidation-RATIFIED-2026-09-09.md) where expressly stated. It changes no Engram ownership, configuration, or files.

The user commissioned this final review and local document commit, explicitly forbidding further hub.py/peerhub asks and code changes. No new peer round was dispatched. This is the commissioned final opinion, not a claim that ag, cc, and cx newly voted unanimously. Implementation remains a separate follow-up under DIR-006. The independent D-CTX security review in section 8 is an additional gate. A RATIFIED filename does not manufacture independent agreement.

Read in full before deciding:

1. [Voice A](peerhub-ux-simplification-proposal-A-2026-09-12.md).
2. [Voice B](peerhub-ux-simplification-proposal-B-2026-09-12.md).
3. The September 12 ratification, including the amendment.
4. [cc.deepthink's third review](peerhub-ux-simplification-cc-deepthink-review-2026-09-12.md).
5. [D-CTX proposal 1](peerhub-dctx-proposal-1-2026-09-13.md).
6. Engram's docs/design/engram-ux-simplification-RATIFIED-2026-09-12.md, read from the named Engram worktree.

Attribution correction: the Engram artifact's actual header names cc as its decider. The user requests cx.astra's equivalent final-review role here. I adopt the artifact's evidence-driven method without rewriting its recorded authorship or claiming memory of authorship.

Additional inspection covered the older architecture ownership rules, config hierarchy, runtime composition, application facade, native/compatibility consensus, workspace identity, backup/restore, prompt lifecycle, migrations, release workflow, packaging configuration, and tracked-file inventory.

Evidence labels:

- **[empirical_probe]** means observed during this dispatch. Source inventories and mocked-I/O probes are distinguished from end-to-end behavior.
- **[cli_live]** means an actual local CLI result, without dispatching a vendor model.
- **[declared, unverified]** means a documentary claim or source-derived behavior not exercised end to end here. A source citation makes a claim inspectable; it does not certify live behavior.
- **TEST NEEDED** names an outstanding acceptance experiment. Future contracts below are decisions, not claims of shipped implementation.

Proposal 1's previously blocked vendor environment canaries remain unverified. No passing-suite count, current quota, latest published release, or permission-confinement claim is inferred from old narrative.

## 2. Findings that change the verdict

### 2.1 Current shape, measured again

The inventory used git ls-files and Python over tracked files, rather than assuming that ignored runtime files were source.

| Item | Measured value [empirical_probe: source inventory] | Consequence |
|---|---:|---|
| Top-level commands | 30 | P3 grouped display; it did not shrink the surface. |
| Leaf command paths | 103 | Counted by intercepting parser construction and traversing actual subparsers. |
| peerhub/cli.py | 4,637 lines | Extraction is warranted; extraction alone is insufficient. |
| application/api.py | 3,136 lines | Moving everything into the existing facade would recreate the monolith. |
| application/legacy.py | 1,527 lines | Native command definitions still live under a misleading name. |
| .gitignore | 233 lines | P7 was partial, not the small project-specific file proposed by A. |
| Markdown under docs/design | 149, including 93 directly in that directory | The index's claim to categorize “all 85” is stale. |
| Tracked files under tools | 3,093 | This is substantial fixture/capture history, not only a facts helper. |
| Runtime migrations | 31 numbered SQL files | Alembic's 1–19 baseline is not the current engine. |

The measured top-level help is 48 lines and still begins with the complete 30-command argparse choice list. Grouping helped. The earlier claim that it delivered the entire discoverability improvement is false.

### 2.2 P8 introduced a release-path regression

P8 correctly moved the version to peerhub/_version.py and made package metadata dynamic. [.github/workflows/publish.yml](../../.github/workflows/publish.yml), however, still performs:

~~~python
tomllib.load(open("pyproject.toml", "rb"))["project"]["version"]
~~~

Evaluating that exact lookup against this checkout produced **KeyError: 'version'** [empirical_probe]. The workflow step runs on release events before the build. The CLI tests did not cover this release consumer.

**Ruling:** retain P8 and repair this consumer first. Obtain the expected version from the same source without importing the dependency-heavy application before build, then check the built wheel/sdist metadata and release tag agree. Do not restore a second manually maintained version or disable tag validation.

The local CLI reports 0.3.0 with its editable checkout path and git d25e264 [cli_live]. That proves the source-facing half works; it does not prove tonight's commits are published.

### 2.3 The architecture exemption protected the wrong thing

[ARCHITECTURE.md](ARCHITECTURE.md), section 2.1, already requires application.api to be the only external mutation entrance. Its header also warns that its original implementation-status description became stale.

Current _run_consensus in [cli.py](../../peerhub/cli.py) constructs ConsensusService directly and forwards parsed.proposer/parsed.actor. ApplicationAPI.submit still calls its authentication check a skeleton and compares client IDs; it does not establish that the actor belongs to the caller. The production-tree search found no Client construction [empirical_probe: source inspection; end-to-end enforcement unverified].

The one-process/two-voter quorum is supported by two earlier empirical reports: cc.deepthink's review and proposal 1. Current ConsensusService.cast_vote still checks eligibility of the supplied string and stores votes under it. This dispatch did not successfully repeat the filesystem-backed reproduction; section 11 records the limit.

**Ruling:** preserve transactional storage, reducers, leases/fences, and the outbox where they implement demonstrated invariants. Reopen their wiring, authorization, provenance, and restoration boundaries. Nine design rounds are not evidence that a later CLI respected the resulting architecture.

### 2.4 Backup membership contradicts the named-allowlist promise

[application/backup.py](../../peerhub/application/backup.py), _copy_config_files, copies every immediate entry for which is_file() is true. It does not limit membership to ask.toml, arbiter.json, and proposals.json. [config-hierarchy.md](../config-hierarchy.md) promises an explicit named allowlist.

Calling the actual helper with mocked filesystem entries copied a synthetic unrecognized.private.txt [empirical_probe: mocked I/O]. No real credential was inspected or copied. The use of is_file() also does not establish a no-follow boundary [declared, unverified for a live link].

**Ruling:** known config families define backup membership. Validate them, reject redirected members, and report unknown files as excluded. One shared family inventory drives configuration validation, initialization, backup, and restore. “Config should not contain secrets” is not an exclusion mechanism.

### 2.5 Restore is not a whole-workspace transaction

Restore uses one deterministic restore-staging directory, deletes an existing directory there, validates a staged DB, copies the DB into the live target, and only then copies config. Bundle config members are enumerated from disk rather than strictly enforced from a validated manifest.

A mocked-I/O execution of the actual restore function, failing at config copy, recorded:

~~~text
bundle database -> staged database
staged database -> live target database
config copy failed
~~~

[empirical_probe: control flow with mocked I/O]. The resulting mixed-generation risk follows directly from that ordering; real crash recovery remains TEST NEEDED. Concurrent restores share a staging name. A known config file absent from the bundle is not explicitly reconciled, allowing an older target policy to survive.

Workspace identity falls back to the directory basename. Two absent databases under different parents both resolve to “project” [empirical_probe: pure function with absent files]. Existing-database read errors can also fall back to the name. This is not a sufficient identity foundation for D-CTX or a cross-workspace restore-refusal guarantee.

**Ruling:** workspace identity and restore are inside this renewal. Restoring state must never silently resurrect a live attempt, lease, credential, or authority to execute pending external effects.

### 2.6 Ignored directories still need lifecycle ownership

The root contains .ai, .peerhub, .hypothesis, .pytest_cache, scratch, and test-p1b [empirical_probe: root listing]. Presence alone does not prove ownership of every member.

The staged-prompt janitor in [adapters/prompt_transport.py](../../peerhub/adapters/prompt_transport.py) deletes matching files based on age alone. It has no attempt/lease lookup and no entry budget. An old file is not proof that its consuming process is dead. Its assertion that slow dispatches remain protected does not cover a dispatch older than the threshold [source-derived behavior, declared/unverified; long-running-process test required].

**Ruling:** retain transient/durable separation; replace age-only lifecycle assumptions. Give generated files owners and terminal cleanup rules. Reject .ai/scratch and automatic purging of unknown work.

## 3. The product and its callers

### 3.1 One meaning, two useful surfaces

PeerHub serves a person asking peers to work and a dispatched peer submitting a bounded operation. Both need the same workspace and authorization semantics. They need different input/presentation.

| Surface | Target |
|---|---|
| Everyday human work | ask, broadcast, status, diag |
| Occasional setup/maintenance | workspace, config, adapter, backup |
| Agent/integration use | Typed commands through the same application boundary; named CLI commands remain thin translations |

Keep existing command paths through migration. Adding gov/internal/compat namespaces would rename the burden, not remove repeated context or authenticate it. Do not make ordinary voting require a hand-authored JSON envelope.

Approve a future one-shot **peerhub call METHOD --input -** transport over ApplicationAPI, with method/schema discovery from the same registry. Input contains business arguments; verified context travels separately and the transport supplies mechanical envelope metadata. This is not a new bus, daemon, policy engine, or authentication system.

Mutations requiring safe retries need stable request/idempotency identity. Generating a fresh key on each retry is not safe retry support. The transport must not be called complete while its handlers still trust actor params. It follows shared authorization and descriptor conformance.

### 3.2 Help, status, and honest terminology

Retain P3's grouping and improve it during CLI extraction:

- Bare peerhub and --help show the four daily commands, four setup groups, and the route to full help.
- --help-all exposes the full existing command set; per-command --help remains available.
- Use COMMAND in the first usage line rather than printing all 30 names.
- Replace the global “four consensus verbs overlap” aside with accurate subcommand help: proposal-add/proposal-vote are configured-electorate workflows over the same consensus service; propose/vote are explicit round primitives.
- New agent output uses one documented JSON result/error contract, with workspace selection, request identity, execution certainty, and safe provenance. Existing JSON contracts migrate deliberately with versioning.

Default status answers where this project is and whether known peers are usable. diag explains measured readiness, freshness, quota, and gating. Keep advanced health/gate/lease commands as drill-downs. Do not compress those distinct facts into an unexplained green boolean.

Classify effects explicitly: status and config paths read existing state; diagnostic collection may inspect live vendor evidence, while an explicit refresh that persists telemetry is a declared write through its application handler. Neither discovery nor a dashboard should create a workspace merely to display “uninitialized.” Preserve useful live diagnostics without calling their persistence side effects read-only.

Correct the README's “broadcast ... with unified consensus” description. The inspected FanOutCoordinator tracks delivery legs and all_completed/partial/none_completed. Delivery does not establish agreement on content or authenticate a vote. Document fan-out and result collection; reserve consensus language for a workflow that actually adjudicates a defined question.

### 3.3 Keep explicit capability escalation

Keep READ_ONLY as the default for ask and broadcast, with explicit -t/--capability-tier escalation. Do not infer elevation from prompt text or mutable workspace preferences.

When relevant, distinguish requested downstream effect tier, measured enforcement level, and separately granted governance methods/resources. dispatch/capability.py already distinguishes ADVISORY, ENFORCED, and CONFINED from READ_ONLY/WORKTREE_WRITE/GIT_MUTATE/REMOTE_MUTATE. A default tier is not fresh proof that a vendor subprocess is confined.

Keep ordinary success quiet. A denial names the denied operation and relevant bound rather than suggesting indiscriminate permission escalation.

## 4. One deliberately selected workspace

**P10 is promoted into this renewal**, paired with D-CTX's context contract. It is no longer an indefinitely separate design backlog. Implementation still follows direction-level agreement and acceptance fixtures.

### 4.1 Resolution is pure

Return root, durable identity if present, selection source, initialization status, and project boundary. Resolution never initializes, migrates, or merges stores.

If context is selected:

1. Its root is initially a candidate. Verify the credential against a trusted workspace/issuer binding and an existing valid store.
2. Explicit workspace/actor/room flags must agree with verified scope. Precedence cannot override authorization.
3. Invalid, malformed, stale, or expired selected context fails closed. Never resume ordinary discovery or downgrade automatically.

Without selected context, choose:

1. Explicit --workspace PATH.
2. PEERHUB_WORKSPACE if set, requiring an absolute path and reporting source=env.
3. Nearest .peerhub directory from cwd upward, within the nearest Git worktree boundary and including that boundary.
4. That Git worktree root if it has no workspace marker.
5. Otherwise cwd as an uninitialized candidate.

Both .git directories and .git files used by worktrees/submodules are project boundaries. Do not cross into an enclosing repository automatically. Explicit selection can intentionally share a parent workspace.

An existing .peerhub remains a boundary when it holds only config or its DB is missing/corrupt/unsupported. Report that state; do not bypass it and apply another project's policy.

Preserve explicitness in parser metadata. The current guard compares parsed.workspace with "." and cannot distinguish --workspace . from the default, despite its comment promising that distinction. Fix the representation rather than adding another string special case.

### 4.2 Initialization is an operation

ask/broadcast may automatically initialize at a discovered Git project root or an explicitly selected workspace. This keeps real-project first use simple.

In an arbitrary directory without a project boundary or initialized store, create no surprise .peerhub. Return one action: run peerhub workspace init here, or choose a project with -w PATH. No interactive approval prompt, ephemeral coordination store, or default global project history.

This deliberately **changes** the earlier “ask auto-initializes anywhere in cwd” ruling. Retain P1b's repaired notice after actual database creation and report the resolved absolute root.

Read-only commands never initialize, migrate, seed policy, or run recovery sweeps merely because --workspace was explicit. Split read composition from create_runtime's unconditional initialization. For an old schema, use a supported reader or explain the required migration; inspection cannot silently perform it.

### 4.3 Identity, nesting, and portability

New workspaces receive persisted opaque IDs, separate from directory basenames/display names. Existing invalid databases fail; they do not get a replacement identity.

Do not rewrite existing IDs on reads or bulk migrate blindly. Inventory old identities, handle collisions, and establish an activation epoch before D-CTX relies on them.

- **Relocation:** retain durable identity, explicitly update the path binding.
- **Independent clone:** mint new identity and epoch.
- **Restore:** validate the target identity and mint a new epoch; no live authority survives.

Existing parent/child stores remain separate. Nearest-marker discovery preserves both and reports the relationship; no automatic history merge or child-store deletion.

Acceptance covers explicit ".", env selection, config-only markers, corrupt stores, nested projects, .git files, same-basename siblings, case/drive aliases, relocation, and simultaneous initialization. Racing initializers converge on one persisted identity. The no-new-junction rule concerns creation; normal operation must recognize existing path aliases without creating or deleting host mappings.

## 5. Repository structure

### 5.1 Keep meaningful domains; remove false boundaries

peerhub is the package/import identity, not an _sys-style internal dumping ground. Keep its name and the current flat source layout. A src layout is a packaging technique, not a prerequisite for the measured fixes.

Retain core, application, adapters, dispatch, governance, health, routing, telemetry, state, and persistence. Do not collapse them into engine/common/utils. The state-port versus persistence-implementation split has a concrete purpose.

Target:

~~~text
peerhub/
  __init__.py, __main__.py, _version.py, client.py, runtime.py
  cli/
    __init__.py               # preserves peerhub.cli:main
    parser.py                 # root help/registration
    context.py                # CLI input -> shared context resolution
    commands/                 # daily/setup/ops/governance translators
  application/
    api.py                    # small canonical facade
    commands/
      __init__.py             # preserves current public command imports
      base.py
      dispatch.py, consensus.py, rooms.py, ...
    handlers/                 # registry-backed handlers by real domain
    ...                       # current use-case workflows
  core/, adapters/, dispatch/, governance/, health/, routing/, telemetry/
  state/                      # storage interfaces
  persistence/migrations/     # sole production SQL migration chain
  config_data/                # packaged defaults
~~~

Ellipses denote existing use cases, not permission to scaffold empty frameworks. Keep config_data: moving resource paths just to rename it defaults would add churn without a behavioral gain.

Reject the claim that native definitions “permanently” belong in application.legacy. Move them into commands by domain; leave legacy.py as a small deprecated re-export/compatibility shim for known external imports. Put actual legacy argument/slug helpers in a named compatibility module. Remove the shim only with caller evidence and a published migration contract, not a guessed one-release deadline.

Keep builtins/fake_adapter.py until read/governance composition no longer requires a fake peer and all production defaults are audited. A dependency still used by production cannot simply be moved into tests.

### 5.2 One visible migration engine

Move root alembic/ and alembic.ini under **tools/migrations/alembic/**, preserving the v19 baseline and parity tests. State explicitly that this is a historical experiment on HOLD. Update test_alembic_baseline_parity.py, script locations, and documented invocations in the same change.

The development runner must require an explicit disposable database target. It must not infer cwd/.peerhub/peerhub.sqlite3 as current alembic/env.py does. Keep Alembic/SQLAlchemy development-only; no runtime cutover is authorized.

Move scripts/migrate_engram_directives_2026_09_03.py into **tools/migrations/** and update references. Remove scripts/ when empty. This is tool relocation, not permission to execute a migration against Engram.

### 5.3 Current documentation and historical evidence

Target:

~~~text
README.md                     # first use
docs/
  README.md                   # navigation + authority rules
  STATUS.md                   # current readiness/gaps + one backlog link
  architecture.md             # current implemented boundaries + target deltas
  config-hierarchy.md         # retain this stable reference
  reference/                  # human CLI, agent protocol, lifecycle
  design/                     # active decisions and unfinished rounds
  history/
    design/YYYY-MM/           # superseded proposals, debates, status journals
    reviews/                  # completed review evidence
~~~

One current status page, one active backlog. Avoid another MASTER-FINAL-V2 index competing for authority.

Before moving documents, record original path, authority, successor, implementation status, and incoming references. Move genuinely superseded material; retain still-binding contracts in the active map. Preserve content/date, update internal links, and leave redirects for externally linked paths where necessary. Do not classify every dated design as obsolete or build long redirect chains.

This document remains at the exact commissioned path. This dispatch changes no index/history file. The follow-up must index this decision before claiming documentation renewal is finished.

The 3,093 tracked tools files include substantial fixture/capture evidence. Preserve current captures until their consumers, hashes, and retention role are mapped. New canonical fixtures belong with their harness or in tests/fixtures; session reports belong in history. Do not rename thousands of files for visual neatness or delete evidence because it does not ship in a wheel.

### 5.4 Development clutter

Keep one documented ignored scratch/ with owner-controlled explicit cleanup. Reject auto-purge hooks and .ai/scratch.

Keep .hypothesis and .pytest_cache at conventional developer locations; they are not PeerHub product state. No new cache abstraction is needed to hide them.

Classify test-p1b and other loose local probe directories before relocating/removing anything. Untracked does not mean expendable.

Finish P7 later with a small relevant ignore inventory, preserving credential/local-environment exclusions and intentional runtime exclusions. Compare status before/after and avoid broad patterns that conceal source. .gitignore is not a wheel/sdist manifest.

## 6. Durable state, temporary files, and recovery

### 6.1 Preserve .peerhub's meaning

Keep the September 9 two-tier model and durable DB path:

~~~text
<workspace>/.peerhub/
  peerhub.sqlite3
  peerhub.sqlite3-wal
  peerhub.sqlite3-shm
  config/
    ask.toml
    arbiter.json
    proposals.json

<selected global config home>/
  models.toml
  ask.toml
  arbiter.json
  proposals.json

<environment-respecting temp>/peerhub/workspaces/<workspace-key>/
  prompt-staging/
  contexts/<attempt-id>/<credential-id>.json   # D-CTX still gated
  restore-staging/<operation-id>/
~~~

No workspace models.toml: governed workspace model bindings remain the authority. Keep explicit > environment > default selection of global config root and workspace > global > packaged values for supported families. A selected malformed layer fails; absence never redirects into a different default home.

Do not introduce .peerhub/state/database.sqlite3, another global coordination DB, .peerhub/cache, or .peerhub/run. Reuse the environment-respecting temp resolver. A path digest organizes storage; it does not authenticate workspace identity.

.ai is external orchestration state. PeerHub neither creates nor relocates it and must not promise it will “naturally disappear” after hub.py retirement. Explain and leave it to its owner. The same ownership discipline applies to vendor-native dotdirs. No whole-dotdir cleanup.

### 6.2 Explicit writer ownership

| Class | Owner/destination | Lifecycle |
|---|---|---|
| Workspace/governance records | Persistence, .peerhub DB | Durable, never janitor debris |
| Project/global config | Config family, selected root | User policy preserved through upgrades |
| Staged prompt | Dispatch attempt, workspace temp | Remove on confirmed terminal lifecycle/ownership outcome |
| D-CTX bearer | Issuer, isolated attempt temp path | Revoke authority before unlink; never backup |
| Restore staging | Unique restore operation temp path | Remove only after its completion/recovery |
| Published backup | Explicit output path | Durable, sensitive, no automatic expiry |
| Test/scratch output | Creating test/reviewer | Explicit owner cleanup |

Default product writes go only to the durable workspace home, explicitly selected global config, or resolved temp namespace. User-requested output/backup is an explicit exception. Static writer inventory plus clean-fixture write manifests enforce this. Authorized peer edits to project files are separately attributed effects, not PeerHub metadata.

Deletion needs positive ownership and safe lifecycle state. A bounded janitor has entry/time limits, rejects links/reparse redirection, and never removes live/uncertain attempts based solely on age. Credential expiry invalidates authority even if unlink fails; unlink alone cannot revoke it.

### 6.3 Backup/restore must preserve a coherent generation

Keep SQLite's online backup primitive. Its database snapshot guarantee does not make separate config files transactional; see the [Python SQLite backup reference](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup).

Before claiming whole-workspace recovery:

1. Use a strict versioned manifest: known config names/hashes, schema, identity, snapshot policy, transcript inclusion. Validate the entire bundle and configs before activation. Reject traversal, absolute paths, redirected members, and unsupported schemas. Unknown backup-source config is excluded and reported.
2. Use a unique staging directory and maintenance lock honored by all PeerHub writers. Refuse activation while dispatches, credentials, or owned processes remain active/uncertain. An SQLite write lock alone does not establish application quiescence.
3. Capture DB and config coherently under maintenance coordination or verify revisions/hashes and retry. Do not claim one policy generation after a concurrent edit.
4. Restore the exact intended known config set. A known file absent from the bundle cannot leave an unrelated target version silently active. Journal removal/replacement and rollback copies; preserve unknown target content.
5. Journal staging, prior-generation backup, activation, and completion. Failure restores the old DB/config generation or leaves an explicit recovery-required state that blocks use. Two replacements are not one atomic transaction.
6. Mint a new activation epoch, invalidate credentials/active ownership, and quarantine pending external effects for reconciliation. Old pending rows cannot authorize automatic effect replay.
7. Specify same-identity restore, independent clone, and relocation separately. A matching basename proves none of them.

Keep transcripts opt-in and actual removal from the backup DB. Audit other durable prompt/result fields before describing a transcript-excluded bundle as free of sensitive content. Excluding one table is not a privacy boundary.

These corrections extend the prior dotdir ratification while preserving its names. They are prerequisites to trusting credentials across lifecycle transitions.

## 7. One mutation path and a better P4 sequence

### 7.1 Application contract

~~~text
human CLI / dispatched-peer CLI / call / embedded Client
  -> workspace and caller resolution
  -> application gateway: authenticate, authorize, validate, idempotency
  -> native handler / use-case workflow
  -> governance service and/or dispatch coordinator
  -> transactional store + existing broker/outbox as appropriate
~~~

Trusted recovery workers use narrow authenticated system ports. A system: string or caller-constructed frozen GovernanceWriteContext does not grant authority. The context is verification output, not proof by virtue of its type.

After convergence, production CLI handlers may not instantiate mutating services, access private UoW SQL, or bypass this gateway. Reads have deliberate read composition. Dispatch leases and governance targets can retain distinct transactional mechanisms; do not force every durable write into MutationRequest for diagram symmetry.

Retain ApplicationAPI as the facade, but extract domain handlers and shared enforcement from its current monolith. Do not build an unrelated permanent GovernanceWriteGateway alongside it. A transitional shared verifier is acceptable only when both old translators and ApplicationAPI delegate to the same policy and have a removal gate.

For every mutator, the production-call map names entrance, resolver, authorizer, handler, transactional owner, receipt, external effects, and an integration test reaching the real entrance. Tests on an unreachable layer do not satisfy this map.

### 7.2 Replace “P4 after D-CTX” with two bounded stages

**P4a proceeds in the implementation follow-up alongside independent D-CTX review:**

- Characterize all 103 actual command paths and their relevant output/exit contracts.
- Extract parser/help, shared context input, daily/setup translators into cli/.
- Preserve peerhub.cli:main and python -m peerhub.
- Extract native commands from legacy.py and domain handlers from api.py without changing wire semantics.
- Establish the single gateway seam and production-call map. Characterization is not a security fix.

**P4b follows the independent authority decision:**

- Route governance translators through the common verifier and exact grants.
- Expose the one-shot typed transport through the same descriptors.
- Remove direct CLI service/UoW access, duplicate identity defaults, and obsolete compatibility behavior only after parity/migration evidence.

Do not perform a giant file move immediately before replacing behavior. Equally, do not leave 4,637 lines and bypassable composition untouched while waiting for carrier canaries. P4a creates a dependency boundary every viable carrier needs.

Keep argparse. No framework migration, daemon, service locator, plugin loader, or new general command bus is needed.

### 7.3 Preserve useful proposal semantics

ProposalCoordinator uses ConsensusService and adds configured electorate selection, health filtering, reconciliation, and invariant-write-request projection. Calling this a second redundant consensus engine is inaccurate.

Keep one native state machine. The higher-level proposal workflow remains a named use case; explicit primitives and compatibility syntax are documented accurately. Both paths use the same authorization/provenance policy. Lower-level propose/vote cannot bypass restrictions by avoiding the coordinator.

Keep [HUB] in the explicitly tested compatibility renderer until parity is deliberately retired. New native UI uses PeerHub naming. Existing tests establish a compatibility contract, not a permanent prohibition on evolving it.

## 8. D-CTX: promising carrier, security implementation held

**Verdict: retain the attempt-scoped credential-file direction, reject proposal 1 as ready to implement, and require a genuinely independent second voice before shipping it.** This is an adverse self-review, not the Round 1 author approving the same design under another profile.

I share the cx lineage attributed to proposal 1. A profile/effort change is not independence. The earlier cc review independently establishes the problem; it did not review the later file design. No absent peer is counted as agreement.

### 8.1 Retained constraints

- Identity claim, credential presentation, and authorization grant are separate.
- Random credentials are attempt-scoped, revocable, issuer-hashed, and rotated on retry.
- Environment carries locators/hints, not PEERHUB_DISPATCH_TOKEN.
- Explicit --context is a useful fallback.
- Bind command/attempt, lease/fence, workspace, actor instance, separate profile, exact resource, and policy revision.
- Conflicting flags fail; invalid selected context cannot silently downgrade.
- Local OS identity supplies attribution, not peer verification or human presence.
- Governance grants are independent of filesystem/git capability tier.
- Historical records remain LEGACY/UNKNOWN, never retroactively verified.

These are ratified direction constraints. Issuance, enforcement, lifecycle, and rollout below remain independently gated.

### 8.2 Required corrections to my earlier proposal

**D1 — The file is a carrier, not authority.** Authority resides in the trusted issuer record and current lease/fence/policy. Editing actor/grants in JSON cannot change authority. A trailer calling a file “authoritative” authenticates nothing.

**D2 — Define the issuer and workspace trust anchor.** Proposal 1 allows a file to nominate a DB and asks that DB to verify its token. A fabricated DB can validate fabricated credentials unless the target operation is anchored to an independently trusted workspace/issuer. Specify who may issue credentials/grants and how protected targets reject another store. “Server-minted” is not a boundary in a library any caller can instantiate.

**D3 — Separate confusion protection from compromised-worker resistance.** Owner-only files ordinarily separate OS principals, not peers acting under the same principal. Windows access tokens represent the security identity used for access checks; they do not certify a model author. See [Microsoft's access-token model](https://learn.microsoft.com/en-us/windows/win32/secauthz/access-tokens). A worker able to read sibling credentials, edit SQLite/policy, or invoke an unrestricted local-admin route can bypass the proposed guarantee.

A first scope may be cooperative local coordination with strong error detection and auditability. Label it that way. Operations requiring resistance to a compromised worker need a trusted mutation broker outside worker write access plus measured OS enforcement, or stay held. A same-user daemon/named-pipe PID check is not sufficient proof; do not add one ceremonially.

**D4 — Grants cannot launder asserted decisions.** A round/task ID in an admitted request is not permission to act on it. Grant issuance must trace to trusted policy and approved scope/revision. Nested dispatch attenuates, never expands, parent authority. No normal ask flag can mint a governance administrator.

**D5 — Reject .peerhub/run/contexts.** Use the existing transient workspace namespace and a per-attempt exclusive unpredictable path. This corrects proposal 1's conflict with the September 9 durable/transient separation. If a sandbox cannot read that location, record the failure and review a broker-mediated transport or alternate placement; no silent durable-file fallback.

**D6 — Measure secure creation and actual reader access.** Restrictive directory/file permissions, no-follow handling, exclusive creation, atomic publication, reader checks, and Windows reparse behavior require actual platform tests. This dispatch itself encountered WinError 5 on a newly created temporary probe directory. Creating a path does not prove the intended sandboxed consumer can read it. Do not relax the peer sandbox as an unreviewed workaround.

**D7 — Fix environment composition before adding a delta.** workflows.py sends a nonempty environment_delta as PipeRunnerConfig.env; pipe.py forwards it to Popen. Python specifies a provided env mapping replaces inherited environment; see the [subprocess reference](https://docs.python.org/3/library/subprocess.html). Compose an approved parent snapshot, permitted adapter deltas, and protected authoritative context keys. Define Windows case-insensitive key handling, inherited-context scrubbing for nested dispatch, and compatible probe/dispatch environment policy.

**D8 — Verify at commit.** Attempt/lease/epoch/resource authorization must still hold when the mutation commits. Close verification-versus-revocation races using transactional checks or a revision/fence checked in the commit boundary. Expired-token idempotent replay may return an authorized existing receipt only under an explicit policy; it cannot recommit or substitute an actor.

**D9 — Separate stable operation identity from credential-instance evidence.** Hashing every credential ID/verified_at field into the semantic mutation digest can reject a legitimate retry after credential rotation. Freeze operation, actor, resource, and approved decision identity separately from each authentication-attempt record. Changed intent must fail; authorized re-authentication of the same retry must remain auditable. This requires a concrete test, not “hash all provenance.”

**D10 — Bind the decision content.** Votes must bind source/decision hash, electorate, independence policy, required assurance, and relevant revision. Define amendment invalidation/restart. Apply the same binding to Final Call ACKs, finalization, arbiter attachment, and execution of the resulting grant. A round label alone cannot authorize changed content.

**D11 — Uniqueness is not independence.** Actor instance, root peer, profile, provider/model evidence, and failure domain differ. Two credentials for one actor count once; profiles/instances with different strings are not automatically independent voters. Snapshot policy and measured evidence; unknown domains remain unknown.

**D12 — Prevent authority resurrection.** Bind an activation epoch and terminal fencing. Restoring a credential ledger cannot revive a saved bearer. A completed attempt cannot authorize its successor. Define lost-process, clock-change, retry, resume, and revocation outcomes. Revocation precedes deletion.

**D13 — No administrative escape hatch.** --human, --force, --allow-asserted-actor, a local username, or a caller-built TRUSTED_SYSTEM context does not establish Tier-0 authority. Protected operations require an independently specified trusted human/system route or remain held. Clearing an env variable cannot reopen a weaker route to the same protected resource.

**D14 — Bound transitional asserted writes.** ASSERTED mode may support legacy observation/auditing. It cannot issue credentials, lower protected assurance, rewrite the protecting policy/electorate, assign privileged roles, finalize protected rounds, or grant external effects. Protecting votes while leaving their policy freely mutable is not closure.

### 8.3 Reduce the carrier surface

Emit PEERHUB_CONTEXT_FILE as the principal locator and PEERHUB_WORKSPACE only as a useful non-secret convenience. Other identifiers belong in validated context and safe diagnostic output. Do not mirror one file into ten environment variables without a measured consumer.

The model invokes a context-aware command without reading/printing the bearer file. The CLI reads it internally. The prompt fallback carries a correctly quoted locator, not a token or shell-evaluated untrusted fragment.

The trailer is a server-authored hint before prompt processing, but remains text in a model context and can be miscopied or attacked. Issuer verification must withstand forged trailers, changed paths, and copied credentials. Redaction covers transcripts, errors, output events, receipts, process displays, config dumps, and backups.

### 8.4 Exact independent-review brief

The next voice should investigate source/counterexamples before reading these verdicts where practicable and return ACCEPT/REVISE/REJECT with evidence for each:

| Question | Required evidence |
|---|---|
| What can a worker read/write/spawn/impersonate under each profile? | Real filesystem/process canaries with version/profile evidence; no inference from --help. |
| What anchors issuer and workspace trust? | Fake-DB/context rejection and authenticated grant-issuance path. |
| Can one actor impersonate another or count twice? | Native/proposal votes, ACK, finalization, aliases, profiles, failure-domain cases. |
| Can admin/asserted/system routes bypass protection? | Complete mutator map including policy/config/role writes and direct API/service reachability. |
| Does the carrier reach actual tool subprocesses? | Independent environment-marker and explicit-locator canaries for Claude, Codex, Agy; marker absent from prompt. |
| Do scope/lifetime limits hold? | Retry/nesting, fence/revoke races, stale session, relocation, restore, clock change, crash. |
| What security promise may ship? | Explicit cooperative-local versus compromised-worker scope and eligible operation classes. |
| Can migration/recovery preserve authority? | Schema rollback, emergency human route, no revived credentials or external effects. |

Environment passthrough alone is not acceptance. An independent “AGREE” without counterexamples is not acceptance. Where DIR-006 requires other voter agreement, this independent voice is necessary but not sufficient.

## 9. Explicit disposition of tonight's work

| Item | Verdict | Required change/follow-up |
|---|---|---|
| P1: READ_ONLY default | **Keep.** Both parser defaults re-measured. | Report measured enforcement separately; no implicit escalation. |
| P1b and its repair | **Keep successful-init timing; extend policy.** | One project resolution; distinguish explicit "."; section 4 initialization contract. |
| P2: short flags | **Keep.** | Shared registration while preserving existing long forms/placement. |
| P3: tiered help | **Keep grouping, revise display/explanation.** | Concise default help, full-help path, accurate primitive/proposal distinction. |
| P4: unshipped CLI split | **Pursue now as P4a; finish after D-CTX as P4b.** | Replace the indefinite wait and giant mechanical split. |
| P5: README/STATUS | **Keep history out of first use; correct claims.** | Fan-out is not unified consensus; disclose identity/cutover limits beside the feature claim; explain update ownership. |
| P6: python -m peerhub | **Keep.** | Same main after extraction; installed-wheel test outside checkout. |
| P7: ignore trim | **Keep; finish later.** | Actual file remains 233 lines; preserve meaningful exclusions. |
| P8: source version/lazy stdout action | **Keep; urgent release-consumer fix.** | publish.yml's KeyError, artifact/tag/version check; no stale editable metadata as truth. |
| P9: correct PeerHub vote hint | **Keep.** | [HUB] stays only in compatibility rendering pending intentional retirement. |
| P10: workspace discovery | **Promote into this renewal.** | Shared pure resolver, initialization/identity contract, no automatic store merging. |

No blanket revert of 8e0dc10 through 91c866a is warranted. **“Nothing shipped needs changing” is superseded:** P8 needs a release fix; P3/P5 need further correctness/UX changes; P1b needs the workspace-policy correction. Default-tier and short-flag work remain sound.

## 10. Install/update and delivery sequence

### 10.1 One installation owner

PeerHub is a Python package. Do not copy Engram's runtime management into it. Vendor CLI installation, authentication, and updating remain outside PeerHub.

For standalone installation, document the matching interpreter's package-manager upgrade command and version check. For editable development, document source update plus editable reinstall when dependency/metadata changes require it. For an Engram-managed installation, identify the owning manager instead of silently modifying that environment from PeerHub.

The concrete standalone instructions are:

~~~text
python -m pip install peerhub
python -m peerhub --version
python -m pip install --upgrade peerhub
python -m peerhub --version
~~~

The first pair installs/verifies; the second upgrades/verifies. Use the interpreter selected for that installation. An editable developer uses git pull --ff-only in a clean checkout and python -m pip install -e . (or the existing development-extra form) when reinstalling changed metadata/dependencies. Do not prescribe git pull for a wheel installation or package-manager replacement of an editable source tree. These are future user-documentation instructions, not commands executed in this dispatch.

Diagnostics show interpreter, package location/version, and editable source where known. Do not assume every pip-created installation is independently managed. Unknown manager provenance remains unknown.

No new install.bat, update.bat, automatic pip subprocess, standalone updater, or credential-manager wrapper. The requirement is one understandable lifecycle, not duplicated lifecycle managers.

Preserve global/workspace config through upgrade. Schema changes have supported-version checks and backup/recovery policy. Package downgrade is not database rollback. Default package uninstall preserves .peerhub; no data-purge command is introduced.

### 10.2 Ordered implementation follow-up

These are work packages for the separate follow-up, not changes made by this document.

| Order | Work | Dependency | Exit evidence |
|---|---|---|---|
| R0 | Repair release-version consumer; correct current overclaims | None | Release guard works, wrong tag refused, artifact metadata agrees. |
| R1 | P4a extraction and real production-call map | Baseline characterization | Existing paths/output/exit/import contracts preserved; no new bypass. |
| R2 | Workspace resolver, read composition, identity/lifecycle fixtures | Section 4 contract and direction ratification | Correct parent/subdir selection, clean arbitrary cwd, no read initialization/migration, collision/race cases. |
| R3 | Backup/restore and temp-owner repair | R2 identity/maintenance contract | Real crash/rollback/concurrency tests; safe membership; no authority resurrection. |
| D0 | Independent D-CTX review | Runs alongside R0–R3 | Written section 8.4 dispositions, explicit scope, required DIR-006 agreement. |
| D1 | Common verifier/grants, issuer, ledger, carrier | D0 and relevant R2/R3 prerequisites | Carrier + commit-time authority; no implicit trust upgrade. |
| R4 | P4b governance convergence and typed call | D1 | Identical protection on all entrances; old bypasses removed; compatibility cannot lower assurance. |
| R5 | Root/tools/history/help/reference cleanup | Mapping can start independently; final UI follows actual interfaces | Links/imports/fixture hashes/package contents checked; status accurately names remaining gaps. |

Security dependencies must not postpone all useful work. Folder cleanup cannot declare completion while governance still trusts arbitrary actor strings.

### 10.3 Acceptance gates

1. **Reachability:** every supported boundary has a production caller and entrance-level test; no direct CLI mutating service/private-SQL bypass after R4.
2. **Actor integrity:** one process cannot satisfy protected quorum by claiming two peers; profile aliases, cleared context, direct API calls, and weaker policy-write routes do not restore the bypass.
3. **Decision integrity:** votes/ACK/finalization bind content, electorate, independence, assurance, and commit-time authority.
4. **Workspace integrity:** subdirectory operation reaches the intended store; ambiguous fresh cwd stays clean; explicit "." works; corrupt state is not treated as missing.
5. **Lifecycle integrity:** coherent restore or explicit recoverable block; old credentials/effect authority cannot revive; live prompts survive age thresholds.
6. **Artifacts:** build wheel/sdist from a clean tree and test installed artifacts outside the checkout. Verify migrations/default resources, entry points, versions/tags, and exclusion of credentials, databases, scratch, and accidental history payload. Source-distribution inclusion requires actual build inspection; see [Python packaging guidance](https://packaging.python.org/en/latest/guides/using-manifest-in/).
7. **Honest UX:** concise default/full detailed help, source-tagged readiness/provenance, clear upgrade owner, no verified-governance promise before its gate passes.
8. **Regression:** meaningful targeted checks, then the normal fast suite/type check before implementation commits. Vendor/sandbox canaries are separate named gates, not implied by fast tests.

Tests must observe effects, preserved data, real callers, and artifacts. Pinning filenames or a type name does not prove architecture or security.

## 11. Verification limits and dispatch record

This dispatch ran source inventory, parser introspection, the local version command, the exact release-version expression, and narrow mocked-I/O backup/restore/identity probes. It did not run the full suite, type checker, build packages, restore a live database, or dispatch vendor models. Those remain explicitly named gates.

A disposable filesystem probe under scratch/renewal-review-0eoec9qo failed with WinError 5 while creating its child and during Python cleanup. No consensus/backup/restore operation in that attempted fixture ran. The later cleanup command was rejected by automatic approval review with only “blocked by policy” supplied. The residual scratch directory was not claimed removed. Later backup/restore probes used mocked I/O and made no live-store writes.

This is not evidence of environment scrubbing by a vendor, proof that file credentials cannot work, or a reason to enable stronger permissions. It is evidence that the actual consumer sandbox must be tested.

The absolute P: patch path was rejected as outside the tool's project. Git and directory inspection established that P:/workspace/peerhub is the same repository at workspace/peerhub inside the session's declared writable root, with the same baseline commit and ordinary docs directories. The document is recorded through that repository-relative path; no sibling repository or external system path is included.

This document is the only intended tracked change. Existing code and runtime databases, Engram files, and external hub state are outside this dispatch's mutation scope. The local commit is neither a release nor a push.

No EXHAUSTIVE_COMPLETE is claimed for the product: HIGH findings and independent security work remain. The commissioned review is complete when its document is recorded and committed; the implementation handoff must carry these holds.

## 12. Final ruling

Keep the improvements that shortened ordinary use. Replace the premise that everything remaining is presentation-only. The actual simplification is **one project, one context-resolution rule, one authority check, one mutation path, and a lifecycle that preserves their meaning through retries and restore**.

Keep useful architecture and remove its bypasses. Keep .peerhub and make ownership precise. Preserve evidence and stop treating historical design as live operational truth. Begin bounded structural work now; do not ship my earlier D-CTX proposal without an independent reviewer confronting the trust and lifecycle gaps above.
