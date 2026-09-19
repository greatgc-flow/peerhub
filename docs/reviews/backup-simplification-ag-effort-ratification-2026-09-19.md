# ag.effort DIR-006 ratification: single-folder backup/restore/reset simplification (2026-09-19)

**Role:** `ag.effort`, standing in for `cx` as a one-time, user-authorized substitute vote (DIR-006 exception, not a policy change). See `docs/design/peerhub-r4-p4b-converged-design-2026-09-17.md` for the precedent case earlier the same day.

**Target document:** `docs/design/engram-peerhub-single-folder-backup-simplification-2026-09-17.md`

**Verdict: RATIFY WITH AMENDMENT.** Full amendment text applied to the target document at ratification time; see that document's current state for the merged result.

**Verification discipline applied by cc after receiving this verdict:** every specific file:line citation below was independently re-read against the real source files before the ratification was accepted. All confirmed accurate except one line-range imprecision, which the report itself flagged and cc corrected in the target document (see "Citation-level corrections" below).

---

## (a) Per-question answers

### 1. Central claim: physical separation is a genuine dead end
**Confirmed accurate.** `_sys/env.json`'s `tool_env_vars` table (engram-repo, `D:\Engram&Peerhub\engram-main-worktree\_sys\env.json:15-19`) maps exactly one env var per AI vendor tool to its whole config root (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `GEMINI_DIR`, `GH_CONFIG_DIR`, `PEERHUB_CONFIG_HOME`) -- unlike `npm`/`python` entries in the same file, which do define separate prefix-vs-cache variables. `docs/engram-dotdir.md:41-42` (engram-repo) explicitly bars reintroducing directory junctions. `docs/design/dotdir-consolidation-RATIFIED-2026-09-09.md` §2.3 (line ~218) and §4.1 (line ~333) already ruled `.engram/` a live redirect root with backup defined by explicit allowlist, never directory membership.

### 2. Soundness of reframing "one folder" as the backup artifact (zip)
**Confirmed sound.** Matches the already-ratified allowlist rule (dotdir-consolidation §4.1/§4.2) and avoids leaking credentials (`codex/auth.json`, `gh/hosts.yml`, `claude/.credentials.json`) or shipping large caches, which a raw directory copy would do.

### 3. PeerHub WAL-mode correctness and existing backup commands
**Confirmed accurate.** `peerhub/persistence/sqlite.py:380-381` runs `PRAGMA journal_mode = WAL` / `PRAGMA synchronous = FULL` on every non-readonly connection. On-disk spot check confirmed live `-wal`/`-shm` sidecars across three separate workspace roots. `peerhub/application/backup.py:1-15` documents and implements `sqlite3.Connection.backup()`-based online backup, never a raw file copy. Shipped CLI surface: `peerhub/cli/commands/setup.py:88-114` (`backup workspace --output DIR`, `backup restore BUNDLE`), documented at `docs/STATUS.md:62`. No new peerhub engineering needed.

### 4. Open item 1 -- vendor mechanism ag/cc may have missed
**None found.** Direct binary inspection of `claude.exe`, `codex.exe`, and `agy.exe` confirmed: Codex's `CODEX_SQLITE_HOME` relocates only internal SQLite telemetry/queue DBs, not `cache/`, `tmp/`, or `auth.json`; Antigravity's `ANTIGRAVITY_APP_DATA_DIR` shifts the entire app root, not just cache. No vendor exposes an isolated cache-only or credential-only redirect.

### 5. Open item 2 -- `engram reset` default scope
**Recommendation adopted: `.engram/` only by default; `workspace/` requires explicit `--all`.** Reasoning: `engram reset` runs while Engram stays installed (unlike `uninstall --purge-data`, which removes the whole product); `workspace/` holds real project source/git clones, so deleting it by default on a "reset my AI config" command would violate least-astonishment. `--all` reuses the existing typed-folder-name confirmation gate at `_sys/core/uninstaller.py:217-226` rather than building a second one.

### 6. Open item 3 -- risk of first-class CLI verbs
**Three concrete risks identified, all resolved as new safety-invariant requirements added to the design doc:** (1) process-liveness race -- `restore`/`reset` must refuse to run while `claude.exe`/`codex.exe`/`agy.exe` are active; (2) accidental in-place overwrite on restore -- mitigated by an automatic pre-restore snapshot to `<base-dir>/_sys/data/backups/pre_restore_<timestamp>.zip`; (3) backup-time write-consistency -- warn if AI CLI processes are running during `engram backup`.

### 7. Citation-level errors from prior rounds
See "Citation-level corrections" below.

## (b) Citation-level corrections found (independently verified by cc, both real)

1. The original document's "section 4.1, and its cache-inventory around line 218" conflated two separate sections of `dotdir-consolidation-RATIFIED-2026-09-09.md` -- the cache-inventory table is in §2.3 (line 218), the allowlist ruling is in §4.1 (line ~333). Corrected in the target document.
2. The original document's unqualified references to `_sys/env.json`, `docs/engram-dotdir.md`, and `README.md` are ambiguous, since this design doc lives in the `peerhub` repo, which has none of those three files at those paths -- they all refer to Engram's own repo (`engram-repo`, i.e. `D:\Engram&Peerhub\engram-main-worktree`). Corrected in the target document's status header.

## (c) Final verdict

**RATIFY WITH AMENDMENT.** Justification: the ag.deepthink+cc synthesis is structurally and technically sound -- it honors the ratified no-junctions constraint, correctly extends the allowlist-based backup model, and correctly identifies that PeerHub's existing `backup workspace`/`restore` commands already fully solve PeerHub's side with zero new engineering. Amendment was required to: close open item 2 with a concrete, reasoned default (`.engram/`-only, `--all` for workspace); add three safety invariants that did not exist as a requirement in the standalone-script form; and fix two citation-precision issues across repo boundaries. Implementation may now begin.
