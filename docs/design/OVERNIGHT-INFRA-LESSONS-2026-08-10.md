# Overnight infra lessons — 2026-08-09/10

Status: incident record, not a design doc. Read this before re-diagnosing
any of the symptoms below from scratch — all four were already root-caused
once during an overnight peerhub session (2026-08-09 22:00 → 2026-08-10
16:00). These are `P:\`/hub.py-layer infrastructure problems, not peerhub
code problems, but they repeatedly blocked peerhub work, so they're
recorded here where peerhub contributors will actually see them.

## Incident 1 — D: drive rename broke hub.py's codex dispatch

**Symptom:** `hub.py ask --to cx.*` fails with
`OSError: [Errno 22] Invalid argument` in `_spawn_process`, followed by a
node `MODULE_NOT_FOUND` on a stale path like
`D:\Engram\node_modules\@openai\codex\bin\codex.js`, and/or a cmd.exe
error like `'Peerhub\PortableDev'은(는) 내부 또는 외부 명령...`.

**Root cause:** the portable environment's underlying `D:` folder got
renamed/restructured mid-session (`D:\Engram` → `D:\Engram&Peerhub\
PortableDev (v2.1)\`, note the new folder name contains a literal `&`).
`P:\_sys\core\hub.py`'s `_resolve_invoke_cli()` called
`Path(__file__).resolve()` on its relative-path branch, which follows the
`P:` `subst` mapping to the real `D:` target — this should have re-resolved
correctly on its own, but something about the character/mechanics of a
subst target containing `&` produced a stale/garbled path instead. A
second, independent risk was also present: any code that later runs a
resolved absolute path through `cmd /c` or `shell=True` without quoting
will have that literal `&` parsed as a cmd.exe command separator.

**Fix (already applied, `_sys/core/hub.py:8221`):** removed `.resolve()`
from `_resolve_invoke_cli()`'s relative-path branch — `portable_root` now
stays on the `P:\` drive letter as invoked, never touching the real `D:`
path at all. This sidesteps the whole class of problem (both the stale
resolution and the unquoted-`&` risk) and is arguably more correct for a
portable environment anyway: treat the mapped drive letter as canonical,
don't chase the real target.

## Incident 2 — cx's write sandbox silently read-only (DPAPI)

**Symptom:** `cx` (codex) launches and responds fine to `hub.py ask`, but
any file-write attempt fails. Two different error shapes were seen
depending on approach: `shell_command` crashes before PowerShell starts
with `CryptUnprotectData failed: 2148073483` (Windows `0x8009000B
NTE_BAD_KEY_STATE`); `apply_patch` / direct file-write instead just
reports `"filesystem is read-only in this session"` with no crash.

**What did NOT fix it:** running `codex` once interactively under an
elevated (Administrator) terminal — the natural-seeming fix, since
`0x8009000B` is a classic "DPAPI ciphertext bound to a different
session/context" error and `[windows].sandbox = "elevated"` in
`_sys\codex\config\config.toml` implied elevation was the missing piece.
No UAC prompt ever appeared, and `.sandbox-secrets\sandbox_users.json`'s
timestamp never moved — the hypothesis was reasonable but wrong (or at
least not sufficient) in practice. A "full access" option selected
inside one interactive `codex` session also didn't help — it doesn't
persist, and doesn't apply to `hub.py ask`'s separately-spawned
processes. More fundamentally: `elevated` sandbox mode requires
interactive UAC consent by nature, which is structurally incompatible
with `hub.py ask`'s non-interactive background dispatch model regardless
of whether the DPAPI issue is fixed.

**Actual fix — two config keys, in `_sys\codex\config\config.toml`:**
```toml
sandbox_mode = "workspace-write"   # top-level key; was ENTIRELY ABSENT
                                     # before — codex was silently
                                     # defaulting to its safest built-in
                                     # (effectively read-only)

[windows]
sandbox = "unelevated"              # was "elevated"; valid values are
                                     # ONLY "elevated" or "unelevated" —
                                     # "workspace-write" here is a config
                                     # error ("unknown variant"), that's a
                                     # DIFFERENT key than sandbox_mode
```
Both keys were required together — `unelevated` alone still left codex
defaulting to read-only (no `sandbox_mode` set); `sandbox_mode =
"workspace-write"` alone failed to load
(`unknown variant 'workspace-write', expected 'elevated' or 'unelevated'`
is the value for `[windows].sandbox`, a different key with a different
enum). Verified via a real `hub.py ask` file-write dispatch, not just
codex's own self-report — the file existed on disk with exact expected
content afterward.

## Incident 3 — "oversized ask" silently truncates a dispatch to a checkpoint

**Symptom:** a `hub.py ask` dispatch exits cleanly (exit 0,
`ask_guards` status `"clean"`), but `changed_files: []` — no files were
actually written despite a substantial task being requested. The peer's
reply is short and reads like a progress checkpoint
(`"PROGRESS 1: ... | next=..."`) rather than a completed task report.

**Root cause:** `hub.py` logs
`[HUB:WARN] {peer}: oversized ask detected (task_items=N > limit=5) -
injecting an incremental-progress instruction before dispatch` whenever a
dispatch prompt contains more than 5 list items — and this counts **every
bulleted (`-`) or numbered (`1.`) list item in the entire prompt summed
together**, not just a main task list. A prompt with a 6-item reference
list plus a 4-item action list (10 total) trips it exactly the same as an
11-item single list would. When this fires, the injected instruction
appears to redirect the peer toward reporting incremental progress rather
than completing the full task — the peer isn't malfunctioning, the
dispatch itself got silently reshaped.

A related, more informative warning also exists and is worth watching
for: `[HUB:WARN] {peer}: suspiciously short reply for a substantial ask
(reply_chars=N < warning_chars=300; task_items=M > limit=5) - automatic
retry suppressed; verify completeness`. When present, treat it as
near-certain confirmation the task did not complete.

**How to avoid it:** keep dispatch prompts to 5 or fewer total list items
(bullets and numbers combined) across the whole prompt. For anything
needing more structure, either write it as prose paragraphs with no list
markers, point at an external reference file instead of inlining a list,
or split into multiple smaller sequential dispatches. If a dispatch keeps
tripping this despite simplification, it may be faster to do the task
directly than to keep fighting the heuristic.

**After ANY dispatch**, check `.ai\ask_guards\{ask_id}.json`'s
`changed_files` (or `git status`) before trusting a "clean" exit —
a clean exit code alone does not mean the task was actually completed.

## Incident 4 — one peer absorbing all write load can blow past its EXH ceiling

**Symptom:** `diag.py` shows a peer's EXH pace metric climbing past an
explicit ceiling (this environment's standing rule: ag/cx EXH ≤ 4.0×) —
observed peak 4.07× on `ag`'s G-pool.

**Root cause:** this environment's load-distribution assumption is that
write-capable work spreads across both `ag` and `cx`. When `cx` was
write-blocked all night (Incident 2), every implementation dispatch
funneled through `ag` alone with no counterbalance, and its usage pace
climbed past the ceiling after roughly 8 substantial dispatches in one
stretch.

**Fix:** none needed in code — this is a process signal, not a bug. When
one write-capable peer is unavailable, either pace dispatches to the
remaining one more conservatively, or route non-write-capable work
(design/critique/read-only analysis) to profiles on a *different* quota
pool if available (e.g. `ag.opus`/`ag.gptoss` use a separate 3P-pool from
`ag.effort`/`ag.deepthink`'s G-pool — using them doesn't touch a G-pool
that's already near its ceiling). Treat ≥4.0× as a hard stop for new
dispatches to that peer, not just a number to note.

## Incident 5 — general system resource contention under heavy concurrent dispatch

**Symptom:** multiple distinct failure shapes appeared during stretches
with 3+ concurrent `hub.py ask` dispatches running: a raw `MemoryError`
with no other context from `hub.py` itself; a PowerShell diagnostic
command failing with `Thread failed to start`; `ag` dispatch output
saturated with Cygwin/Git-Bash `dofork: child -1` and `cygheap read copy
failed` noise (usually harmless on its own, see the separate
`reference_ag_cygwin_dofork_noise_2026_08_10` memory note); and one
confirmed Windows Application-Error crash of `claude.exe` itself
(`Exception code: 0xc0000409`, a stack-corruption fault) at 06:19:53,
found via `Get-WinEvent -FilterHashtable @{LogName='Application';
ProviderName='Application Error'}`.

**Root cause:** this machine was running 25-30+ concurrent
node/python/codex/bash processes during peak overnight multi-peer
dispatch stretches, with free physical memory observed as low as
~4.7GB/15.8GB. All of the above are consistent symptoms of the same
underlying resource exhaustion, not separate bugs. The one `claude.exe`
crash was NOT caused by any specific config change (verified: it
happened at 06:19:53, hours before the cx sandbox `config.toml` change
made later that afternoon — timing rules out that specific hypothesis).
The 3P-pool (`ag.opus`/`ag.gptoss`) also climbed to 3.28x EXH during a
3-way-parallel dispatch stretch, showing even the "spare" pool isn't
immune to being loaded up quickly when used concurrently.

**Fix:** none needed in code — this is a capacity signal, not a bug.
Before dispatching 3+ things in parallel, check free memory (`Get-CimInstance
Win32_OperatingSystem | Select-Object FreePhysicalMemory`); if it's
trending low or a dispatch fails with `MemoryError`/`Thread failed to
start`, stagger dispatches instead of firing them concurrently and let
in-flight ones finish first.

## Known-open, not fixed here

Two pre-existing test failures were found (independently reproduced via
git-stash bisection, confirmed unrelated to any of the work done during
this session):
- `tests/static/test_client_architecture.py::test_client_never_imports_persistence`
  — hardcodes a stale `D:\PortableDev (v2.1)\peerhub\peerhub\client.py`
  path missing the `Engram&Peerhub` segment. This is itself another
  Incident-1-shaped casualty of the same drive rename, just in test code
  instead of hub.py.
- `tests/unit/tools/test_generate_manifest.py::test_generator_runs_and_produces_valid_manifest`
  — asserts a stale expected content hash.

## Summary for future readers

- Symptom involves `D:\Engram\...` (bare, no `Engram&Peerhub` segment) or
  a cmd.exe error splitting on `&` → Incident 1, already fixed in
  `hub.py`. If it recurs, re-check for `.resolve()` calls that chase a
  `subst` target.
- Symptom involves `CryptUnprotectData`, "read-only sandbox", or cx
  failing to write → check `_sys\codex\config\config.toml` has BOTH
  `sandbox_mode = "workspace-write"` and `[windows] sandbox =
  "unelevated"`. Don't waste time on interactive-elevation fixes — they
  don't work for `hub.py ask`'s non-interactive model.
- A dispatch exits clean but nothing changed → check the prompt's total
  list-item count (bullets + numbers) before assuming the peer failed;
  keep dispatch prompts to ≤5 total list items.
- A peer's EXH is climbing fast with no counterpart peer sharing load →
  that's Incident 4's shape; consider a different quota pool (opus/gptoss
  vs effort/deepthink for `ag`) before just pushing through the ceiling.
- A dispatch fails with `MemoryError`, `Thread failed to start`, or a
  session/app crash during heavy concurrent dispatching → check free
  memory before assuming a code bug; that's Incident 5's shape. Verify
  timing against any recent config change before blaming it — a
  same-night `claude.exe` crash turned out to predate a suspected config
  change by 8 hours once actually checked against `Get-WinEvent`.
