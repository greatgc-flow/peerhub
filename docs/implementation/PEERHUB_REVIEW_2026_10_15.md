# PeerHub cross-peer review — 2026-10-15

## Scope and evidence

Reviewed `main` at `7a3a4b96eeb4cc3a7182b4ea466946836930a2d1` (released as 0.16.0). The independent reviewers were `cc.effort`, `cx.effort`, and `ag.effort`; the initial `ag.deepthink` call ended uncertain after a 240-second silence, so the AG review was retried in a fresh stream. All delivered reviews were read-only. The synthesis below independently inspected the cited code and used small, isolated reproductions for store selection, terminal rendering, and repeated `ClaimStore` initialization. It does not claim a fresh full test-suite or live-provider pass.

The reviewers agree that the Core/Bridge separation, persisted certainty ledger, replay identity checks, bounded subprocess handling, and read-only inspection safeguards are substantial strengths. They did not find evidence that ordinary session reuse is broadly broken. Their highest-value concerns are operational truthfulness, cancellation behavior, access scope in embedded MCP, and avoidable local database work.

## Prioritized findings

| Priority | Finding and evidence | Assessment / next design decision |
| --- | --- | --- |
| P1 | Explicit `--db` loses to `PEERHUB_DB` in [`store_select.py`](../../peerhub/cli/store_select.py) around line 40. `select_store('requested.db', env={'PEERHUB_DB':'other.db'}, discover=False)` selected `other.db`. README's explicit-path promise conflicts with this precedence. | Unsafe for mutating commands. Define explicit CLI path as highest precedence, then update tests that currently encode environment precedence. Verify with an end-to-end command writing only the requested store. |
| P1/P2 | A cancel can arrive after the runtime has spawned a process but before Bridge commits `STARTED` ([`adapters/base.py`](../../peerhub/extensions/adapters/base.py) around 1105; [`bridge.py`](../../peerhub/extensions/bridge.py) around 1245 and 1615). The control handler may record a write-once `nothing_running` while the process continues. CC identified the code path; the race was **not reproduced**. | Treat as a high-consequence concurrency hypothesis. Build a deterministic blocked-yield test before changing the state machine. Cross-process cancellation has a related active-process ownership limit that needs an explicit contract. |
| P1/P2, host-dependent | Embedded MCP reads in [`mcp.py`](../../peerhub/extensions/mcp.py) around 358 and 448 do not apply session/`allowed_streams` authorization; append authorization around 329 does not constrain control record kinds. | Confirm the MCP host trust model. If scoped sessions promise read/write isolation, enforce it on tools and resources and restrict privileged record kinds. The CLI path does not construct this MCP server, so this is not evidence of a CLI data leak. |
| P2 | [`ClaimStore.__init__`](../../peerhub/extensions/bridge_claims.py) around 76–92 drops and recreates four triggers inside `BEGIN IMMEDIATE`; [`ask.py`](../../peerhub/extensions/ask.py) around 149 constructs it per ask. In a disposable database, three constructions changed SQLite `schema_version` **6 → 14 → 22**. | Confirmed non-AI work and potential schema-lock/cache churn. Move DDL to initialization/migration or skip it when the expected trigger set is already installed; preserve future-schema refusal and legacy-trigger repair. Benchmark contention before claiming an end-to-end speedup. |
| P2 | `diag integrity` compares observations and rollups using separate autocommit reads ([`diag.py`](../../peerhub/extensions/diag.py) around 91). A concurrent atomic append can be observed across two snapshots and produce a false corruption result. | Use one read transaction for the complete audit and test the interleaving. |
| P2 | `diag health --stream` derives health from Core stream data and omits unresolved Bridge deliveries ([`diag.py`](../../peerhub/extensions/diag.py) around 399). A `STARTED` delivery can block the next ask while health still says `OK`. | Add a separate operational delivery-health projection with blocking identity/certainty. Keep Core stream state and Bridge delivery state distinct. |
| P2 | Human list/show rendering prints stored values with terminal controls intact ([`app.py`](../../peerhub/cli/app.py) around 601). A value containing ESC and a forged `next_cursor` line was reproduced in isolated output. | Sanitize control characters and line breaks in human rendering; retain exact values in JSON. |
| P2 | Monitor computes sleep before skipping missed slots ([`monitor.py`](../../peerhub/cli/monitor.py) around 192). CX's simulated 60-second schedule with 75-second probes started at 0, 75, 150 seconds. | Decide whether the contract is fixed-rate with skipped slots or immediate catch-up. The current behavior needs accurate documentation or a cadence fix and corrected tests. Skipping to 0, 120, 240 seconds is the safer monitoring default. |
| P2/P3 | Single-stream health delegates to a full `render(["streams"])` scan ([`diag.py`](../../peerhub/extensions/diag.py) around 170 and 399); work and skill listings make N+1 SQLite connections ([`work.py`](../../peerhub/extensions/work.py) around 403; [`skills.py`](../../peerhub/extensions/skills.py) around 600 and 636). | Confirmed query shapes; growth impact has not been measured. Use targeted stream queries and single-snapshot list projections, then benchmark with representative record/item counts. The actual command is `peerhub diag health --stream`, not `peerhub stream health`. |
| P3 | Windows `taskkill` may report a missing PID when the parent exits during termination ([`process_tree.py`](../../peerhub/extensions/process_tree.py) around 14; [`adapters/process.py`](../../peerhub/extensions/adapters/process.py) around 242). | Improve result classification and test the race. **Do not** equate a vanished parent PID with confirmed termination of every descendant; preserving process-tree certainty matters. |
| P3 | Vendor session ID validation permits a leading hyphen ([`adapters/base.py`](../../peerhub/extensions/adapters/base.py) around 33), yet the CX resume argv uses the ID positionally around line 689. | Reject option-shaped IDs in both adapter and Bridge validation; test `--last` and `-x` (the existing semicolon case does not cover this). |

## Design and recovery issues to retain

- A confirmed kill after `STARTED` still leaves an uncertain delivery that blocks later asks. This is deliberate conservative behavior: termination does not prove that external effects did not occur. Add an explicit, auditable `stream resolve` workflow instead of automatically marking such deliveries acknowledged.
- Session inspection exposes stored state/resumability but not enough redacted binding and current eligibility information to choose an exact handle confidently. Define eligibility as a read-only projection that distinguishes stored resumability from current binding/age constraints.
- Catch-up prompts include record bodies in a textual envelope. A forged record header inside a lower-trust body could affect model behavior. This is a prompt-boundary design risk, not a demonstrated code execution issue; test boundary encoding with hostile bodies before selecting a format.
- Local live-gate release evidence is checked for hash and passing results but is not independently signed. This is optional provenance hardening, contingent on the release-editor threat model; it is not evidence that the published release bypassed gates.
- The 20 ms subprocess wait polling loop is a measurable 50-wakeups-per-second design, but no meaningful CPU or end-to-end latency impact was established. Profile before changing its concurrency behavior.

## CLI, monitoring, and latency snapshot

The current CLI separates profile/peer/stream/session/diagnostic commands and has explicit read-only inspection protections. The gaps above show that its operational views are not yet fully congruent: Core health does not show Bridge blockage; session listings do not explain effective resume eligibility; and explicit database selection has surprising precedence. These should be resolved as contract changes with command-level examples rather than renaming the whole CLI.

On this machine and the existing small store (6 peers, 270 streams, 577 records, 427 observations, 278 bridge-session-history rows), three read-only process launches per command produced these median wall times: `profile list` 353 ms, `peer list` 332 ms, `stream list` 319 ms, `session list` 396 ms, `diag --json` 427 ms. These include CLI startup and are **not** scale benchmarks. GitHub CI run 38648798626 had eight successful jobs; the reported Ubuntu job durations were about 6.0–6.7 minutes and Windows 21.7–32.3 minutes. Quota diagnostics were stale/unavailable, so no provider-quota or live-model speed conclusion is supported. The repeated ClaimStore DDL and broad/N+1 reads are concrete non-AI latency candidates; provider execution latency was not re-measured in this review.

## Development file line inventory

The companion [CSV](PEERHUB_DEVELOPMENT_LOC_2026_10_15.csv) contains every counted path, category, physical lines, nonblank lines, and bytes. It counts 286 Git-tracked UTF-8 development files under `peerhub/`, `tests/`, `tools/`, `.github/workflows/`, and `docs/m1_spec/tools/`, plus root `pyproject.toml` and `pyrightconfig.json`. Included suffixes are `.py`, `.pyi`, `.json`, `.yml`, `.yaml`, `.toml`, `.ps1`, and `.sh`. Markdown/prose, generated/untracked files, and blank-line-free estimates are outside scope. Physical lines use Python `splitlines()`; nonblank lines have non-whitespace content.

| Category | Files | Physical lines | Nonblank lines |
| --- | ---: | ---: | ---: |
| Product | 76 | 18,636 | 16,304 |
| Tests | 189 | 34,241 | 28,410 |
| Development tools | 16 | 2,078 | 1,849 |
| CI | 3 | 352 | 326 |
| Configuration | 2 | 85 | 77 |
| **Total** | **286** | **55,392** | **46,966** |

The largest product files are `peerhub/extensions/bridge.py` (1,838), `peerhub/extensions/adapters/base.py` (1,209), `peerhub/extensions/quota_probes.py` (1,048), `peerhub/extensions/skills.py` (925), and `peerhub/cli/app.py` (861). File size alone is not a defect; these are useful targets for focused review and decomposition only when a behavioral change justifies it.

## Recommended sequence

1. Specify and test explicit database selection; resolve the cancellation race and MCP authorization contract with deterministic failing tests.
2. Correct the integrity snapshot, Bridge-aware health, human terminal rendering, and monitoring schedule contracts.
3. Remove per-ask DDL, target single-stream health queries, and collapse N+1 listing reads; measure representative cold/warm and concurrent workloads before assigning speedup claims.
4. Design explicit uncertain-delivery resolution and explain session eligibility in read-only inspection; then address option-shaped vendor IDs and the remaining lower-priority hardening items.

No product code was changed by this review.
