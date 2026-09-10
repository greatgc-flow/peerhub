# PeerHub `.peerhub` Durable-Settings Consolidation Proposal

**Status:** SUPERSEDED by [`dotdir-consolidation-RATIFIED-2026-09-09.md`](dotdir-consolidation-RATIFIED-2026-09-09.md), which adjudicated this Round-1 proposal against an independent Engram-side proposal and is now fully implemented (13/13 items shipped as of `peerhub` v0.3.0). Kept for its citations and Round-1 reasoning; do not treat anything below as current design authority on its own.
**Investigation baseline:** `7746ee7115fd39589fb505e544909b8f8e82385c`
(`v0.2.0`, 2026-09-09)
**Scope:** PeerHub-owned user preferences, workspace preferences, durable
workspace state, and adjacent runtime artifacts. Engram and the three peer
CLIs' own profile directories are outside this proposal.

## 1. Decision

A real global tier is warranted. PeerHub is no longer “stateless by design”
in the relevant sense:

1. It actively reads a user-level `models.toml` from
   `~/.peerhub/config/` (or the directory selected by
   `PEERHUB_CONFIG_HOME`). This is not an aspirational hint: the resolver
   opens the file and returns its matching profile before consulting packaged
   defaults.
2. It actively reads a second user-level file, `ask.toml`, from the same
   location. That file changes continuity, oversized-prompt staging, and
   transcript-storage behavior on real `peerhub ask` dispatches.
3. It already owns a workspace tier: `<workspace>/.peerhub/peerhub.sqlite3`,
   plus two manually maintained workspace policy files,
   `.peerhub/arbiter.json` and `.peerhub/proposals.json`.

The right design is therefore to **standardize and finish the hierarchy that
already exists**, not to invent a hollow dot-directory. The global directory
contains only durable portable configuration. Each workspace directory
contains workspace configuration and the existing durable SQLite state.
Caches, prompt staging, statusline snapshots, and ordinary lock files do not
belong in either durable configuration tier.

## 2. Zero-base findings

### 2.1 The global model override is real

[`peerhub/application/model_config.py`](../../peerhub/application/model_config.py#L31-L45)
resolves `PEERHUB_CONFIG_HOME/models.toml` when the environment variable is
set, otherwise `~/.peerhub/config/models.toml`. An explicit environment
override replaces the default directory outright; a missing file does not
fall back to the user's home directory.

[`ModelConfigService.resolve`](../../peerhub/application/model_config.py#L137-L188)
checks, in order:

1. the exact workspace profile binding held by the workspace registry;
2. the exact profile in the global TOML file;
3. the exact profile, then wildcard, in packaged defaults;
4. a configuration error if none resolves.

The real direct-ask path constructs this service and resolves a binding before
building the adapter request
([`direct_ask.py`](../../peerhub/application/direct_ask.py#L615-L662)). The
integration tests independently exercise workspace-over-global and
global-over-packaged precedence
([`test_model_config.py`](../../tests/integration/application/test_model_config.py#L44-L90)).
The Codex adapter's `~/.peerhub/config/models.toml` hint is consequently
accurate, not dead text
([`codex_adapter.py`](../../peerhub/adapters/codex_adapter.py#L89-L95)). It
should be retained and expanded in public documentation to mention
`PEERHUB_CONFIG_HOME`.

The packaged file is distribution data, not a user customization point. Its
own header calls it release-maintained and identifies global and workspace
overrides
([`model-defaults.toml`](../../peerhub/config_data/model-defaults.toml#L1-L15)),
and `pyproject.toml` includes all `peerhub.config_data/*.toml` files as package
data ([`pyproject.toml`](../../pyproject.toml#L43-L49)).

### 2.2 A second real global preference file exists

[`peerhub/application/ask_config.py`](../../peerhub/application/ask_config.py#L31-L45)
uses the same `PEERHUB_CONFIG_HOME` rule for `ask.toml`.
[`load_ask_config`](../../peerhub/application/ask_config.py#L122-L185) loads
the packaged defaults and overlays the global file per key. The direct-ask
path calls it for every dispatch
([`direct_ask.py`](../../peerhub/application/direct_ask.py#L498-L518)), and
the integration suite proves that a global file changes real behavior
([`test_ask_config.py`](../../tests/integration/application/test_ask_config.py#L21-L64),
[`test_direct_ask.py`](../../tests/integration/application/test_direct_ask.py#L974-L1027)).

The current comment says there is no workspace layer because the values are
not “per-peer facts.” That does not establish that they are not
**per-workspace preferences**. Transcript retention/privacy, context
continuity, and staging policy can legitimately differ between workspaces.
The missing workspace overlay is a real hierarchy gap.

### 2.3 The workspace tier is real and broader than a settings file

[`PathLayout.for_workspace`](../../peerhub/core/context.py#L30-L47) defines
the workspace home as `<workspace>/.peerhub` and the database as
`peerhub.sqlite3`. The CLI's existing `--workspace` argument defaults to the
current directory
([`cli.py`](../../peerhub/cli.py#L2577-L2588)); the README also presents the
database at this exact location
([`README.md`](../../README.md#L138-L145)). Thus an accidental current
working directory is currently enough to create a new workspace database.

The database is not disposable cache. `create_runtime()` initializes it and
wires all coordination services to it
([`runtime.py`](../../peerhub/runtime.py#L116-L158)). It contains, among
other domains, peer/profile bindings, routing and health projections,
dispatch requests/attempts/leases, session bindings, governance targets and
effects, rooms, tasks, lessons, directives, capability configuration,
evidence, telemetry, continuity checkpoints, and dispatch transcripts. The
new transcript table stores the full canonical response text by attempt
([`0031_dispatch_transcripts.sql`](../../peerhub/persistence/migrations/0031_dispatch_transcripts.sql#L1-L18)),
and direct ask writes to it when enabled
([`direct_ask.py`](../../peerhub/application/direct_ask.py#L779-L792)).

Two durable workspace settings currently sit flat beside the database:

- `load_final_arbiter_policy()` reads `.peerhub/arbiter.json`; absence
  disables the feature
  ([`arbiter_review.py`](../../peerhub/application/arbiter_review.py#L81-L118)).
- `load_proposal_voters()` reads `.peerhub/proposals.json`; absence produces
  an empty electorate
  ([`proposals.py`](../../peerhub/application/proposals.py#L38-L80)).

These are genuine configuration and should live under a workspace `config/`
subdirectory. They are not transient state.

### 2.4 Other accumulated files

The source-wide filesystem-write and configuration-loader review found the
following PeerHub-owned material outside the two clean durable categories:

| Material | Current behavior | Classification and proposed disposition |
|---|---|---|
| `.peerhub/prompt-staging/*.prompt.txt` | The packaged `ask.toml` selects `.peerhub/prompt-staging` ([`ask-defaults.toml`](../../peerhub/config_data/ask-defaults.toml#L35-L49)). `stage_prompt()` creates and fsyncs a file there ([`prompt_transport.py`](../../peerhub/adapters/prompt_transport.py#L95-L149)). The direct-ask `finally` closes only the runtime; it does not remove that file ([`direct_ask.py`](../../peerhub/application/direct_ask.py#L779-L806)). | Sensitive ephemeral IPC payload, currently leaked into durable state. Move to OS temp and add lifecycle cleanup. |
| `.peerhub/statusline/ag_statusline_stdin.log` | `peerhub statusline` overwrites this file ([`cli.py`](../../peerhub/cli.py#L622-L642)). A repository-wide reference search found no PeerHub reader of this workspace path; quota readers instead consume the Engram `_sys/data/temp` path. | Ephemeral telemetry snapshot, not backup data. Move to OS temp (or eliminate it if no consumer is added). |
| `peerhub.sqlite3-wal` / `peerhub.sqlite3-shm` | The store explicitly enables WAL mode ([`sqlite.py`](../../peerhub/persistence/sqlite.py#L229-L242)). | SQLite transactional sidecars, not general cache or lock files. They must remain adjacent to the database while SQLite uses WAL. Backups must use SQLite's online-backup/checkpoint facilities rather than relocating or independently copying these files. |
| Logical file locks | `FileLockService` represents locks as governed targets submitted through the broker ([`file_locks.py`](../../peerhub/governance/file_locks.py#L34-L76), [`file_locks.py`](../../peerhub/governance/file_locks.py#L78-L129)). | Already durable rows in the workspace database; there is no ordinary PeerHub `.lock` file to consolidate. |
| `.artifacts/staging` and `*.tmp.<attempt_id>` | Artifact path resolution defaults to workspace `.artifacts/staging` ([`artifacts.py`](../../peerhub/dispatch/artifacts.py#L51-L104)); final-output materialization stages beside the destination and then atomically replaces it ([`materializer.py`](../../peerhub/dispatch/materializer.py#L228-L238), [`materializer.py`](../../peerhub/dispatch/materializer.py#L389-L404)). | Dispatch work-product boundary, not personal settings. Keep outside `.peerhub`; same-filesystem placement is required for atomic replacement. Existing cleanup/recovery rules remain a separate lifecycle concern. |
| `_sys/ai/user-directives.md` and `_sys/data/temp/*` | Direct ask reads the former as a host input ([`direct_ask.py`](../../peerhub/application/direct_ask.py#L202-L223)); telemetry resolves/reads the latter through `_sys` integration. | Engram-owned inputs, not PeerHub-owned accumulated personal data. Do not copy them into `.peerhub`. |
| Python caches, test output, and coverage scratch | Generated by Python/test tooling and ignored by repository policy. | Generic development ephemera, outside this product policy. |

No credential store, authentication token, plugin registry, user-authored skill
directory, or independent cross-workspace session database was found in the
current `peerhub/` source. Peer CLIs continue to own those categories in their
own profile roots.

## 3. Prior “stateless” verdict

The blanket prior verdict is **overturned for the current product**, with one
chronology distinction:

- `models.toml` support was introduced by verified commit `5bbcfbe` on
  2026-09-08, before the folder-structure review's final `6891454` update.
  A conclusion that PeerHub had no global personal/durable configuration was
  therefore already incomplete with respect to model preferences.
- `ask.toml`, prompt staging, and transcript storage were introduced later on
  2026-09-09 by verified commits `7dd66a6`, `332af8b`, and `22e6fd5`.
  Any earlier conclusion is additionally stale with respect to those features.

What remains true is narrower: PeerHub has no CLI-profile-like collection of
auth, skills, memories, and caches. Its global durable surface is small and
configuration-only. That is a reason to keep the layout disciplined, not a
reason to deny that the tier exists.

## 4. Proposed physical layout

```text
GLOBAL CONFIG ROOT
~/.peerhub/                              # default; durable and copyable
└── config/                              # PEERHUB_CONFIG_HOME points HERE
    ├── models.toml                      # existing, real
    ├── ask.toml                         # existing, real
    ├── arbiter.json                     # new global fallback
    └── proposals.json                   # new global fallback

WORKSPACE ROOT
<workspace>/.peerhub/                    # selected by --workspace / cwd
├── config/
│   ├── ask.toml                         # new workspace overlay
│   ├── arbiter.json                     # moved from ../arbiter.json
│   └── proposals.json                   # moved from ../proposals.json
├── peerhub.sqlite3                      # existing durable authority/state
├── peerhub.sqlite3-wal                  # SQLite-owned, present when needed
└── peerhub.sqlite3-shm                  # SQLite-owned, present when needed

OS TEMP ROOT
<temp>/peerhub/workspaces/<workspace-key>/
├── prompt-staging/                      # full prompts, aggressively cleaned
└── statusline/                          # overwrite-only telemetry snapshots
```

There is deliberately no workspace `config/models.toml`. The exact workspace
model binding is already a governed, revisioned record in
`peerhub.sqlite3`, written by `peerhub node bind-profile`, and it is already
the highest-precedence layer. Adding a second file authority for the same
fact would make drift and conflict inevitable. A future export/import command
may serialize selected durable database settings for portability, but the
export is not a second live source of truth.

The global root also deliberately has no database, cache, log, transcript, or
credential subtree. Copying `~/.peerhub/` therefore captures all global
PeerHub preferences without collecting runtime junk.

## 5. Resolution and override semantics

### 5.1 Path precedence

Global configuration home:

1. an explicit library/CLI `config_home` argument (the future CLI spelling is
   `--config-home PATH`);
2. `PEERHUB_CONFIG_HOME`;
3. `Path.home() / ".peerhub" / "config"`.

`PEERHUB_CONFIG_HOME` continues to point at the **`config/` directory
itself**, preserving today's verified contract. It does not point at the
parent `.peerhub` directory. Once an explicit argument or environment value
is selected, missing files mean “that layer has no value”; PeerHub must never
fall through to another user's home directory.

Workspace home:

1. the existing explicit `--workspace PATH`;
2. current directory when the argument is omitted;
3. `<resolved-workspace>/.peerhub` and its `config/` child.

No junction, SUBST, symlink, or directory scan participates in resolution.
The CLI should print both resolved roots through `peerhub config paths
--json`, including the source (`explicit`, `env`, or `default`), so a dispatch
never silently uses a different user's configuration.

Resolution must also be separate from initialization. If a state-creating
command resolves the implicit `.` workspace but no `.peerhub` exists there,
the recommended safe behavior is to require either an explicit `--workspace`
or an explicit `peerhub workspace init`; read-only inspection must not create
a database. This directly prevents the accidental root-level `.peerhub`
directories observed when commands were launched from the wrong current
directory, without changing behavior for an already initialized workspace.

### 5.2 Value precedence

| Configuration family | Highest to lowest |
|---|---|
| Model/profile identity | exact governed workspace DB binding > exact global `models.toml` profile > packaged exact profile > packaged wildcard > error |
| Ask/continuity/transcript policy | explicit command value, where a command exposes one > workspace `config/ask.toml` per-key overlay > global `ask.toml` per-key overlay > packaged `ask-defaults.toml` |
| Arbiter policy | explicit injected policy (tests/embedding only) > workspace `config/arbiter.json` per-key overlay > global `arbiter.json` per-key overlay > built-in safe default (`enabled=false`) |
| Proposal electorate | explicit injected tuple (tests/embedding only) > workspace `config/proposals.json` > global `proposals.json` > empty electorate (proposal creation remains fail-closed) |
| All governed runtime facts | workspace SQLite only; no global fallback |

Lists such as proposal voters and arbiter triggers replace the lower-layer
list as a whole. Objects merge only at documented keys; unknown keys and
wrong types are errors. A malformed selected higher layer is a configuration
error, never a reason to silently use lower defaults. Each file gains a
required `schema_version`, and diagnostic output reports the winning layer.

### 5.3 Relationship to `PEERHUB_SYS_DIR`

`PEERHUB_SYS_DIR` is an Engram-telemetry integration path, not a PeerHub
personal-config root, and must not be overloaded. The current resolvers are
not even identical: quota polling uses explicit argument > environment >
`cwd/_sys`
([`quota_polling.py`](../../peerhub/telemetry/quota_polling.py#L14-L30)),
while the presenter prefers an existing workspace `_sys` before the
environment variable
([`presenter.py`](../../peerhub/telemetry/presenter.py#L278-L297)). The
proposed central config resolver follows the former's explicit > env >
default shape while retaining the already-shipped `PEERHUB_CONFIG_HOME`
name and exact-replacement behavior.

## 6. Temp, cache, and SQLite rules

1. Resolve an OS temp base through Python's platform temp API. Derive a
   non-secret workspace key from the normalized workspace identity (for
   example, a full SHA-256 digest rather than the raw path) and place all
   PeerHub ephemeral IPC under `peerhub/workspaces/<key>/`.
2. Prompt files must be created with exclusive-create semantics and restrictive
   user permissions where the platform supports them. Remove them after the
   adapter has consumed them and its supervised process has reached a terminal
   state. For an execution whose process termination is genuinely uncertain,
   retain the file only until a bounded startup janitor can safely reclaim it.
3. Statusline input is an overwrite-only cache. Put it under OS temp or stop
   writing it if no PeerHub consumer is implemented.
4. Do not claim SQLite WAL/SHM files can be moved. They are part of SQLite's
   active transactional state and remain beside `peerhub.sqlite3`.
5. Do not back up a live database with an ordinary single-file copy. A future
   `peerhub backup workspace` command must use SQLite's online backup API (or
   an equivalent consistent snapshot), then copy the workspace `config/`
   files into the same backup bundle. Restore validates schema and workspace
   identity before activation.
6. Transcript rows are durable and potentially sensitive. Backup/retention
   controls must explicitly include or exclude them; they must not be mistaken
   for cache simply because they are generated automatically.

## 7. Concrete implementation map for the later TDD round

No item in this section is implemented by this proposal.

1. **Central paths:** add `peerhub/application/config_paths.py` with pure,
   injected-path-friendly resolvers for global config, workspace config, and
   workspace temp. Extend `PathLayout` in `peerhub/core/context.py` with
   `workspace_config_home`; keep `database_path` unchanged.
2. **Model config:** make `model_config.global_config_path()` delegate to the
   central resolver. Preserve the existing resolution order and public
   `PEERHUB_CONFIG_HOME` behavior.
3. **Ask config:** change `load_ask_config()` to accept the resolved workspace
   context and overlay `<workspace>/.peerhub/config/ask.toml` after the global
   file. Pass the workspace from `execute_direct_ask()`; broadcast keeps its
   current model-only configuration unless a separately scoped round adopts
   the same dispatch-shaping policy.
4. **Governance settings:** update `load_final_arbiter_policy()` and
   `load_proposal_voters()` to use central path resolution, global fallback,
   and the new workspace `config/` paths.
5. **Compatibility migration:** support the old flat workspace files only
   through an explicit compatibility reader. If both old and new files exist,
   fail with an actionable conflict rather than choosing silently. Provide
   `peerhub config migrate` to validate and atomically move each old file;
   remove compatibility only under the project's normal deprecation policy.
6. **Ephemeral paths:** update `ask-defaults.toml`, `ask_config.py`,
   `prompt_transport.py`, `direct_ask.py`, and `cli.py` so prompt/statusline
   artifacts use the OS-temp resolver and have a cleanup owner.
7. **User surfaces:** add `peerhub config paths`, `peerhub config validate`,
   and `peerhub config init --scope global|workspace`. Generated files contain
   schema versions and comments but never credentials or copied packaged
   model values presented as measured facts.
8. **Backup:** add a consistent SQLite-backed workspace backup/export path;
   document that global backup is a direct copy of `~/.peerhub/config/` (or
   the directory selected by `PEERHUB_CONFIG_HOME`).
9. **Documentation:** add the hierarchy, environment variable, precedence,
   privacy, and migration rules to README/user documentation. Update the
   Codex hint to show the resolved global path rather than only the default
   spelling.

## 8. TDD and release gates

The implementation should not release until tests prove:

- exact path precedence: explicit > environment > default, with no home
  fallback after an override is selected;
- exact value precedence for every family in the table above;
- malformed and conflicting higher layers fail closed;
- implicit current-directory resolution cannot initialize a new workspace;
- workspace model bindings remain the sole workspace model authority;
- old flat arbiter/proposal files migrate without semantic change;
- prompt and statusline files are outside both durable dot-directories;
- prompt cleanup covers success, pre-spawn failure, definite failure,
  cancellation, timeout, and uncertain-process handling;
- a live SQLite backup restores the same workspace identity, schema version,
  governed targets, session continuity, and transcript policy;
- `git status` and a direct filesystem before/after inventory show no new
  unclassified write locations;
- Windows and POSIX path tests cover spaces, non-ASCII names, and an explicit
  config home outside the user's home directory.

## 9. MECE self-check and non-goals

| Category | Covered here? | Decision |
|---|---|---|
| Global user preferences | Yes | Real today: `models.toml`, `ask.toml`; add global governance fallbacks. |
| Workspace preferences | Yes | Move flat policy files under `config/`; add workspace ask overlay; keep model binding in DB. |
| Workspace durable operational state | Yes | Keep one SQLite authority; back it up consistently. |
| Session continuity and transcripts | Yes | Existing DB data, durable and privacy-sensitive. |
| Temp/cache/locks | Yes | OS temp for ordinary ephemeral files; SQLite sidecars remain adjacent; logical locks remain DB rows. |
| Package defaults | Yes | Git/package-owned release data, never copied into live user config automatically. |
| Credentials, peer-CLI memory/rules/skills | Outside scope | Owned by Claude Code, Codex, and Antigravity profile roots. |
| Engram `_sys` inputs and telemetry | Outside ownership | Read through integration boundaries; not consolidated into PeerHub. |
| Shared/team project configuration | Deferred | This proposal is for personal/untracked settings. A future tracked team policy must be explicit and must not silently reuse the ignored `.peerhub/` tree. |
| Cross-workspace aggregation database | Rejected for now | No real consumer or state requiring one was found; adding it would duplicate workspace authority. |

## 10. Round-1 recommendation

Ratify the hierarchy and precedence above, then implement it in one TDD-led
release increment. The highest-priority correctness work is not creating the
global directory—it already exists as a real config surface—but (1) adding
the missing workspace `ask.toml` overlay, (2) consolidating workspace policy
files under `config/`, (3) moving leaked prompt/statusline ephemera to OS
temp with cleanup, and (4) providing a consistent SQLite-aware backup path.
