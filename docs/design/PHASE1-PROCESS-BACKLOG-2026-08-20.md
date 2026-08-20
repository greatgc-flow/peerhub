# Phase 1 Process Backlog — Errors, Quirks & Findings for Pre-Ratification Review

> **STATUS: LIVE, updated continuously through Round 5+. Review before final ratification.**

This document tracks process-level defects, quirks, and empirical findings discovered while producing the Phase 1 design documents, as distinct from the design content itself (which lives in the `PHASE1-*` docs and gets debated by ag/cx directly). The user asked for this to be reviewed alongside the design docs before casting a final ratification vote, so nothing gets lost.

---

## 1. Real defects found and fixed during production (not design disagreements — these are quality-control catches)

| # | Found in | What was wrong | How caught | Status |
|---|---|---|---|---|
| 1 | `PHASE1-CAPABILITY-CROSSWALK-CORE` (Round 3) | ag labeled several consumer-file citations "Empirically Measured" that don't exist on disk (`msg.py`, `test_hub_ask.py`, `test_hub_ask_contract.py`, `test_hub_broker.py`, `test_hub_mailbox.py`, `test_snapshot_collector.py`) | cx's round-4 review (real `ls`/`find`), independently re-confirmed by terminal | **Fixed** in Round 5 item 1 (real pasted grep evidence required for every claim from then on) |
| 2 | `PHASE1-CAPABILITY-CROSSWALK-CLI` (Round 5 item 1 rework) | ag checked `P:\_sys` (rolled back that same night to an unrelated historical commit) instead of `P:\workspace\Engram` (this project's actual fixed reference snapshot) and wrongly concluded `_sys/cli/peerhub.bat` doesn't exist, dropping the file count from 39 to 38 | Terminal directly verified both paths with `ls` | **Fixed** — row restored, 39/39 confirmed. **Standing instruction now given to ag for all future tasks: always use `P:\workspace\Engram`, never `P:\_sys`, as the Engram reference.** |
| 3 | cx's own round-4 critique | Claimed `_sys/ai/protocol.json` was corrupted with 3 literal U+0007 (BEL) control characters | Terminal did a direct byte scan of the real file — 0 matches, claim is false | **cx was wrong here** — noted for calibration, not acted on further (harmless false claim, no file was actually touched) |
| 4 | `PHASE1-PROMOTION-SCHEMA-V1` (Round 5 item 6, first draft) | Worked examples used entirely invented entities (`fs-adapter`, `git-adapter`, `memory-adapter`, `cap.fs.write_file.utf8`) that don't exist anywhere in this project, instead of the real claude/codex/agy adapters and the real 90-action parity ledger | Terminal grepped the new doc for real project terms (init-session, thread-new, credit-consume) — none present, only the invented fs/git namespace | **Fixed** — re-grounded in real adapters/actions with traceable links back to the parity ledger and manifest docs |
| 5 | `PHASE1-MANIFEST-SCHEMA-V2` §6.3 (agy worked example), propagated into `PHASE1-PROMOTION-SCHEMA-V1`'s UNAVAILABLE example | Still specified `transport: PTY` and `builtin:pty-agy-v1` for agy, directly contradicting the already-committed `PHASE1-TEST-TAXONOMY-V3` (Round 3), which explicitly abandoned the PTY-required premise for ag based on real probe evidence (`PTY-BUFFERING-PROBE-2026-08-03.md`) | Terminal cross-checked the two committed documents against each other while reviewing item 6 | **Fixed** (commit `ae22d99`) — agy now defaults to STDIO/builtin:json-agy-v1 with stdin:DEVNULL; PTY kept as opt-in enum value only; promotion schema's UNAVAILABLE example re-grounded in a real plausible scenario (executable absent from PATH) |

## 2. Operational/harness quirks observed (not design defects, but worth remembering)

- **"Killed" background dispatches can still have written real, complete work** before the kill landed — confirmed multiple times this session (e.g. the item-5 batch-1 lease-fix dispatch, the batch-5 parity-ledger dispatch). Always `git status`/read the target file before assuming a killed dispatch produced nothing and redoing the work from scratch.
- **Oversized-ask detection** in `hub.py` counts every line starting with `-`, `*`, `+`, or a numbered list marker across the *entire* prompt text, not just an intentional "items" section — bulleted context sections count too. Write dispatch prompts in flowing prose when possible to avoid tripping this.
- Very large single-shot tasks (e.g. "produce all 90 parity ledger rows in one pass") are prone to the dispatch spending its whole budget writing non-converging helper scripts instead of the actual deliverable. Splitting into fixed, explicit batches (e.g. 18 actions at a time) worked reliably every time it was tried.
- ag will sometimes reconstruct content from a paraphrased summary instead of using verbatim source text if a fresh session is given only a summary — always paste full original text (or point at an already-committed file) rather than re-describing it from memory when asking for a faithful reproduction.

## 3. Real empirical findings worth keeping regardless of Phase 1's outcome

- **hub.py idempotency audit** (Round 5 item 5, all 90 actions): a large fraction of hub.py's actions were previously assumed idempotent in earlier drafts but are not — see the five `PHASE1-PARITY-LEDGER-BATCH*` documents for the full per-action verdict with line evidence. This is genuinely useful knowledge about the legacy system independent of whether Phase 1 proceeds, since any future caller of `hub.py` directly (not just peerhub) should know which actions are safe to retry blindly and which are not.
- **Real race condition found**: `thread-new` (and, per Round 5 item 5 batch 5, likely `proposal-add`, `thread-react`, `proposal-vote`, `lesson-sweep`, `thread-append` too) has an unlocked check-then-act pattern (`path.exists()` followed by an unlocked append) that can produce duplicate records under concurrent callers. This is a real bug in the *legacy* `hub.py`, independent of the peerhub migration — worth deciding separately whether to backport a fix to `hub.py` itself or just carry the correct locked behavior into peerhub's replacement.
- **Real ACL/transport findings** (Round 5 item 3): the originally-designed admission rule ("directory must deny unprivileged writes") would have rejected every real CLI installation on this machine, since `Authenticated Users:(M)` is the Windows default on portable/secondary NTFS volumes. The corrected rule (deny `Everyone`/`ANONYMOUS LOGON`/`Guests` write access specifically) is the one that's actually meaningful and achievable — this is a generally-applicable lesson for any future Windows-targeted admission/trust design, not just this project.

## 4. Open items as of this writing (not yet resolved)

- Round 5 items 6 (promotion schema) and 7 (shim lifecycle hardening) still in progress/pending as of this backlog entry.
- Whether to backport the real `thread-new`-class race-condition fixes into legacy `hub.py` itself (currently frozen/not-to-be-touched per separate user instruction) is an open question not yet raised with the user — flagging here rather than deciding unilaterally.

---

*This document is appended to, not rewritten, as new findings surface. Each entry should be added at the time of discovery, not reconstructed from memory later.*
