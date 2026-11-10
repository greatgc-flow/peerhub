# PeerHub health and session continuity check — 2026-10-14

## Baseline

Checkout: `feat/session-reuse`, initially `12129517`, clean worktree.
Session reuse PR1–PR3 are committed locally; PR4/PR5 are pending.
The monitoring-v2 shared snapshot/policy baseline required by PR4 is absent.

The installed shared-environment command outside the checkout imports
`D:/PkgDev/_sys/env/venv/Lib/site-packages/peerhub` at version 0.15.2.
Its ask help still exposes `--resume` and lacks `--fresh`/`--session`.
Inside the checkout Python imports current source instead. Installed package
metadata alone therefore does not identify the executing source. Keep the
shared Engram environment intact; validate and install this checkout into the
dedicated development environment before using it from another directory.

The dedicated venv previously advertised an editable checkout but imported a
namespace package (`peerhub.__file__` was null) from the parent workspace.
Reinstalling with setuptools' `editable_mode=compat` and no dependency changes
corrected that directory/package-name collision. From `D:/PkgDev/workspace`,
the following verified development command now imports this checkout, reports
0.16.0, exposes fresh/exact selection, and reads the live stream as health OK:

```powershell
$env:PEERHUB_DB = 'D:/PkgDev/workspace/peerhub/.peerhub/core.db'
& 'D:/PkgDev/_sys/data/temp/ph-dev-venv/Scripts/peerhub.exe' ask cx 'Your next prompt' `
    --profile cx.effort --stream your-conversation `
    --workspace D:/PkgDev/workspace/peerhub
```

Keep the same stream, peer, profile, model, effort, workspace and write mode
between turns for compatible automatic reuse. Changing the stream per turn
prevents native continuity; reserve new streams for independent work or explicit
recovery after uncertainty. Copy the full returned handle for `--session`.
This executable follows the editable branch; it is not a published release.
The pre-existing shared-environment monitor remains running from the parent
workspace (PID 22336 at inspection), using installed 0.15.2. Its environment
was not upgraded underneath the live process.

## Live evidence

Explicit owner-requested one-shot pings used each peer's `.effort` profile,
separate streams, 240-second process deadlines and 65,536-byte capture limits.
All returned PONG with TERMINAL certainty and a fresh generation-1 handle.
Persisted activity durations: cc 5.153s, cx 7.525s, ag 10.018s. The activity
timer starts after bootstrap/Bridge initialization, so it is not whole-call
latency. Shell startup/import/initialization/cleanup time is additional; these are single samples,
not latency distributions. Input billing tokens were cc 24,092, cx 15,635,
ag 12,042 even for a short prompt. Billing is not context occupancy.

The following independent read-only consultations reused each ping's exact
handle and generation, with mode `resumed` and no fallback. This verifies
native continuity for all three peers on this checkout, not the old installed
command. Raw output stays in ignored `.peerhub/work/health-20261014/`.

At measurement time the selected store had 480 Records, 260 deliveries,
1,494 evidence rows and 330 observations. Eight read-only diagnostic renders
took 45.64–92.81ms and returned OK. A fake-runtime fresh/resumed pair took
983.6ms including cold imports and initialization; 1,213 SQLite execute calls
accounted for 521ms under cProfile. These measurements show local overhead,
but do not establish a multi-second database bottleneck in the current store.

## Agreed investigation boundary

Independent cc/cx/ag audits identify missing delivery/evidence lookup indexes
and an execution-evidence query that fetches all peer history before filtering
in Python. Registry initialization also repeatedly scans generation evidence.
Diagnostic observation/stream reads and integrity checks scale with history.
Pipe-drain hangs are a hypothesis requiring reproduction, not a confirmed live
failure. Do not weaken certainty, fencing, integrity or continuity contracts
to improve a timing number.

First proposed implementation slice: index existing extension-owned lookup
paths and filter execution evidence in SQL. Preserve response content, attempt
ordering, evidence ordering, peer filtering and transaction behavior. Verify
using large unrelated-history fixtures and query plans. Counter initialization,
process cleanup and monitoring prerequisites require separate focused changes.

The second proposed slice gates historical counter/scope seeding on the
pre-DDL presence of both projection tables, or legacy conversion. PR2 creates
and seeds these tables in one transaction; ordinary writers maintain them.
An initialized reopen does not repair individually missing rows by scanning
history. A delivery-only unstarted scope remains without a projection until
allocation, when its known-zero counts start at coverage sequence zero; a late
unattributable confirmation still creates incomplete counts. Missing tables
retain the existing conservative historical seeding path. All upgrade, DDL,
index installation and rollback hooks remain in the existing transaction.

No new Core primitive, provider telemetry guess, session-only monitoring shim,
release, shared-environment upgrade or external publication is part of this
slice. Subsequent session inspection follows the canonical PR4 prerequisites.

## Query benchmark

Windows 11 build 26300, Python 3.14.8, local scratch SQLite store, 100,000
unrelated same-peer evidence rows, 30 warmed samples. The original query/filter
and indexed implementation returned identical deliveries and events:

| Execution-evidence lookup | Median | p95 |
| --- | ---: | ---: |
| Original lifetime scan | 285.22ms | 325.77ms |
| Indexed record/peer selection | 1.97ms | 2.90ms |

This measures only the evidence lookup on a synthetic growing store, not AI
inference or whole-ask latency. The regression gate uses VM instruction counts
rather than machine-dependent timing. Benchmark code/output are kept with the
ignored health-check artifacts.

## Validation and remaining work

- Final deterministic suite: 2,329 passed, one skipped in 432.60s
  (`pytest -q -p no:cacheprovider -n 4`). The skip is the existing Windows
  symlink-privilege condition, not a session/provider test.
- Focused delivery/registry suite: 52 passed.
- Pyright: zero errors/warnings. Command inventory and diff checks passed.
- cx independently approved both fixes with no P1/P2 findings. cc/ag/cx
  consulted before implementation; the second fix's initialization invariant
  was approved by all three. Reviews did not independently execute tests.
- Live scopes ended with generation 1 throughout: cc two reuses, cx five,
  ag three, each following one fresh call. No health-check delivery remains
  uncertain or unfinished. Runtime telemetry remains explicitly unknown.

Core contracts, certainty, request replay and retained-session selection were
not redesigned. PR4/PR5 inspection/monitoring are still unimplemented; first
provide the canonical shared monitoring-v2 snapshot/policy baseline. Larger
diagnostic history scans, schema/trigger setup cost, output-limit semantics and
the unreproduced surviving-pipe issue remain separately scoped investigations.
The active shared 0.15.2 monitor requires an intentional later executable
transition; the dedicated development executable is ready now. No push, PR,
merge or release was performed by this health-check change.
