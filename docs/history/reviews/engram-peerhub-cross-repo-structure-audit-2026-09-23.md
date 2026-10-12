# Engram/peerhub Cross-Repo Structure Audit + peerhub Remediation — 2026-09-23

## Trigger

Between 2026-09-21 and 2026-09-22, Engram went through 12 commits (v3.3.1 →
v3.3.7) without a cc terminal present; the v3.3.7 release notes claim a
"Dual cc/cx Peer Sign-off" that no artifact in either repo backs (no
`docs/reviews/` entry, empty `_sys/ai/ask_history.jsonl` for the window).
The user attributes the claim to ag acting alone; treat it as unverified,
not confirmed, until/unless further evidence surfaces. This audit is the
first real independent review of that stretch of work, done retroactively
plus two forward-looking checks the user asked for:

1. Do the Engram v3.3.2-v3.3.7 fixes have an applicable equivalent gap in
   peerhub? (dispatched to cx.effort)
2. Is every file in both repos in the right place, matching each repo's own
   stated conventions? (dispatched to ag.opus)

## Method

- Engram (`D:\Engram&Peerhub\engram-main-worktree`, HEAD `24b6564`): full
  pytest run reproduced independently by the terminal (not taken from
  commit-message claims) — `505 passed, 3 skipped, 6 xfailed`. v3.3.7
  release zip downloaded and sha256-verified against
  `_sys/core/release-manifests/3.3.7.json` (`299f7346...`); scanned for
  leaked `.jsonl`/backup/credential files (none found, unlike the v3.3.0
  incident). One code path spot-checked directly (the v3.3.5 swap-lock
  retry fix in `provisioner.py`) — confirmed it correctly wires into
  `deploy()`'s status classification, no regression.
- cx.effort dispatched with the 8 relevant Engram commits summarized
  in detail and 5 concrete questions (A-E) about analogous gaps in
  peerhub's persistence/CLI/adapter/path code. cx's sandbox could read
  peerhub but not write to it (filesystem guard scoped to the PortableDev
  workspace) — it diagnosed one real gap (below) but could not apply the
  fix; the terminal implemented it directly from cx's diagnosis.
- ag.opus dispatched for a read-only MECE placement audit of both repo
  trees (no implementation). Its executive summary listed 6 "certain"
  items and 6 "worth a second opinion" items; the terminal independently
  re-verified every "certain" item against the actual git-tracked state
  before acting on any of them (two turned out to be false positives from
  reading local working-tree state instead of tracked files — see below).

## Findings & Disposition

### A. cx.effort's applicable-gap check (Engram → peerhub)

| # | Question | Verdict | Action |
|---|---|---|---|
| A | Windows atomic-swap retry-on-lock parity with Engram's `_safe_rename` | **Real gap** — `peerhub/dispatch/materializer.py:391`'s `os.replace()` had no retry on transient `PermissionError`, unlike Engram's post-v3.3.5 `provisioner.py`. Fixed (below). | Fixed |
| B | Substring (not boundary) peer-identity matching | Not found — `adapters/registry.py` uses exact mapping/membership. | No action |
| C | Unvalidated comma-separated CLI scope list | Not found — `cli/commands/daily.py:440-444` already validates/resolves every broadcast target before admission. | No action |
| D | Hardcoded drive-letter / root-metacharacter assumptions | Not found — no hardcoded drive paths; `.cmd` wrapper bypass in `dispatch/pipe.py` already avoids the cmd.exe metacharacter class of bug. | No action |
| E | Self-managed CLI tool update delegation (`self_update_peer` pattern) | Not applicable — peerhub only discovers/invokes peer CLIs read-only, it has no install/update manager to delegate from. | No action |

**Fix applied (item A):** `ArtifactMaterializer._replace_with_retry()` added
(5 attempts, `0.1s * 2^n` backoff on `PermissionError`), used for the
staging→target atomic rename. On exhausted retries the result status
changed from `HARD_FAILURE` to `RETRYABLE_FAILURE` (a transient lock is,
by definition, worth a later retry — matches the enum's own stated
purpose, and mirrors Engram's identical `provisioner.py::_safe_rename`
pattern for the same underlying bug class: AV/indexer/recently-killed-child
file locks). Two new tests added: transient lock that recovers by the 3rd
attempt (`TestAtomicRenameTransientLock`), and a persistent lock that
exhausts all 5 attempts and returns `RETRYABLE_FAILURE` with staging
cleanup verified (`TestAtomicRenamePersistentLock`).

### B. ag.opus's structural placement audit

Executive summary verdict: **the 2026-08-19 Engram/peerhub split and the
2026-09-20 history migration are clean — no file belongs in the wrong
repo.** Per-item disposition, independently re-verified by the terminal:

| Item | ag.opus verdict | Terminal verification | Disposition |
|---|---|---|---|
| `peerhub/tests/test_event_dataclass_validation.py` misplaced | move → `tests/unit/` | Confirmed: pure dataclass validation, no I/O/fixtures, `tests/unit/` has an established flat-file convention (10 existing examples) | **Moved** |
| `peerhub/tests/test_sqlite_events.py` misplaced | move → `tests/integration/persistence/` | Confirmed: exercises a real `SqliteStateStore`; `tests/integration/persistence/` already exists with the matching test style | **Moved** |
| Engram `_sys/common/{assets,scripts}/` stray empty dirs | delete if empty | **False positive** — these are deliberately created by `provisioner.py::deploy()`'s folder-structure step (`_sys/core/provisioner.py:1399-1400`) every run; not git-tracked, nothing to "clean up", would just be recreated | No action |
| peerhub missing `CONVENTION.md` | create | Confirmed: only an archived pre-split copy exists under `docs/history/from-engram-repo/` | **Created** (`peerhub/CONVENTION.md`, grounded in this repo's actual observed patterns, not copied from Engram's) |
| No cross-repo file moves needed | — | Agreed, nothing found | No action |
| No problematic duplication | — | Agreed (the one apparent duplicate — the archived pre-split `CONVENTION.md` — is intentionally archived history, correctly distinct from the live one) | No action |
| Engram `dist/` "committed" release zips (git bloat concern) | worth 2nd opinion | **False positive** — `dist/` is gitignored (`.gitignore: /dist/`); local build output only, never committed | No action |
| `docs/reviews/` vs `docs/history/reviews/` boundary | worth 2nd opinion | **Already documented and intentional** — `docs/reviews/README.md` §2 explicitly states the active/archived split and the design doc (`peerhub-holistic-renewal-RATIFIED...2026-09-13.md` §5.3) that established it | No action |
| `tests/fakes.py` at `tests/` root, "consolidate?" | worth 2nd opinion | **Correctly placed** — imported via `from tests.fakes import ...` from ~20+ files across both `tests/unit/` and `tests/integration/`; moving it into either subtree would be the actual violation | No action |
| peerhub `tools/phase0_fixture_runner/` (3077 files under `tools/` vs `tests/`) | worth 2nd opinion | Not independently resolved this pass — moving ~3000 files without understanding every consumer risks silent breakage; flagged for a dedicated, narrowly-scoped follow-up rather than acted on blind | **Deferred** — no action taken |
| peerhub design docs cross-linking into Engram | worth 2nd opinion | Not acted on — cross-repo doc links are a real but low-urgency nice-to-have, not a placement defect | **Deferred** — no action taken |

## Verification

- `pytest tests/` (peerhub, full suite, post-fix + post-move): **1775
  passed, 5 skipped (pre-existing, environment-specific), 15 deselected
  (slow/e2e), 0 failed**.
- `pyright` (peerhub, full package, strict mode): **0 errors, 0 warnings**.
- Engram: no code changes made (both flagged items were false positives);
  Engram's own suite independently re-run and confirmed green (505
  passed/3 skipped/6 xfailed) as part of verifying the prior 12 commits,
  not because this audit touched Engram.

## Open item carried forward (not this audit's to close)

The v3.3.7 "Dual cc/cx Peer Sign-off" claim remains unverified. If it
matters for the record, the fix is process, not code: any future
peer-signed release note should point at a real `docs/reviews/` entry or
a hub.py `ask_history.jsonl` line, the same way this document does for
today's work — not a bare claim in the release body.
