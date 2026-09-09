# P:\ Folder/File Structure MECE Review (2026-09-09)

> Requested: check whether P:'s folder/file structure cleanly separates
> permanent (durable, must-backup) content from temporary/ephemeral
> content, specifically for "personal" data -- AI memory, backlog,
> settings -- such that a single-folder copy would give a full
> backup/restore. Status: safe cleanup done; a real physical
> reorganization was investigated, found genuinely risky, and deferred
> per explicit user decision -- this doc records both what was done and
> what remains, so the deferred work isn't lost.

## 1. Completed: Dead Scratch Project-Folder Cleanup (safe, no P: commit needed)

`_sys/claude/config/projects/` -- Claude Code auto-creates one folder per
distinct working-directory path ever used, with a per-project `memory/`
subfolder for the real one (`P--`, i.e. `P:\`). This directory is
gitignored (`.gitignore:83`, `_sys/claude/config/projects/`), so none of
this was ever git-tracked -- cleanup here needed no P: commit and doesn't
touch the freeze.

Before: 30 project folders, ~295MB. Inspected every folder's content: 22
of them contained ONLY a single raw session-transcript `.jsonl` file (no
`memory/` subfolder, no durable content) and corresponded to already-closed,
already-fully-documented-elsewhere investigations (the `bug1-direct-ask-
admission0` fresh-workspace investigation, the `D:\ClaudeDev` fresh-install
verification round, assorted one-off probe/scratch dirs) -- see
`P--/memory/project_fresh_install_verification_2026_09_08.md` and
`project_peerhub_ask_bootstrap_deadlock_critical_2026_09_07.md` for where
those investigations' real findings already live durably.

Deleted those 22. **8 remain**, all genuinely active:
`P--` (the real, current project -- 196-file `memory/`), `D--Engram-
Peerhub-PortableDev--v2-1-` (same logical project, accessed via its real
`D:\` path instead of the `P:\` SUBST alias), `P--peerhub`/`P--workspace-
peerhub`/`D--...--peerhub`/`D--...--workspace-peerhub` (peerhub work,
duplicated 4 ways across the SUBST-vs-real-path and repo-root-vs-workspace-
prefix distinctions), `P---sys-core` (hub.py-adjacent work), and
`P---sys-env-nodejs-npm-global` (16 real session logs -- this is where
`hub.py` sets `cwd` when invoking `claude.cmd`/`codex.cmd`/etc. as
subprocesses for peer dispatch, so it's a genuinely active, recurring
location, not scratch -- confirmed by content before keeping it).

## 2. Real Finding: Personal/Durable Data Is Scattered Across 3 Locations, Each With Its Own Internal Mixing Problem

**Location A -- `_sys/claude/config/` (top level)**: settings/config
(`settings.json`, `CLAUDE.md`, `.credentials.json`) sit flat alongside
clearly-ephemeral cache/log files (`history.jsonl`, `stats-cache.json`,
`status_input.log`, `.last-cleanup`) with no folder split. Only 3 files
here are actually git-tracked (`settings.json`, `statusline-command.sh`,
and one more) -- moving the untracked ones needs no commit, but
`settings.json` is tracked, so any physical move of it would.

**Location B -- `_sys/claude/config/projects/P--/memory/`**: the real
memory system (196 files) -- now much easier to find after part 1's
cleanup (8 project folders instead of 30), but still nested inside a
Claude-Code-owned, per-cwd-path auto-managed directory structure that
isn't really "yours" to restructure (Claude Code itself decides this
layout; see part 4 below for why this constrains what's actually fixable).

**Location C -- `_sys/ai/` (top level)**: 21 git-tracked, durable
config/schema/policy files (`orchestration.json`, `protocol.json`,
`backlog.json`, `model-registry.json`, `governance_params.json`, etc.)
sit in the exact same flat directory as ~9 untracked, purely-ephemeral
runtime-state files (`ask_history.jsonl`, `leases.json`, `mailbox.json`,
`nodes.json`, `routing_metrics.jsonl`, `state.json`, `task_registry.json`).
**This is the sharpest instance of the problem the user described** --
there is no way to "just copy the durable config" without also
manually excluding the runtime files by name, because there's no folder
boundary between them at all.

**Contributing structural quirk (not fixable by reorganizing files)**:
P:\ is reachable via both a SUBST drive letter (`P:\`) and its real path
(`D:\Engram&Peerhub\PortableDev (v2.1)\`). Claude Code's project-identity
model keys strictly off the literal cwd path string, so using both
conventions across sessions creates genuinely duplicate project folders
for the same logical project (confirmed: `P--` and `D--Engram-Peerhub-
PortableDev--v2-1-` are the same project, just two path spellings; same
for peerhub's 4-way duplication in part 1). No file move fixes this --
only consistently using one path convention going forward would.

## 3. Investigated and Deferred: Physical Reorganization of `_sys/ai/`

The obvious fix for Location C -- split `_sys/ai/` into e.g. `config/`
(the 21 tracked files) and `runtime/` (the ~9 untracked files) -- was
investigated for real risk before attempting it, per standing practice
(never execute a structural change on a live, actively-relied-upon system
without checking blast radius first).

**Finding: genuinely high risk, not attempted.** `grep -c "_sys/ai/\|ai_root"
_sys/core/hub.py` returns **963** -- `hub.py` (the single ~12,000-line
orchestrator this whole environment's peer-dispatch machinery depends on,
including tonight's still-pending AI-collaboration MECE re-audit once cx's
quota resets) references these paths extremely heavily, and a first pass
found no evidence of a single centralized path-resolution point that would
make a folder move a small, contained change -- individual filenames
(`orchestration.json`, `backlog.json`, `leases.json`, etc.) appear to be
referenced directly in many places throughout the file. Physically moving
any of these files risks breaking live peer-dispatch functionality for the
rest of this session's active work, for a structural cleanup that has no
functional urgency tonight.

**User decision (explicit, asked via AskUserQuestion after this risk was
found)**: defer the actual reorganization. Document the finding now (this
doc); do the real work in a separate, dedicated session with time to
properly trace and update every one of hub.py's 963 references first (or
find/confirm a centralized path-resolution seam that makes it safe), not
as a rushed add-on to an already-long overnight session.

## 4. Recommended Path Forward (for that future session)

Two real options were on the table when this was deferred; re-evaluate
which fits once actually executing:

1. **Safer, no-move alternative** (was the recommended option when this
   was last discussed): write a small backup/export script that COPIES
   (never moves) the confirmed-durable set (`_sys/ai/`'s 21 tracked
   files, `_sys/claude/config/settings.json` + `CLAUDE.md`,
   `_sys/claude/config/projects/P--/memory/`) into one consolidated
   output folder on demand. Zero risk to hub.py's existing path
   assumptions since nothing at the source moves; "one folder = full
   backup" becomes true for the generated snapshot, on demand, rather
   than for the live source tree.
2. **Real physical reorganization**: only after tracing all 963 `hub.py`
   references (and everything under `_sys/checks/`, `_sys/cli/`, etc. that
   may also reference these paths -- not yet checked) and confirming
   either a safe centralized update point or a complete, verified list of
   every call site to fix. Higher-value long-term (fixes the root cause
   instead of working around it) but real, non-trivial effort -- do not
   attempt this under time pressure or late in a long session.

Either way, `_sys/claude/config/projects/`'s Claude-Code-managed layout
(part 1/2's Location B) isn't something to restructure directly -- it's
owned by the Claude Code application itself, not this project's code.
