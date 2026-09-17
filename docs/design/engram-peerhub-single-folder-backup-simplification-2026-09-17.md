# Single-folder backup/restore/reset simplification — Part A/B design (Engram + peerhub)

**Status:** 2-voice converged (ag.deepthink + cc), pending cx (rate-limited until 2026-09-19). Design only, no implementation yet. Scope: can each package be simplified enough that a dedicated backup script isn't needed — ideally "copy/delete one folder" for backup/restore/reset? P:\ (frozen legacy) is explicitly out of scope.

## Prior art this extends, not re-litigates

`docs/design/dotdir-consolidation-RATIFIED-2026-09-09.md` (section 4.1, and its cache-inventory around line 218) already rejected "backup = wholesale directory copy" for `.engram/{claude,codex,agy}/`, because each AI CLI vendor's own config root mixes real credentials (`codex/auth.json`, `gh/hosts.yml`) and large caches (`cache/`, `shell-snapshots/`, `daemon/`, `telemetry/`, etc.) together with genuinely durable data (memory, settings, rules, skills, session transcripts) inside one vendor-owned directory tree Engram doesn't control the internal layout of. The ratified fix, already built and shipping: `_sys/checks/backup_personal_data.py`'s explicit named allowlist.

## Engram: why finer physical separation is a dead end (verified)

Independently confirmed by direct source reading: `_sys/env.json`'s `tool_env_vars` maps exactly one environment variable per tool to exactly one `{base, sub}` target (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `GEMINI_DIR`, each a single value, each redirecting the tool's *entire* config root — there is no second, vendor-respected env var for "just the cache" or "just the credentials"). Two consequences, both dead ends for a purely physical fix:

1. **No native env-var split exists.** These vendors don't expose a way to point durable state and cache/credentials at two different locations — confirmed against `_sys/env.json`'s actual table, not assumed.
2. **A directory-junction-based split would work mechanically but violates an already-ratified architectural constraint.** `docs/engram-dotdir.md` is explicit: "Engram does not use SUBST drives or directory junctions... that machinery has been completely removed in favor of pure environment-variable redirection." Reintroducing junctions just to split one vendor's cache from its config would reverse a deliberate, already-shipped decision for a narrow gain.

**Converged recommendation: reframe "one folder" as the backup *artifact*, not the live state layout.** `.engram/` stays exactly as it is today (a live, vendor-managed, gitignored root — this was never going to change). What changes is ergonomics: elevate the existing, already-correct `_sys/checks/backup_personal_data.py` into first-class `engram` CLI verbs:

```
engram backup [--out PATH]     # today: python _sys/checks/backup_personal_data.py --backup [--out PATH]
engram restore PATH [--force]  # today: python _sys/checks/backup_personal_data.py --restore PATH [--force]
engram reset [--yes]           # new: wraps the existing uninstall --purge-data data-deletion path (README.md's
                                # documented `uninstall --purge-data` already deletes .engram/ + workspace/ with
                                # "un-bypassable typed confirmation" -- reuse that confirmation gate, don't build a
                                # second one) for the case where the user wants to wipe personal state without
                                # uninstalling the program itself.
```

To the user, the *output* of `engram backup` genuinely is one clean, pristine folder — copy it, zip it, move it to a USB drive, exactly like a manual folder copy, except it's actually safe (the allowlist already guarantees zero credential leakage by construction — a credential file simply has no entry in `ITEMS` that could ever copy it). This satisfies the stated goal ("폴더 하나만 복사") without reopening the risk the 2026-09-09 ratification correctly closed. `engram reset` on the *live* root is already a plain, safe folder delete today (`.engram/` has no cross-references from other live state that would be corrupted by deleting it) — no script needed for that direction; the CLI verb is purely for a friendlier confirmation UX around a real delete, reusing the existing `--purge-data` gate, not new safety logic.

## PeerHub: already effectively "one folder," with one real caveat

`<workspace>/.peerhub/` (`peerhub/core/context.py`'s `PathLayout.for_workspace()`) is confirmed durable-only, no credential mixing (D-CTX credentials live in a fully separate, always-ephemeral OS-temp path via `resolve_workspace_temp()`, `peerhub/application/config_paths.py` — never inside `.peerhub/`).

- **Reset (delete):** genuinely just `rm -rf .peerhub/` / `Remove-Item -Recurse`. No script needed, no caveat.
- **Backup (copy):** **not safe as a raw manual folder copy.** `peerhub.sqlite3` runs in WAL mode (its `-wal`/`-shm` sidecar files are real evidence of this, independently observed today during the P:\ folder survey). A plain OS-level copy taken while a write transaction is in flight can capture an inconsistent snapshot. This is exactly why `peerhub backup workspace`/`backup restore` (already shipped, SQLite online-backup-API-based, `docs/STATUS.md`) exists and remains the correct tool for this direction — not a simplification target, a correctness requirement. **Conclusion: peerhub already satisfies the user's goal for reset; for backup, the existing CLI command already IS the simplest correct answer (one command, not a folder copy) — no further engineering needed here.**

## What's actually left to build (the only real deliverable from this round)

Wire `_sys/checks/backup_personal_data.py`'s existing, correct logic into `engram backup`/`engram restore`/`engram reset` as real CLI subcommands (today it's a standalone script the user has to know exists and invoke with `python _sys/checks/...`). This is a thin CLI-surface addition over already-correct, already-tested logic — not new backup logic. Peerhub needs no equivalent work; `peerhub backup workspace`/`restore` already exists at the CLI level.

## Open items for cx (and any further cc/ag rounds)

1. Does cx agree the physical-split options are genuinely dead ends, or does it find a vendor mechanism ag/cc missed?
2. Scope check on `engram reset`: should it default to confirming and deleting `.engram/` only, or also `workspace/` (per the existing `--purge-data` semantics)? README.md's default `uninstall` leaves both untouched; `--purge-data` deletes both together. A reset-without-uninstall command may want its own, possibly narrower, default — worth an explicit decision, not an assumption.
3. Any real risk in exposing `engram backup`/`restore` as first-class verbs that the standalone-script form didn't have (e.g. discoverability making people run `engram reset` more casually than they'd run a python script by hand)?
