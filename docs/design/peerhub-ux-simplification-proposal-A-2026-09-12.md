# PeerHub UX / Structure Simplification — Round-1 Proposal (Voice A)

**Status:** PROPOSAL (Round-1 independent voice A of 2; awaiting cross-compilation and ratification)
**Date:** 2026-09-12
**Author:** ag (Antigravity, Opus)
**Scope:** PeerHub user-facing surface ONLY — CLI command/flag ergonomics, install experience, config/dotdir layout, README length and clarity, folder-structure hygiene. **Explicitly out of scope:** peerhub's core service architecture (consensus machinery, capability-lease enforcement, persistence/UoW split, hub.py-replacement roadmap internals) — already converged through a documented 9-round adversarial review (`docs/design/ARCHITECTURE.md`, `docs/design/peerhub-architecture-debate.md`).

---

## 0. Facts found (all measured on disk, 2026-09-12)

### 0.1 CLI surface measured from source

| Metric | Value | Source |
|---|---|---|
| `cli.py` total lines | 4,509 | `peerhub/cli.py` measured |
| `cli.py` total bytes | 203,591 | file size on disk |
| Top-level commands | 30 | counted from `subparsers.add_parser()` calls (lines 2681–3963) |
| Actionable command paths (leaf subcommands + top-level leaves) | 103 | 5 top-level leaves + 98 subcommand endpoints |
| Total distinct flags/options across all commands | 543 | counted per-command from argparse definitions |
| Short flags defined | 0 | no `-x` aliases anywhere; only built-in `-h` |
| `room` subcommands alone | 18 | `create`, `thread-new`, `create-thread`, `append-message`, `send`, `broadcast`, `check-inbox`, `mark-read`, `promote-message`, `react`, `unreact`, `append-handoff`, `checkpoint`, `context-fill`, `update-status`, `clear`, `rebuild-session-bindings`, `status` |
| Commands with ≥6 required flags | 9 | `consensus propose` (7), `lesson propose` (7), `session heartbeat` (7), `session close` (7), `session open` (6), `duty heartbeat` (6), `duty close` (6), `task checkpoint` (6), `room send` (6) |
| CLI framework | raw `argparse` | no click/typer; single `main()` function, lines 2672–4216 |

### 0.2 README structure

| Section | Lines | Bytes (approx) | Purpose |
|---|---|---|---|
| Title + one-liner | 1–3 | 300 | What peerhub is |
| Status narrative | 5–57 | ~10,500 | Historical changelog inline (regression narratives, production-integration, LegacyTranslator retirement, config consolidation, etc.) |
| Install | 66–89 | ~900 | Three install methods |
| Try it (CLI examples) | 91–138 | ~2,500 | Showing every CLI command |
| `ask` flag reference | 140–149 | ~900 | Exit codes and flag details |
| Status output example | 151–159 | ~400 | One example |
| Run the tests | 161–173 | ~500 | Three commands |

**Key observation:** 60% of README by byte count (lines 5–65) is a **status journal** documenting historical events, bug narratives, and design decisions. This is valuable project history but actively harms discoverability of the "Try it" quick-start path.

### 0.3 Root folder contents (verified on disk)

| Entry | Tracked | Purpose | Assessment |
|---|---|---|---|
| `.ai/` | **No** (gitignored line 226) | `hub.py` runtime state (leases, mailbox, sessions, ask_history) — 7 files + 7 subdirs | **Expected:** operational data from `hub.py` dispatch sessions. Not peerhub's responsibility. |
| `.git/` | N/A | Git repository | Normal |
| `.github/` | Yes | CI workflows | Normal |
| `.gitignore` | Yes | 245 lines | Normal, but bloated (see §4) |
| `.hypothesis/` | **No** (gitignored line 53) | Hypothesis test framework database | **Expected:** standard Hypothesis ephemeral state |
| `.peerhub/` | **No** (gitignored line 2) | Workspace SQLite database (663KB) | **Expected:** peerhub's own development workspace state |
| `.pytest_cache/` | **No** (gitignored line 54) | pytest cache | **Expected:** standard pytest ephemeral state |
| `alembic/` | Yes | Alembic migrations (deferred, HOLD status) | See §4 |
| `alembic.ini` | Yes | Alembic configuration | See §4 |
| `docs/` | Yes | 88 design docs + 32 legacy-translator retirement records + adapters/compatibility/reviews | See §5 |
| `LICENSE` | Yes | MIT | Normal |
| `peerhub/` | Yes | Source package (14 subdirs, 4 top-level files) | Normal |
| `pyproject.toml` | Yes | Package config | Normal |
| `pyrightconfig.json` | Yes | Type checker config | Normal |
| `README.md` | Yes | 173 lines, 21KB | See §5 |
| `scratch/` | **No** (gitignored line 235) | 9 ad-hoc patch scripts + 5 pytest work dirs | **Expected:** gitignored scratch area for probe/dev work |
| `scripts/` | Yes | 1 file (`migrate_engram_directives_2026_09_03.py`) | See §4 |
| `tests/` | Yes | Test suite | Normal |
| `tools/` | Yes | `peerhub_facts/` fact-refresh tool | Normal |

### 0.4 Config/dotdir layout

The config/dotdir consolidation (ratified 2026-09-09, `docs/design/dotdir-consolidation-RATIFIED-2026-09-09.md`) established:

- Two-tier hierarchy: global (`~/.peerhub/config/`) and workspace (`<workspace>/.peerhub/config/`)
- Clear precedence: workspace > global > built-in
- Four config families: `models.toml`, `ask.toml`, `arbiter.json`, `proposals.json`
- `PEERHUB_CONFIG_HOME` env var for portable installs
- Dedicated CLI: `config paths|validate|init|migrate`
- Backup/restore: `backup workspace|restore`

**Assessment:** This is well-designed and recently ratified. The consolidation addressed the `.peerhub/*.json` → `.peerhub/config/*.json` migration cleanly. No loose ends found that aren't already covered.

### 0.5 Prior art: existing simplicity-related reviews

| Document | Date | Key finding relevant to this review |
|---|---|---|
| `docs/design/INTERFACE-MECE-AESTHETIC-AUDIT-2026-08-24.md` | 2026-08-24 | Focused on P:\\'s non-peerhub entry points (`claude_entry.py`, `codex_entry.py`, `agy_entry.py`), not peerhub's own CLI surface. Found DRY violations in those files. Did not audit peerhub's CLI command/flag ergonomics. |
| `docs/reviews/p-drive-folder-structure-mece-review-2026-09-09.md` | 2026-09-09 | Focused on P:\\ workspace folder organization, not peerhub repo structure. |
| Project memory (referenced in task): "core `ask` sound but buried in enterprise-CQRS ceremony; proposals/consensus redundant" | 2026-09-07 | This finding is referenced in the project's own memory system. The characterization captures a real observation about the CLI surface. See §1 for whether this proposal should extend, supersede, or leave it. |

**Relationship to the 2026-09-07 finding:** This proposal **extends** that finding with concrete evidence and actionable remedies. The 2026-09-07 memory note observed the symptom ("`ask` sound but buried in enterprise-CQRS ceremony") but didn't prescribe specific changes. Tonight's review provides the measurements and the specific simplification plan.

---

## 1. CLI command/flag surface

### 1.1 Finding: The CLI has grown past what a human or AI can hold in their head

**Evidence:** 30 top-level commands with 103 actionable paths and 543 flags, all implemented in a single 4,509-line `cli.py` file using raw argparse. For comparison:

- `git` has ~22 porcelain commands (the "everyday" surface) and ~140+ total, but these are organized across separate executables and have decades of ecosystem documentation.
- `docker` has ~13 management commands + ~15 top-level commands, with clear user-vs-admin separation.
- PeerHub has 30 top-level nouns, no organization into user-facing vs. admin-facing tiers, no short flags, and a single monolithic file implementing every command.

**The 2–3 most-used commands are clear from the README's own ordering and from what peerhub actually needs to do:**

| Tier | Commands | Used by |
|---|---|---|
| **Primary** (daily use) | `ask`, `status`, `diag`, `broadcast` | Every user — human or AI — actually dispatching prompts |
| **Setup** (once per workspace) | `workspace init`, `config init/validate/paths`, `adapter discover` | First-time setup, troubleshooting |
| **Admin/ops** (rarely, by operators) | `health`, `peer`, `lease`, `gate`, `broker`, `node`, `routing`, `leadership` | System administration |
| **Governance** (hub.py parity) | `consensus`, `task`, `lesson`, `directive`, `room`, `duty`, `session`, `alert`, `error`, `feedback`, `lock`, `artifact`, `role` | Automated governance machinery — **these are primarily API-contract surfaces called programmatically by `hub.py` or its replacement, not human-typed commands** |

**The long tail is genuinely problematic.** The Governance tier (13 command groups, ~65 subcommands, ~350 flags) is hub.py-replacement infrastructure designed for programmatic invocation, not human typing. Having it flat alongside `ask` and `status` in `--help` output means a new user sees 30 top-level commands when they need 4. This is the concrete manifestation of the 2026-09-07 "buried in enterprise-CQRS ceremony" observation.

### 1.2 Finding: `--capability-tier` on `ask` is required and verbose

**Evidence (`peerhub/cli.py` line 2898):**
```python
ask_parser.add_argument(
    "--capability-tier",
    required=True,
    choices=tuple(tier.name for tier in CapabilityTier),
    help="Required downstream capability tier",
)
```

Every `ask` invocation requires `--capability-tier READ_ONLY|WORKTREE_WRITE|GIT_MUTATE|REMOTE_MUTATE`. This is the single most friction-adding flag on the most-used command.

**Current:** `peerhub ask ag "hello" --capability-tier READ_ONLY` (56 characters)
**With a default:** `peerhub ask ag "hello"` (22 characters, 60% shorter)

The project's stated preference is "flexible, general, very simple." A required `--capability-tier` on every single `ask` invocation is not simple — it's a safety mechanism that could be a sensible default (`READ_ONLY`) with explicit escalation.

### 1.3 Finding: Session/duty commands require absurdly long invocations

**Evidence (README line 128):**
```
peerhub session open --workspace-scope-id ws1 --room-id room1 --actor-principal-id p1 --instance-id i1 --profile-id cx.standard --session-fingerprint fp1
```
That's **6 required flags** on a single command, all with verbose `--kebab-case-long-names`. `session heartbeat` and `session close` are **7 required flags each**. `duty claim` is 5 required flags.

These commands are designed for programmatic invocation by the hub.py-replacement coordinator, not for humans. That's fine — but they occupy the same `--help` tier as `ask`, which IS for humans.

### 1.4 Finding: No short flags anywhere

**Evidence:** Zero `-x` single-character aliases across all 543 flags. Not a single one. For the primary commands:

- `--capability-tier` could be `-t`
- `--workspace` could be `-w`
- `--json` could be `-j`
- `--profile` could be `-p`

### 1.5 Proposed changes

#### P1: Default `--capability-tier` to `READ_ONLY` on `ask`

**Before:** `peerhub ask ag "hello" --capability-tier READ_ONLY`
**After:** `peerhub ask ag "hello"` (defaults to `READ_ONLY`)
**Escalation:** `peerhub ask ag "write this file" --capability-tier WORKTREE_WRITE` (still explicit)

**Rationale:** READ_ONLY is the safe, restrictive default. The 80/20 pattern for `hub.py` dispatches (and for the README's own examples) is READ_ONLY. Making it the default removes friction from the most common case while preserving explicit escalation for mutations.

**Risk:** A user who forgets to escalate gets a READ_ONLY dispatch that may fail if the peer needs write access — but this is the same failure they'd get with a wrong explicit tier, and the error message is already clear.

#### P2: Add short flags to the primary commands

| Long flag | Short | Applies to |
|---|---|---|
| `--capability-tier` | `-t` | `ask`, `broadcast` |
| `--workspace` | `-w` | all commands that accept it |
| `--json` | `-j` | all commands that accept it |
| `--profile` | `-p` | `ask` |
| `--peer` | (already positional on `ask`) | `status`, `diag` |

**Implementation scale:** Mechanical — one `add_argument` change per alias. ~30 lines of `cli.py` changes.

#### P3: Split `--help` output into command tiers

Organize `peerhub --help` to show tiered groups instead of a flat list:

**Before:**
```
commands:
  workspace, status, config, backup, adapter, diag, broadcast, health,
  peer, lease, broker, gate, ask, statusline, consensus, task, lesson,
  directive, node, lock, artifact, role, routing, leadership, feedback,
  error, alert, room, duty, session
```

**After:**
```
Primary commands:
  ask         Send one prompt to a real peer CLI
  broadcast   Broadcast one prompt to multiple peers
  status      Show the current workspace status
  diag        Show live peer diagnostics and quota telemetry

Setup & config:
  workspace   Manage the peerhub workspace itself
  adapter     Manage peerhub adapters
  config      Inspect peerhub's own resolved configuration
  backup      Back up or restore one workspace

Operations (peer infrastructure):
  health      Manage peer health
  peer        Inspect and recover peer nodes
  lease       Inspect session leases
  gate        Check dispatch gate condition
  node        Manage the peer node registry
  routing     Discover candidates and elect leaders
  leadership  Manage the workspace-global leadership slot

Governance (hub.py parity — programmatic use):
  consensus   Manage consensus rounds
  task        Manage task lifecycles
  lesson      Manage governance lessons
  directive   Manage governance directives
  room        Manage rooms and messages
  duty        Manage terminal duty
  session     Manage room-participation sessions
  alert       Raise durable alerts
  error       Record operational-error evidence
  feedback    Manage the feedback journal
  lock        Manage durable file locks
  artifact    Manage durable named artifact records
  role        Manage workspace role assignments
  broker      Inspect governance effect delivery
  statusline  Format live statusline for an AI peer
```

**Implementation:** This requires either switching from raw `argparse` to a framework that supports command groups (like `click` with `Group`) or custom `--help` formatting. The `argparse` approach would use a custom `HelpFormatter` subclass — about 40 lines of code. A full migration to `click` is more investment (~2 days) but would also give short flags, automatic shell completion, and cleaner per-command modules.

**Risk:** Changing the `--help` visual layout is backward-compatible (no command names change). A `click` migration changes the parser but preserves the same CLI contract.

#### P4: Split `cli.py` into per-command modules

**Before:** 4,509 lines in one file, with `main()` at line 2672 containing 1,372 lines of argparse definitions followed by a cascade of `if parsed.command == "X": return _run_X(parsed)`.

**After:**
```
peerhub/
  cli/
    __init__.py      # main(), tiered help formatter
    ask.py           # _run_ask, ask parser
    broadcast.py     # _run_broadcast, broadcast parser
    status.py        # _run_health, status parser
    diag.py          # _run_diag, diag parser
    config.py        # config subcommands
    backup.py        # backup subcommands
    governance/      # consensus, task, lesson, directive, room, duty, session, etc.
      __init__.py
      consensus.py
      task.py
      ...
    ops/             # health, peer, lease, gate, node, routing, leadership
      __init__.py
      health.py
      ...
```

**Implementation scale:** Large refactor (~2–3 days), but purely mechanical. Every `_run_X` function and its parser definition move together. No functional changes.

**Risk:** Import ordering changes could surface latent circular dependencies. Mitigated by having each module import only what it needs from the service layer.

---

## 2. Install/first-use experience

### 2.1 Finding: `pip install peerhub` → first dispatch has reasonable but improvable friction

**Current first-use flow:**
```bash
pip install peerhub               # ✓ works, installs 'peerhub' entrypoint
peerhub --version                 # ✓ works
peerhub ask ag "hello"            # ✗ error: --capability-tier is required
peerhub ask ag "hello" --capability-tier READ_ONLY
                                  # ✓ works (if ag/agy.exe is on PATH and authenticated)
```

The `--capability-tier` stumble is the main friction point. With the P1 default proposed in §1.5, the flow becomes:
```bash
pip install peerhub
peerhub ask ag "hello"            # ✓ works immediately (defaults to READ_ONLY)
```

### 2.2 Finding: `--workspace .` defaulting to cwd is correct

Every command that takes `--workspace` defaults to `"."` (current directory), which is the right default. The workspace is auto-initialized on first `ask` via the direct-ask admission bootstrap (`peerhub.direct-ask/v1`), so no manual `workspace init` is needed for the primary use case. This is good design and needs no change.

### 2.3 Finding: `adapter discover` is a strong first-use diagnostic

```bash
peerhub adapter discover --json
```
This probes each peer CLI and reports `MEASURED/UNAVAILABLE/ABSENT` — exactly the right diagnostic for "why isn't my `ask` working?" No change needed.

### 2.4 Finding: No `python -m peerhub` support

**Evidence:** `peerhub/__main__.py` does not exist. While `pyproject.toml`'s `[project.scripts]` correctly installs a `peerhub` entrypoint, the `python -m peerhub` convention is a standard Python packaging expectation.

**Proposed:** Add `peerhub/__main__.py`:
```python
"""Allow `python -m peerhub`."""
from peerhub.cli import main
raise SystemExit(main())
```

**Implementation scale:** 3 lines, one new file. Zero risk.

---

## 3. Config/dotdir layout

### 3.1 Finding: The 2026-09-09 consolidation is in good shape

The config hierarchy (`docs/config-hierarchy.md`) is well-documented, the precedence rules are clear, `config validate` provides real diagnostics, and the workspace/global separation is correct.

**No loose ends found worth closing in this round.** Specifically:

- `PEERHUB_CONFIG_HOME` override works as documented.
- `config migrate` handles the legacy `.peerhub/*.json` → `.peerhub/config/*.json` transition.
- `config init` scaffolds starter files correctly.
- No confusion between `.peerhub/` and `.ai/` — they serve completely different purposes (peerhub workspace state vs. hub.py operational data), and both are gitignored.

### 3.2 Finding: `.ai/` at repo root is hub.py's, not peerhub's

`.ai/` contains `leases.json`, `mailbox.json`, `ask_history.jsonl`, `routing_metrics.jsonl` — all hub.py runtime state. It is correctly gitignored (line 226: `.ai/`, with comment "Local hub instance runtime state"). **This is not peerhub's responsibility to clean up** — it will naturally disappear when hub.py is retired.

---

## 4. Root folder hygiene

### 4.1 Finding: `scratch/` — gitignored, but worth noting

**Evidence (.gitignore lines 231–235):**
```gitignore
# Ad-hoc review/probe scratch work (e.g. adversarial design-round probes
# like the one accidentally committed and removed 2026-09-09, see
# docs/reviews/p-drive-folder-structure-mece-review-2026-09-09.md) --
# never meant to be permanent repo content.
scratch/
```

Contents: 9 `patch_*.py` / `append_*.py` scripts (hotfix/probe code) + 5 `pytest-item*` work directories. All gitignored, with a documented explanation. **This is not hygiene debt** — it's a well-managed scratch area. No change needed.

### 4.2 Finding: `.hypothesis/` and `.pytest_cache/` — standard ephemeral state

Both are gitignored by the standard Python `.gitignore` template (lines 53–54). Present because `hypothesis` is a dev dependency and tests run locally. **No debt.**

### 4.3 Finding: `alembic/` and `alembic.ini` — vestigial tracked content

**Evidence:**
- `alembic.ini` (5,008 bytes) and `alembic/` directory are tracked in git.
- The README (line 39) explicitly states: "Alembic runtime cutover: Ratified as HOLD. The bespoke runner remains the sole runtime migration engine."
- `alembic` is a dev dependency only (`pyproject.toml` line 41: `dev = [..., "alembic>=1.13.0"]`).

These files represent a deferred migration path. They are tracked intentionally as a design record/future starting point, not hygiene debt. **No change proposed**, but it's worth noting that if the HOLD is indefinite, these could eventually move to a branch.

### 4.4 Finding: `scripts/` — one historical migration script

Contains only `migrate_engram_directives_2026_09_03.py` (5,646 bytes) — a one-time migration script. This could move to `tools/` for consistency, but it's a single file and not causing confusion. **Low priority.**

### 4.5 Finding: `.gitignore` is 245 lines, mostly template boilerplate

**Evidence:** Lines 1–218 are the GitHub Python `.gitignore` template (including sections for Django, Flask, Scrapy, Celery, Redis, RabbitMQ, ActiveMQ, SageMath — none of which peerhub uses). Lines 219–245 are peerhub-specific rules.

**Proposed:** Trim the template to only the rules that matter: Python bytecode, distribution, test/coverage, environments, type checkers, and peerhub-specific entries. This would reduce the file from 245 lines to ~50.

**Risk:** None; unused patterns have no effect. This is pure cosmetic cleanup.

---

## 5. README length/structure

### 5.1 Finding: The README is a project journal masquerading as a quick-start guide

**Evidence:** 21KB / 173 lines. The "Status" section (lines 5–65) is 60 lines of historical narrative covering:
- A 2026-09-07 regression disclosure (lines 9–16)
- A feature-by-feature implementation inventory (lines 18–34)
- A "designed but not built" section (lines 35–37)
- "Explicitly deferred" items (lines 38–43)
- "Not yet implemented / honest gaps" (lines 44–49)
- Cross-references to 7 other design documents (lines 50, 52, 54, 56, 58, 60, 62, 64)
- A LegacyTranslator retirement narrative (line 54)
- A production-integration-gaps-closed narrative (line 56)
- A v0.3.0 release note (line 58)
- A config-consolidation narrative (line 60)
- A hub.py-replacement design phase narrative (line 62)
- Architecture debate citation (line 64)

This is valuable project history. It should not be deleted. But it should not be the first thing a new user reads.

### 5.2 Finding: The "Try it" section showcases every command equally

Lines 91–138 show 22 CLI examples — from `peerhub --version` (primary) to `peerhub session open --workspace-scope-id ws1 ...` (deep governance plumbing). A new user cannot distinguish "start here" from "advanced API" from this flat listing.

### 5.3 Proposed README restructure

**Before (21KB, one long scroll):**
```
# peerhub
Status (60 lines of history)
Install (24 lines)
Try it (47 lines — 22 examples)
Run the tests (13 lines)
```

**After (~3KB quick-start + link to full status):**
```
# peerhub

One-liner description.

## Quick start

pip install peerhub
peerhub ask ag "say hello in exactly three words"
peerhub diag
peerhub status

## Install

Three options (same as today).

## Key commands

| Command | What it does |
|---------|-------------|
| peerhub ask PEER PROMPT | Dispatch a prompt to a real peer CLI |
| peerhub broadcast PROMPT --peers ag,cx | Fan-out to multiple peers |
| peerhub diag | Live diagnostics and quota telemetry |
| peerhub status | Workspace health check |
| peerhub config validate | Check config layers |
| peerhub adapter discover | Find installed peer CLIs |

See `peerhub --help` for the full command list, including governance/room/session commands for hub.py-parity programmatic use.

## Status

Current version: v0.3.0. `peerhub ask` works end-to-end.
For the full status journal, architecture decisions, and design history, see docs/design/README.md.

## Run the tests
(same as today)
```

The existing 60 lines of status narrative move to `docs/design/README.md` (which already exists as a 5,150-byte design-docs index) or a new `docs/STATUS.md`, with the README linking to it.

**Implementation scale:** ~1 hour. The content is preserved, just relocated.
**Risk:** Users who are used to finding status details in the README would need to follow one link. Mitigated by keeping a one-line status summary in the README.

---

## 6. Summary of findings and proposed changes

### Disposition relative to the 2026-09-07 architecture simplicity finding

The 2026-09-07 finding ("core `ask` sound but buried in enterprise-CQRS ceremony; proposals/consensus redundant") is **validated and extended** by tonight's empirical audit. The "buried" characterization is accurate: 103 command paths and 543 flags in a flat namespace, with no visual or organizational separation between the 4 commands a user needs and the 99 governance-API endpoints. This proposal provides the concrete simplification plan that the 2026-09-07 note lacked.

The "proposals/consensus redundant" part of that finding is **architecture** (out of scope tonight). As a CLI-surface observation, however: `consensus propose`, `consensus proposal-add`, `consensus proposal-vote`, and `consensus vote` are four overlapping subcommands that do approximately the same thing (create/vote on a consensus item), which is worth flagging as API-level redundancy even if the internals are out of scope.

### Change priority matrix

| ID | Change | Scope | Effort | Impact on daily use |
|---|---|---|---|---|
| **P1** | Default `--capability-tier` to `READ_ONLY` | 1 line in `cli.py` | 5 min | **HIGH** — removes friction from every `ask` invocation |
| **P2** | Add short flags (`-t`, `-w`, `-j`, `-p`) | ~30 lines | 30 min | **MEDIUM** — faster typing for repeated use |
| **P3** | Tiered `--help` output | ~40 lines (formatter) or ~2 days (click migration) | 30 min – 2 days | **HIGH** — discoverability |
| **P4** | Split `cli.py` into per-command modules | ~4,500 lines reorganized | 2–3 days | LOW (internal only), but enables P3 |
| **P5** | README restructure (status → docs/) | ~100 lines moved | 1 hour | **HIGH** — first impression |
| **P6** | Add `__main__.py` | 3 lines | 5 min | LOW |
| **P7** | Trim `.gitignore` boilerplate | ~195 lines removed | 15 min | NEGLIGIBLE |
| **P8** | `broadcast` default `--capability-tier` to `READ_ONLY` | Already done (line 2782) | 0 | Already correct |

### Implementation order (dependency-respecting)

1. **P1** (standalone, zero dependencies)
2. **P2** (standalone, no dependencies)
3. **P6** (standalone)
4. **P5** (standalone)
5. **P7** (standalone)
6. **P4** → **P3** (P3 is easier after P4, but P3's formatter-only variant can be done independently)

### Risks and tradeoffs

| Risk | Mitigation |
|---|---|
| P1: Changing `--capability-tier` default could surprise callers that assumed it was always explicit | Add a deprecation period: first release warns when the flag is omitted ("Defaulting to READ_ONLY; pass --capability-tier explicitly to suppress this message"), then silence the warning in the next release |
| P3/P4: Refactoring `cli.py` could break `pyproject.toml`'s `peerhub = "peerhub.cli:main"` | Keep `peerhub.cli:main` as the entry point; the new `peerhub/cli/__init__.py` exports `main()` |
| P5: External documentation/tutorials may link to README sections by anchor | Use the same heading text ("Install", "Try it") in the new structure; redirect anchors if needed |

---

## 7. Explicit non-changes (things this review examined and deliberately left alone)

1. **Config/dotdir consolidation:** In good shape. No changes proposed.
2. **`.ai/` at repo root:** hub.py state, correctly gitignored, not peerhub's responsibility.
3. **`scratch/`:** Gitignored, documented, correctly managed.
4. **`.hypothesis/`, `.pytest_cache/`:** Standard test framework ephemeral state.
5. **`alembic/`, `alembic.ini`:** Tracked intentionally as a deferred design record.
6. **The governance command surface (consensus/task/lesson/room/duty/session):** These are API-contract endpoints for hub.py-replacement machinery. Their verbosity is appropriate for programmatic invocation; the problem is only that they share the same `--help` tier as user-facing commands (addressed by P3).
7. **Core architecture:** Explicitly out of scope per the task definition.
