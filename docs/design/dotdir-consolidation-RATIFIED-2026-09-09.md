# Dot-Directory Consolidation — Final Ratification

**Status:** RATIFIED. Supersedes both Round-1 proposals where they conflict.
**Date:** 2026-09-09
**Decider:** cc.deepthink (final call)

**Inputs ratified:**

1. `docs/design/dotdir-consolidation-engram-proposal-2026-09-09.md`
   (Engram repo, commit `2bcd33b`, author: Antigravity)
2. `docs/design/dotdir-consolidation-peerhub-proposal-2026-09-09.md`
   (PeerHub repo, commit `5386318`)

**Verification basis.** Every load-bearing citation in both proposals was
re-read against source before ruling. Where a proposal's characterization
diverges from source, this document records the divergence and rules
against the proposal. Three source trees were consulted and are
distinguished throughout, because they are *not* the same code:

| Short name | Path | HEAD | Role |
|---|---|---|---|
| **engram-repo** | `D:\Engram&Peerhub\engram-main-worktree` | `2bcd33b` (v3.2.0) | The Engram product source; where the Engram proposal is committed |
| **v2.1-env** | `D:\Engram&Peerhub\PortableDev (v2.1)` | `7815145` | The deployed portable environment this session runs in |
| **peerhub-repo** | `…\PortableDev (v2.1)\workspace\peerhub` | `5386318` (v0.2.0) | PeerHub product source |
| **tttt** | `D:\tttt` | — | Standalone Engram install; `.ais/` as sole live root; pip-installed peerhub |

---

## 0. Verification summary

### 0.1 PeerHub proposal — citations confirmed

Confirmed accurate, by direct read:

- `global_config_path()` returns `Path(PEERHUB_CONFIG_HOME) / "models.toml"`
  when set, else `Path.home()/".peerhub"/"config"/"models.toml"`, with an
  explicit no-fallback-to-`~` contract in the docstring
  ([`model_config.py:31-45`](../../peerhub/application/model_config.py#L31-L45)).
- `ModelConfigService.resolve()` checks workspace binding → global TOML →
  packaged exact → packaged `"*"` → `ModelConfigError`
  ([`model_config.py:137-188`](../../peerhub/application/model_config.py#L137-L188)).
- `global_ask_config_path()` uses the identical `PEERHUB_CONFIG_HOME` rule for
  `ask.toml` ([`ask_config.py:31-45`](../../peerhub/application/ask_config.py#L31-L45));
  `load_ask_config()` takes **no arguments** and overlays only packaged →
  global ([`ask_config.py:138-185`](../../peerhub/application/ask_config.py#L138-L185)).
  The missing workspace overlay is real.
- `PathLayout.for_workspace()` → `<workspace>/.peerhub` +
  `peerhub.sqlite3` ([`context.py:30-47`](../../peerhub/core/context.py#L30-L47)).
- `load_final_arbiter_policy()` reads flat `.peerhub/arbiter.json`, absence ⇒
  `enabled=False` ([`arbiter_review.py:81-118`](../../peerhub/application/arbiter_review.py#L81-L118));
  `load_proposal_voters()` reads flat `.peerhub/proposals.json`, absence ⇒ `()`
  ([`proposals.py:38-80`](../../peerhub/application/proposals.py#L38-L80)).
- `--workspace` defaults to `"."` ([`cli.py:2577-2588`](../../peerhub/cli.py#L2577-L2588)).
- The statusline write is genuinely orphaned. The **only writer** is
  `<workspace>/.peerhub/statusline/ag_statusline_stdin.log`
  ([`cli.py:622-642`](../../peerhub/cli.py#L622-L642)); the **only readers**
  target a different path, `<sys_dir>/data/temp/ag_statusline_stdin.log`
  ([`presenter.py:321`](../../peerhub/telemetry/presenter.py#L321),
  [`quota_polling.py:625`](../../peerhub/telemetry/quota_polling.py#L625)).
  Independently re-confirmed; the proposal's claim stands.
- Direct-ask's `finally` closes only the runtime; no staged-prompt removal
  ([`direct_ask.py:805-806`](../../peerhub/application/direct_ask.py#L805-L806)).

**Verdict: the PeerHub proposal's factual basis is sound and its
"stateless" overturn is correct.** One gap is added in §4 below.

### 0.2 Engram proposal — one citation is environment-specific, one conclusion is wrong

- The §3 anchor ("`build_env(base_dir, sys_dir)`, around line 72,
  before/during the peer iteration" and "`_sys/ai/peers.json`'s hardcoded
  `env_vars: {"CLAUDE_CONFIG_DIR": "config"}`") is **exactly accurate for
  v2.1-env**: `build_env` at `launcher.py:51`, per-peer env loop at
  `launcher.py:71-81`, and `_sys/ai/peers.json` carries
  `{"CLAUDE_CONFIG_DIR": "config"}`, `{"CODEX_HOME": "config"}`,
  `{"AGY_CONFIG_HOME": "config", "GEMINI_DIR": "config"}`.
- It is **not applicable to engram-repo**, the repository the proposal was
  committed into. There, `build_env` is at `launcher.py:101`, there is **no
  peer iteration**, and there is **no `_sys/ai/` directory and no
  `peers.json` in any code path** (`grep -rn "peers.json" --include=*.py`
  over engram-repo returns zero hits; the only matches are historical
  `_sys/data/backlog.json` entries and session notes). Per-peer env injection
  was removed by the Engram/PeerHub separation work.
- The tool-verdict table is accurate as to *content*: `_sys/git-config/.gitconfig`
  exists in v2.1-env, and `_sys/env/vscode/data/user-data/User/settings.json`
  exists alongside `globalStorage/`, `History/`, `workspaceStorage/`.
- **The proposal's own VS Code argument disproves its own `.engram/` tree.**
  See §2.3. This is the one substantive error, and it is corrected here.

---

## 1. Cross-repo coherence — RESOLVED

**Ruling.**

> **Inside an Engram environment, PeerHub's global config root is
> `<engram_root>/.engram/peerhub/config/`, selected by Engram exporting
> `PEERHUB_CONFIG_HOME` at launch. `~/.peerhub/config/` remains the correct
> and unchanged default for any PeerHub install outside Engram. PeerHub's
> workspace tier stays at `<workspace>/.peerhub/` in both cases and is never
> relocated under `.engram/`.**

**This requires zero PeerHub code changes.** `PEERHUB_CONFIG_HOME` already
exists, is already tested, and already has exactly the required semantics:
it *replaces* the config directory outright and never silently falls back to
`~` ([`model_config.py:31-45`](../../peerhub/application/model_config.py#L31-L45),
[`ask_config.py:31-45`](../../peerhub/application/ask_config.py#L31-L45)).
Engram exporting one environment variable is the entire integration. No new
resolution rule, no `.engram/`-awareness inside PeerHub, no second
precedence ladder. That is the coherence proof: the two designs meet at one
already-shipped, already-tested contract.

**Why the global tier must move inside Engram (an argument neither proposal
made).** `Path.home()` on Windows resolves to the **host** profile —
verified `C:\Users\GC` on this machine, and `~/.peerhub` does not exist
there. For an environment whose entire premise is "move the drive and
everything comes with it," leaving PeerHub's global config at `~/.peerhub/`
is a leak straight out of the portable boundary: the user's model bindings
and ask policy would silently stay on the old machine. Redirecting to
`.engram/peerhub/config/` is therefore not merely tidy, it is **required for
portability** — and it is precisely the "everything in one place" outcome
the user asked for.

**Why they do not collide.** The two dot-directories occupy different tiers
and different lifetimes:

- `.engram/` is **global**: per-installation, user-level, moves with the
  drive, one per Engram install.
- `<workspace>/.peerhub/` is **per-workspace**: it is keyed to a project
  that is cloned, moved, archived and deleted independently of the Engram
  install. Its SQLite database is the sole authority for governed workspace
  facts ([`runtime.py:116-158`](../../peerhub/runtime.py#L116-L158)).
  Moving it under `.engram/` would break that identity and is **rejected**.

**Exact spelling.** `.engram/peerhub/config/` — bare name `peerhub`, not a
nested `.peerhub`, matching `.engram/`'s other children (`claude/`,
`codex/`, `agy/`, `git/`, `gh/`). The trailing `config/` level is
**mandatory and non-negotiable**: `PEERHUB_CONFIG_HOME` points at the
directory that *contains* `models.toml`, not at its parent. Pointing it at
`.engram/peerhub/` would silently resolve every layer as absent and fall
through to packaged defaults with no error. This is the single most likely
implementation mistake in this whole plan; task 4 exists to catch it.

**Corollary — a live bug this ruling exposes.** There is an accidental,
untracked `.peerhub/` at the **portable root** of v2.1-env containing
`peerhub.sqlite3` and `statusline/`. That is exactly the
"an accidental current working directory is enough to create a new workspace
database" hazard the PeerHub proposal predicts (§2.3, §5.1), observed in the
wild. Under this ratification the portable root is **not** a workspace.
Two enforcement points, both mandatory:

- PeerHub adopts resolution ≠ initialization (task 2 below), so an implicit
  `.` can never create a database.
- Engram's PeerHub invocation path passes an explicit `--workspace` and never
  relies on the inherited cwd.

---

## 2. Engram's auto-apply mechanism — RESOLVED (approach accepted, implementation replaced)

The *approach* — plain per-process environment variables, no `subst`, no
junction — is **accepted and is already empirically proven**. tttt runs
`CLAUDE_CONFIG_DIR` / `CODEX_HOME` / `GEMINI_DIR` pointed directly at
`.ais/{claude,codex,agy}` via `ais-env.bat`, and engram-repo commit
`1351623` records it as confirmed working live. Auto-applying it at launch,
removing the manual per-shell script, is the right goal.

The *implementation as written* is replaced on five counts.

### 2.1 Re-base onto engram-repo, and delete the `peers.json` edit

The patch targets a peer-iteration block and a `_sys/ai/peers.json` that do
not exist in the repo it was committed into (§0.2). **The `peers.json`
sub-task is struck entirely** — there is nothing to bypass in engram-repo,
and v2.1-env inherits the fix when it is upgraded rather than being patched
separately.

### 2.2 Do not hardcode env vars in `launcher.py` — extend `env.json`

`launcher.py`'s module docstring states its own invariant: *"PATH and env
vars driven by env.json. No hardcoding."* The proposal's six literal
`env[...] = ...` assignments violate that invariant and re-implement the
`tool_env_vars` mechanism that already exists at `launcher.py:119-127`.

**Ratified mechanism** — declare the redirect as data:

```jsonc
// _sys/env.json
"tool_env_vars": {
  "NPM_CONFIG_PREFIX":   {"base": "env",    "sub": "nodejs/npm-global"},
  "NPM_CONFIG_CACHE":    {"base": "env",    "sub": "nodejs/npm-cache"},
  "PIP_CACHE_DIR":       {"base": "env",    "sub": "python/pip-cache"},
  "PYTHONUSERBASE":      {"base": "env",    "sub": "python/userbase"},

  "CLAUDE_CONFIG_DIR":   {"base": "engram", "sub": "claude"},
  "CODEX_HOME":          {"base": "engram", "sub": "codex"},
  "GEMINI_DIR":          {"base": "engram", "sub": "agy"},
  "AGY_CONFIG_HOME":     {"base": "engram", "sub": "agy"},
  "GH_CONFIG_DIR":       {"base": "engram", "sub": "gh"},
  "PEERHUB_CONFIG_HOME": {"base": "engram", "sub": "peerhub/config"}
}
```

Code change is then two lines: `_resolve_path_entry` gains a `base_dir`
parameter and `bases["engram"] = base_dir / ".engram"`. Benefits over the
proposed patch: it honours the file's stated invariant; it puts the new
roots under the **existing** `local.config.bat` override precedence
(`launcher.py:121-126`) for free; and it keeps the redirect reviewable as
data rather than code.

### 2.3 `.engram/` is a **live redirect root**, not a clean-config root — the proposal's own VS Code argument applies to all three AI CLIs

The proposal excluded VS Code because `--user-data-dir` forces heavy caches
to sit beside `User/settings.json`. **That property is not unique to VS Code
— the three AI CLIs have it too**, verified on disk in v2.1-env:

| Redirect target | What actually lands there besides config |
|---|---|
| `_sys/claude/config/` (live `CLAUDE_CONFIG_DIR`) | `cache/`, `paste-cache/`, `shell-snapshots/`, `file-history/`, `backups/`, `daemon/`, `telemetry/`, `sessions/`, `plugins/`, `stats-cache.json`, `status_input.log` |
| `_sys/codex/config/` (live `CODEX_HOME`) | `cache/`, `tmp/`, `thread-writer-locks/`, 38 × `sandbox.<date>.log`, `logs_2.sqlite{,-wal,-shm}`, `queue_1.sqlite{,-wal,-shm}`, `state_5.sqlite{,-wal,-shm}`, **`auth.json`** |
| `_sys/antigravity/config/` (live `GEMINI_DIR`) | `cache/`, `crashes/`, `log/`, `scratch/`, `updater/`, `presence/`, `cli.log`, `status_input.log` |

So the proposal's §2 tree — which lists only `settings.json` + `CLAUDE.md`
under `claude/` — is **not achievable as a redirect target**. It is
achievable only as a *snapshot* target, which is exactly what `.ais/` is
today (§5).

**Ruling.** `.engram/` is the **live** root; tool-owned caches, logs and
credentials will land inside it, and that is accepted. "Backup-worthy" is
expressed as an **explicit named allowlist**, never as directory membership.

Rationale for choosing live-redirect over snapshot: the snapshot alternative
keeps two copies that drift, keeps the manual restore step, and preserves
exactly the per-shell `ais-env.bat` friction the user asked to remove.
Live-redirect is already proven at tttt. The cost — caches inside
`.engram/` — is purely cosmetic once backup is allowlist-defined.

**Consequently struck from the Engram proposal:** the claim that `.engram/`
"will sit at the portable root and hold ONLY durable configuration" with
"caches and logs strictly redirected" out of it. Replaced by §4.1–§4.2 of
this document.

**VS Code's exclusion is nonetheless upheld**, on a different and better
ground: it is not one of the tools whose config the user needs to carry as
personal AI state, and `.vscode/` already covers the project tier.

### 2.4 `GIT_CONFIG_GLOBAL` keeps its existence guard and stays out of the table

`launcher.py:135-138` sets `GIT_CONFIG_GLOBAL` **only if the file exists**.
That guard is load-bearing and already answers the proposal's §6 worry that
"git might complain if `.gitconfig` is missing" — so the proposal's blanket
`mkdir -p` over all `.engram/*` is unnecessary. Keep `GIT_CONFIG_GLOBAL` as
a separate conditional block, retargeted to
`base_dir / ".engram" / "git" / ".gitconfig"`, guard intact.
`GIT_CONFIG_GLOBAL` names a **file**, not a directory, and therefore cannot
use `tool_env_vars`.

### 2.5 Directory creation is targeted, not blanket

Create, only if absent: `.engram/{claude,codex,agy,gh,peerhub/config}`.
Do **not** create `.engram/git` (see §2.4). Creation is idempotent and must
never overwrite.

---

## 3. Global/workspace precedence — RECONCILED

**The two proposals are not in conflict, but they are not "compatible" as
stated either: PeerHub specifies a full precedence ladder and Engram
specifies none.** PeerHub's rules (its §5.1 path, §5.2 value) are explicit,
tabulated and match source. The Engram proposal contains no precedence rule
at all, because its design is a pure redirect. The gap is filled here.

### 3.1 Ratified precedence, binding on both repos

> **Value precedence:** workspace > global > packaged/built-in default.
> **Path precedence:** explicit argument > environment variable > default location.
> **Absent-layer rule:** once a layer is *selected*, a missing file means
> "this layer has no value" — never a silent fall-through to a different
> default root, and never to another user's home directory.
> **Malformed-layer rule:** a malformed *selected* layer is a hard
> configuration error, never a reason to quietly use a lower layer.

This is PeerHub's rule, adopted verbatim as the ecosystem rule. It is
already the shipped behavior of `PEERHUB_CONFIG_HOME`.

### 3.2 Engram's obligation under this rule is narrow — and one Engram design item is rejected

Engram's role is limited to **choosing the global root**. Engram **must
not** add any `.engram/`-level merge, overlay or synthesis of tool settings.
Each tool already performs its own project-vs-global merge (Claude Code:
project `.claude/settings.json` — verified present at the v2.1-env root —
against `CLAUDE_CONFIG_DIR/settings.json`). A second merge layer in Engram
would create two authorities for one fact, the same failure mode PeerHub
rejects for `models.toml` (its §4).

**Therefore the Engram proposal's §4 workspace-tier `.engram/` is REJECTED**
(`workspace/MyProject/.engram/claude/projects/` etc.). Two independent
reasons, both decisive:

1. **It is unimplementable with the available mechanism.** `CLAUDE_CONFIG_DIR`,
   `CODEX_HOME` and `GEMINI_DIR` are each a *single* value. There cannot
   simultaneously be a global `.engram/claude` and a per-workspace
   `.engram/claude`.
2. **It duplicates keying the tools already do correctly.** Claude Code
   already partitions per-project state *inside* the one config dir, keyed by
   encoded project path — verified live in this very session, whose task
   output resolves under
   `…/claude/D--Engram-Peerhub-PortableDev--v2-1--workspace-peerhub/…`.
   Re-partitioning it externally would fragment it for no gain.

**Ratified structural invariant — exactly one root per tier:**

| Tier | Owner | Root |
|---|---|---|
| Global (per-installation, personal) | Engram | `<engram_root>/.engram/` |
| Workspace (per-project) | PeerHub + native tool files | `<workspace>/.peerhub/`, `.git/config`, `.vscode/settings.json`, `.claude/settings.json` |

There is no workspace-tier `.engram/`, and no global-tier `.peerhub/` inside
an Engram install other than the one `.engram/peerhub/config/` selected by
`PEERHUB_CONFIG_HOME`.

---

## 4. MECE check across both proposals — seven gaps neither addressed

### 4.1 Neither proposal defines "backup-needed" operationally

The Engram proposal assumed *backup = copy the directory*; §2.3 proves that
is false once `.engram/` is a live redirect root. The PeerHub proposal
defined backup only for its own two artifacts. There is no cross-repo
definition.

**Ruling.** Backup is defined by an **explicit named allowlist**, per repo,
never by directory membership. Engram's existing
`_sys/checks/backup_personal_data.py` is already built this way — its
`.ais/MANIFEST.txt` states that credential files are *"excluded by
construction — every item above is an explicit named allowlist entry, never
a wholesale directory copy."* That construction is ratified and retargeted
(task 11).

### 4.2 Credentials — the Engram proposal's `gh` handling is inverted

It puts `hosts.yml` inside `.engram/gh/` and then adds a warning that
*"external backup scripts must explicitly ignore it."* A
remember-to-exclude rule is the fragile form. Under §4.1's allowlist
semantics the exclusion is automatic.

**Ratified rule.** These files **live inside `.engram/`** (they must — they
are inside the tools' own config roots) and are **never** members of any
backup allowlist, and `.engram/` is gitignored: `gh/hosts.yml`,
`codex/auth.json` (verified present in v2.1-env),
`claude/.credentials.json`, and any future `auth*` / `*credential*` file.
The migration tool (task 7) must fail closed if a credential-shaped filename
would be written into a backup bundle.

### 4.3 Root-hygiene and ignore plumbing — missed by both

Engram enforces a **root allowlist**, and `.engram` is not in it. Verified in
engram-repo:

- `_sys/checks/check_root_hygiene.py:55` — the allowlist's dot-dir set is
  `{".agy", ".ai", ".claude", ".codex", ".git", ".peerhub", ".vscode", "tools", "tmp"}`;
  `check_root()` errors with `Unexpected entry at root:` for anything else.
- `_sys/checks/_common.py:41` — `VENDOR_CACHE_DIRS`, which also gates scanner
  exclusion for `check_unreferenced_functions.py` and `saturation_scan.py`,
  likewise omits `.engram`.
- `.gitignore` ignores `.peerhub/` and `.ai/` but **not** `.engram/`.

Creating `.engram/` without these three edits makes `check_root_hygiene` fail
on every run and leaves the directory showing as untracked. (`.ais` is
likewise not allowlisted, which is why it currently shows as `?? .ais/` in
v2.1-env.) These are P0 and must land **before** anything creates the
directory.

### 4.4 Prompt-staging relocation is a contract change, not a config-value change

The PeerHub proposal's implementation item 6 reads as if pointing
`relative_dir` at temp suffices. It does not: `resolve_staging_dir()`
**hard-rejects absolute paths and anything resolving outside the workspace
root** ([`prompt_transport.py:57-84`](../../peerhub/adapters/prompt_transport.py#L57-L84)),
and `ask-defaults.toml` documents the key as workspace-relative
([`ask-defaults.toml:35-49`](../../peerhub/config_data/ask-defaults.toml#L35-L49)).

**Ruling.** Add an explicit mode key; do **not** relax the validator:

```toml
[prompt_staging]
enabled = true
location = "temp"                          # "temp" | "workspace"
relative_dir = ".peerhub/prompt-staging"   # consulted only when location = "workspace"
```

`location = "workspace"` keeps the existing validator unchanged.
`location = "temp"` uses the temp resolver. An **absolute `relative_dir` is
never accepted** — permitting it would reopen precisely the traversal
surface the validator exists to close.

### 4.5 "OS temp" inside Engram is not the host temp — and that is correct

`launcher.py:59-61` sets `TEMP`/`TMP` to `<sys_dir>/data/temp` before
spawning anything. So PeerHub's "OS temp" resolves *inside the portable
boundary* when running under Engram, and to the real host temp when running
standalone. Neither proposal noticed the interaction; the outcome is the
desired one in both cases.

**Requirement:** PeerHub must resolve temp via the environment-respecting
`tempfile.gettempdir()`, never a hardcoded platform path, or this property
silently breaks.

### 4.6 Engram's own hub config (`_sys/ai/`) — present in the prototype, absent from the proposal

The existing `.ais/` prototype contains an `ai-config/` member with 22 files
(`peers.json`, `protocol.json`, `orchestration.json`, `governance_params.json`,
`backlog.json`, `user-directives.md`, …) copied from `_sys/ai/`, per
`.ais/MANIFEST.txt`. The Engram proposal's `.engram/` tree omits this
category entirely without saying so.

**Ruling.** `.engram/` gets **no `ai-config/`**, and the `.ais/ai-config/`
member is **dropped, not migrated**. Reasons: `_sys/ai/` does not exist in
engram-repo at all (separated out), so no repo-side code can redirect it;
and in v2.1-env it is product configuration living beside untracked runtime
state, not personal settings. `.ais/ai-config/` is a copy of files that
remain live in place — dropping the copy removes a second authority rather
than losing data. Whether `user-directives.md` alone is personal enough to
warrant its own backup entry is a **P2 follow-up**, explicitly out of scope
here.

### 4.7 Conflict handling when both dot-dirs exist

The Engram proposal's §5.3 says "log a warning and use `.engram`." Silent
preference is wrong when one of the two is a stale snapshot (§5). Ratified:
**both present ⇒ hard error, no silent choice** — matching PeerHub's own
rule for old-and-new config files (its §7.5).

---

## 5. Migration — RESOLVED (auto-rename REJECTED)

### 5.1 The proposed auto-rename would silently destroy live state

The Engram proposal's §5 has `launcher.py` rename `.ais` → `.engram` at
startup when `.engram` does not exist. **Rejected.** `.ais/` means two
completely different things in the two environments, and a name-only check
cannot tell them apart:

- **In v2.1-env, `.ais/` is a point-in-time *snapshot*.** Its
  `MANIFEST.txt` reads *"backup_personal_data.py `.ais/` snapshot,
  synced_at: 2026-09-09T13:43:34Z"* and every entry records a `<-` source
  under `_sys/{tool}/config`. Renaming it to `.engram/` and then pointing
  the live env vars at it **promotes a stale copy to live authority** and
  silently abandons everything written to `_sys/{tool}/config` after the
  snapshot — including Codex's WAL-backed `memories_1.sqlite` and Claude's
  9923-file `projects/` tree. Silent data loss.
- **In tttt, `.ais/` is already the live root**, driven by
  `D:\tttt\ais-env.bat` (verified: it sets `CLAUDE_CONFIG_DIR`, `CODEX_HOME`
  and `GEMINI_DIR` to `%~dp0.ais\{claude,codex,agy}`), as recorded in
  engram-repo commit `1351623`. There a rename is correct and safe.

### 5.2 Ratified migration: explicit command, `MANIFEST.txt` as the discriminator

No migration logic in `launcher.py`. Migration is a separate, reviewable,
`--dry-run`-by-default command (`_sys/checks/migrate_dotdir.py`, or a
`backup_personal_data.py --migrate-to-engram` mode):

1. **Preflight.** Refuse to run while any of `claude` / `codex` / `agy` is
   running (SQLite WAL files are live). Refuse if `.engram/` and `.ais/`
   both exist (§4.7). Print the full plan; require `--apply`.
2. **If `.ais/MANIFEST.txt` exists ⇒ `.ais/` is a snapshot.** Do **not**
   promote it. **Move** the live trees `_sys/{claude,codex,antigravity}/config`
   → `.engram/{claude,codex,agy}`, then delete `.ais/` and its manifest.
3. **If `.ais/MANIFEST.txt` is absent ⇒ `.ais/` is the live root.** Plain
   rename `.ais` → `.engram`, then delete `ais-env.bat` (it would otherwise
   re-point the CLIs at a path that no longer exists).
4. **Move, never copy.** A copy leaves two authorities and reintroduces the
   drift this whole exercise exists to remove.
5. Verify afterwards with `peerhub config paths --json` (task 4) and a real
   dispatch on each CLI.

The Engram proposal's point that a leftover `ais-env.bat` is *harmless* is
**wrong after step 3**: post-rename it points at a deleted directory. It
must be removed by the migration, not left to the user.

### 5.3 Final directory names — no ambiguity remains

**Global tier — Engram (live redirect root, gitignored):**

```text
<engram_root>/.engram/
├── claude/            <- CLAUDE_CONFIG_DIR    (settings.json, CLAUDE.md, projects/, + tool caches)
├── codex/             <- CODEX_HOME           (config.toml, CODEX.md, rules/, skills/,
│                                               memories_1.sqlite, + tool caches, auth.json)
├── agy/               <- GEMINI_DIR AND AGY_CONFIG_HOME
│                                              (settings.json, keybindings.json, AGY.md,
│                                               knowledge/, skills/, conversation_summaries.db,
│                                               + tool caches)
├── git/
│   └── .gitconfig     <- GIT_CONFIG_GLOBAL    (a FILE; set only if it exists)
├── gh/                <- GH_CONFIG_DIR        (config.yml, hosts.yml — hosts.yml never backed up)
└── peerhub/
    └── config/        <- PEERHUB_CONFIG_HOME  (models.toml, ask.toml, arbiter.json, proposals.json)
```

**Global tier — PeerHub standalone (outside Engram; unchanged, already shipped):**

```text
~/.peerhub/config/     <- default when PEERHUB_CONFIG_HOME is unset
├── models.toml
├── ask.toml
├── arbiter.json       (new global fallback)
└── proposals.json     (new global fallback)
```

**Workspace tier — PeerHub (identical inside and outside Engram):**

```text
<workspace>/.peerhub/
├── config/
│   ├── ask.toml       (new workspace overlay)
│   ├── arbiter.json   (moved from ../arbiter.json)
│   └── proposals.json (moved from ../proposals.json)
├── peerhub.sqlite3    (sole workspace authority; no config/models.toml, ever)
├── peerhub.sqlite3-wal
└── peerhub.sqlite3-shm
```

**Ephemeral — PeerHub:**

```text
<tempfile.gettempdir()>/peerhub/workspaces/<sha256(normalized workspace path)>/
├── prompt-staging/
└── statusline/        (or removed entirely — see task 12)
```

Under Engram, `tempfile.gettempdir()` resolves to `<sys_dir>/data/temp`
(§4.5), so this stays inside the portable boundary.

**Retired names — must not appear after migration:** `.ais/`,
`ais-env.bat`, `.ais/ai-config/`, `_sys/{claude,codex,antigravity}/config`
*as live roots*, `<workspace>/.peerhub/arbiter.json` and `proposals.json`
*at the flat level*, `<workspace>/.peerhub/prompt-staging/`,
`<workspace>/.peerhub/statusline/`, and the accidental root-level
`.peerhub/` in v2.1-env.

---

## 6. Execution order and priority

**PeerHub goes first.** Its work is self-contained and needs no Engram
change, whereas Engram's change *consumes* the `PEERHUB_CONFIG_HOME`
contract — freeze the interface before wiring it. Engram's migration is also
destructive (it moves live config trees with running-CLI hazards), while
PeerHub's P0 items are live correctness and privacy bugs today: a sensitive
staged prompt is written, fsynced and **never removed**, and an accidental
cwd already created a stray workspace database at the portable root.

Each task below is written to be handed straight to a TDD implementation
dispatch: test first, then implement.

### P0 — correctness, safety, and interface freeze

1. **PeerHub — central path resolver.** Add
   `peerhub/application/config_paths.py` with pure, injectable resolvers for
   global config home, workspace config home, and workspace temp. Extend
   `PathLayout` with `workspace_config_home`; leave `database_path`
   unchanged. Make `model_config.global_config_path()` and
   `ask_config.global_ask_config_path()` delegate to it with **byte-identical
   behavior**. *Tests:* explicit > env > default; a selected-but-absent layer
   never falls back to `~`; Windows and POSIX paths with spaces, non-ASCII,
   and a config home outside the user's home.
2. **PeerHub — resolution ≠ initialization.** An implicit `.` workspace must
   never create a database. A state-creating command requires an explicit
   `--workspace` or a prior `peerhub workspace init`; read-only inspection
   never creates one. Already-initialized workspaces behave exactly as today.
   *Tests:* every state-creating command invoked from a non-workspace cwd.
   *(Directly closes the stray root-level `.peerhub/`.)*
3. **PeerHub — prompt staging to temp, with a cleanup owner.** Add the
   `location = "temp" | "workspace"` key per §4.4; **do not** relax
   `resolve_staging_dir`'s validator; never accept an absolute
   `relative_dir`. Exclusive-create with restrictive permissions where
   supported. Remove the file once the adapter has consumed it and its
   supervised process reaches a terminal state; a bounded startup janitor
   reclaims genuinely-uncertain cases. *Tests:* success, pre-spawn failure,
   definite failure, cancellation, timeout, uncertain-process; and that no
   prompt bytes remain under either durable dot-directory.
4. **PeerHub — `peerhub config paths --json`.** Report every resolved root
   *and its source* (`explicit` | `env` | `default`). This is the acceptance
   instrument for Engram's wiring in task 6 and the only cheap way to catch
   the `.engram/peerhub/` vs `.engram/peerhub/config/` mistake (§1).
5. **Engram — hygiene plumbing, before anything creates the directory.** Add
   `.engram` to `VENDOR_CACHE_DIRS` (`_sys/checks/_common.py:41`) and to the
   root allowlist (`_sys/checks/check_root_hygiene.py:55`); add `.engram/` to
   `.gitignore`. *Tests:* `check_root_hygiene` passes with `.engram/`
   present; `git status` is clean with it populated.

### P1 — the consolidation itself

6. **Engram — `env.json`-driven redirect.** Add `bases["engram"]` and the
   `base_dir` parameter to `_resolve_path_entry`; declare the six variables
   in `tool_env_vars` (§2.2). Retarget `GIT_CONFIG_GLOBAL` to
   `.engram/git/.gitconfig` keeping the `.exists()` guard, as a separate
   block (§2.4). Create only `.engram/{claude,codex,agy,gh,peerhub/config}`,
   idempotently (§2.5). No `peers.json` edit (§2.1). *Tests:* each variable
   resolves to the expected absolute path under a Korean / spaced /
   parenthesized base dir; `local.config.bat` override precedence still wins;
   `GIT_CONFIG_GLOBAL` is unset when the file is absent; `PEERHUB_CONFIG_HOME`
   ends in `peerhub/config` and `peerhub config paths --json` reports
   `source: env` pointing inside `.engram/`.
7. **Engram — explicit migration command** per §5.2: `--dry-run` default,
   `MANIFEST.txt` discriminator, running-CLI preflight, both-exist hard
   error, move-not-copy, `ais-env.bat` removal, credential-shaped-filename
   guard (§4.2). **No auto-rename in `launcher.py`.** *Tests:* snapshot
   branch, live-root branch, both-exist error, running-CLI refusal, and a dry
   run that mutates nothing.
8. **PeerHub — workspace `config/` tier.** Add the workspace
   `config/ask.toml` overlay (`load_ask_config()` accepts the resolved
   workspace; `execute_direct_ask()` passes it). Move `arbiter.json` and
   `proposals.json` under `config/` with an explicit compatibility reader:
   **both old and new present ⇒ actionable conflict error, never a silent
   pick.** Ship `peerhub config migrate` to validate and atomically move each
   old file. *Tests:* precedence per §3.1; old files migrate with no semantic
   change; conflict fails closed.
9. **PeerHub — global governance fallbacks.** `arbiter.json` and
   `proposals.json` gain a global layer, with workspace > global > built-in
   safe default (`enabled=false`, empty electorate). Lists replace whole;
   objects merge at documented keys; unknown keys and wrong types are errors;
   each file carries a required `schema_version`; diagnostics report the
   winning layer.

### P2 — durability, hygiene, documentation

10. **PeerHub — `peerhub backup workspace`.** SQLite online-backup API (never
    a plain file copy of a live database), bundled with the workspace
    `config/` files. `-wal` / `-shm` are never copied independently.
    Transcript rows are durable and sensitive: the retention/inclusion
    decision must be explicit, not implicit. Restore validates schema version
    and workspace identity before activation.
11. **Engram — retarget `backup_personal_data.py`.** Sources move from
    `_sys/{tool}/config` to `.engram/{claude,codex,agy}`; drop the
    `ai-config` member (§4.6); keep the named-allowlist construction and
    assert no credential-shaped file can ever enter it (§4.2). *Tests:* a
    fixture `.engram/` containing `auth.json`, `hosts.yml` and `cache/`
    produces a bundle containing none of them.
12. **PeerHub — statusline log.** The write at `cli.py:636` has no reader
    (§0.1). Prefer **deleting the write**; only if a consumer is actually
    implemented, relocate it to the temp root. Do not keep an orphaned
    durable write.
13. **Docs, both repos.** PeerHub README / user docs get the hierarchy,
    `PEERHUB_CONFIG_HOME`, precedence, privacy and migration rules; the Codex
    adapter hint ([`codex_adapter.py:89-95`](../../peerhub/adapters/codex_adapter.py#L89-L95))
    shows the *resolved* path, not only the default spelling. Engram docs get
    `.engram/`, the retirement of `.ais/` and `ais-env.bat`, and the explicit
    statement that `.engram/` contains credentials and caches and is
    therefore gitignored and never copied wholesale. Add
    `peerhub config validate` and
    `peerhub config init --scope global|workspace`.

### Release gates (both repos, before shipping)

- Exact path precedence and exact value precedence per §3.1, for every
  configuration family.
- Malformed or conflicting higher layers fail closed.
- An implicit cwd cannot initialize a workspace.
- Workspace model bindings remain the sole workspace model authority — there
  is no `config/models.toml` at any workspace.
- Prompt and statusline artifacts exist under neither durable dot-directory.
- A live SQLite backup restores the same workspace identity, schema version,
  governed targets, session continuity and transcript policy.
- `check_root_hygiene` passes and `git status` is clean with `.engram/`
  populated; a before/after filesystem inventory shows no new unclassified
  write location.
- A real dispatch through each of `claude`, `codex`, `agy` and `peerhub ask`
  succeeds with the redirected roots, and `peerhub config paths --json`
  reports `PEERHUB_CONFIG_HOME` resolving inside `.engram/peerhub/config`.

---

## 7. Summary of rulings

| # | Question | Ruling |
|---|---|---|
| 1 | Cross-repo coherence | PeerHub's global config lives at `.engram/peerhub/config/` inside Engram, via `PEERHUB_CONFIG_HOME`; `~/.peerhub/config/` stays the standalone default; workspace `.peerhub/` never moves. **Zero PeerHub code changes.** |
| 2 | Engram auto-apply mechanism | Approach accepted (proven at tttt). Implementation replaced: `env.json`-driven, not hardcoded; `peers.json` edit struck (file absent from engram-repo); `.engram/` is a **live** root that will contain caches and credentials; `GIT_CONFIG_GLOBAL` keeps its existence guard. |
| 3 | Precedence | Not conflicting, but not jointly specified either. Ratified ecosystem-wide: **workspace > global > packaged**; path **explicit > env > default**; a selected-but-absent layer never falls through. Engram's workspace-tier `.engram/` **rejected** as unimplementable and duplicative. |
| 4 | MECE gaps | Seven found: backup definition, credential-handling inversion, root-hygiene/gitignore plumbing, prompt-staging contract change, temp-inside-Engram semantics, the `_sys/ai/` `ai-config` omission, and the both-dirs-exist conflict rule. |
| 5 | Migration | Auto-rename **rejected** — `.ais/` is a stale snapshot in v2.1-env and the live root in tttt, and a name check cannot distinguish them. Explicit `--dry-run` migration command using `MANIFEST.txt` as the discriminator. Final paths fixed in §5.3. |
| 6 | Execution order | PeerHub first (interface freeze + live bugs), Engram second. 13 tasks across P0/P1/P2, each with tests, plus release gates. |
