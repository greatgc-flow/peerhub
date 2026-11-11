# PeerHub Development Status

> **Status Authority:** This document (`docs/STATUS.md`) is the single authoritative source of truth for current release status, published version history, and the remaining development queue. Navigation hubs (`docs/index.md`, `docs/design/README.md`) and maintainer guidance (`docs/HANDOFF.md`) point here.

Dates are UTC.

## Current source: v0.24.7 (published)

v0.24.7 adds the read-only `peerhub diag --plan [--json]` (per-pool spend/hold/rest/blocked from stored observations; #339), full store path and source in `diag`/`monitor` headers (#338), `tools.model_ping --model` (#341), a Python 3.11 f-string quote guard test (#342), versioned live-gate evidence names (#343) and lessons `review-loop`, `instruction-diet` plus cc allow-rule and Windows upgrade docs (#337, #344, #345). Backlog triage 2026-11-11 (unanimous: cc lead, cx, ag): #328/#329 closed as shipped in 0.24.6; #324 step 2 is next (0.24.8); #330, #323, #313, #335 stay deferred.

Published 2026-11-11 (UTC; PR #347, tag `v0.24.7`, target `b1f34968`, publish run 42935285963); G3 passed bound to `b1f34968857f` (live gate 2026-11-11, three stages; evidence `docs/implementation/live_evidence/2026-11-11-v0.24.7.json`). PyPI 0.24.7 verified 2026-11-11 in a clean virtualenv (`--version`, `diag --plan` table and JSON `store_selection`, `explain review-loop`). Reviews: cc.fable 100 (2 rounds), ag.effort 100 (3 rounds).

### v0.24.6 (published)

v0.24.6 adds the read-only `peerhub diag running [--json]` (lease-active asks from existing tables; no progress or staleness fields; a lease is not proof a process is alive) and an `ACCOUNTS` line in `peerhub monitor` (#329: plan, login state and login-token expiry per peer, with compact, `--expand` and JSON parity from one stored snapshot; only normalized fields are stored, no email, organization, id, token or identity hash; collected by `observation refresh` / monitor cycles with bounded no-model-turn probes: `claude auth status`, codex app-server `account/read`, `agy /credits`). It fixes #328 (a provider-disabled agy bucket renders `n/a`; superseded peer-level probe failures no longer show `PROBE`; `peerhub explain <lesson id>` matches the exact id) and lands the #327 items 1, 5 and 9 (guidance link to the adapter guide, `source_records` reuse of the Core row mapper, README doc map and layout). It adds the hard (integer interpreter) and top-tier (regex matcher) profile benchmarks (docs/benchmarks/profiles-hard-2026-11-10, profiles-top-2026-11-10) and their evidence on the profile cards. cc.fable switches to `claude-fable-5-2` under the frozen rule (identity ping PASS, 45/45 on the 2026-11-08 task, hard 100/100, top 115/115; slower in one run: 946 s vs 169 s), checked 2026-11-10. Plan and decision log: docs/design/plan-0.24.5-2026-11-10.md (rows "Follow-up"). Notes: see CHANGELOG [0.24.6].

Published 2026-11-10 (UTC; PR #331, tag `v0.24.6`, target `15fcd356`, publish run 42828266303: the first attempt failed only on the wall-clock test `test_sleep_called_count_minus_1_times` in release-matrix windows-latest 3.11; the failed jobs were rerun and passed, and the test is now deterministic, PR #333); G3 passed bound to `15fcd35611ef` (live gate 2026-11-10, three stages). PyPI 0.24.6 verified 2026-11-10 UTC (clean venv `pip install --no-cache-dir peerhub==0.24.6`: `peerhub --version` 0.24.6, `diag running`, `explain cc.fable` = claude-fable-5-2, ACCOUNTS line, 3P-5H `n/a (disabled)` after one refresh).

### v0.24.5 (published)

Published 2026-11-10 (UTC; PR #326, tag `v0.24.5`, target `8fd00491`, publish run 42816004629); G3 passed bound to `8fd00491632d` (live gate 2026-11-10, three stages). PyPI 0.24.5 verified 2026-11-10 UTC (clean upgrade with --no-cache-dir, `peerhub --version` 0.24.5, `peerhub explain cc.effort` prints the card)

v0.24.5 adds AI-readable profile cards (use for / avoid for / traits with evidence, bound to the binding they were checked against; `peerhub explain <profile id>`, `profile show`, `profile list`), a read-only quota utilization report (`tools/quota_report.py`; first report docs/history/quota-efficiency-2026-11-10.md), re-observed model facts (agy 1.5.4, claude 2.1.322, codex 0.167.0; all packaged profiles pinged except cc.fable, deferred to 0.24.6 because its pool was exhausted) and fixes #325 (a later intact cc `result` resolves an oversized-line gap). #323 was deferred by peer vote (no unanimity). Plan: docs/design/plan-0.24.5-2026-11-10.md (cc lead; cc.deepthink, cx.effort and ag.effort reviews, unanimous on spec v2 and on adding #325).

### v0.24.4 (published)

v0.24.4 puts pace back on the compact monitor: a `PACE` column (used% - elapsed% of the window, signed whole pp), a legend that defines it, `suggest:` ordering that prefers pools without `fast`, one human frame on non-TTY runs (was two), exact-reset-instant handling, and a guide balance recipe bounded by bytes for the packaged defaults (<=60 lines, <=5,600 B). JSON values and schemas are unchanged. Plan: docs/design/plan-0.24.4-2026-11-09.md (cx.astra, cc.fable, ag.argon: two independent audit rounds, cross-critique vote, review round). Published 2026-11-09 (UTC; PR #320, tag `v0.24.4`, target `bedf6d52`, publish run 42605365707); G3 passed bound to `bedf6d52f008`.

v0.24.3 (published) adds actionable oversized-prompt guidance with byte counts (#309) and explains the #312 claim-held symptom (caller tool timeout while the ask kept running; replay with the same `--request-id`) in guidance and a lesson (#312 root cause from the store; no parser change).

v0.24.2 separates compact monitor `low` capacity from `fast` pace, explains states in the legend, adds eligible alternative peers to tier suggestions, and defaults non-TTY monitor watch to one cycle. JSON values and schemas are unchanged (PR #310, unanimous cx+ag+cc review).

v0.24.1 is a patch on 0.24.0: cc `permission_denials` surface as `denied_actions` and an empty cc reply with denials gets `denied_empty` (provider-neutral note), plus the `cc-allow-rules` lesson (#303); #304 store-discovery fallback and fail-closed behavior are included, along with every-peer plan-aware ABSENT windows (P4). Plan: docs/design/plan-0.24.1-2026-11-08.md.

v0.24.0 unifies CLI human output (text by default, `--json` everywhere), store precedence (`--db` over `PEERHUB_DB`) and a redesigned at-a-glance monitor (pools with meaning, per-group limits, fresh-only `suggest:` line, plan-aware ABSENT windows for cx only), reworks profiles quality-first (`ag.effort` flash-high, `cx.effort` sol/high, packaged tops `cc.fable` and `cx.astra`), documents capability-matched quota balancing and concurrency caps (docs/batch.md), adds the ag recipe and denied-empty handling (#300). Plan: docs/design/plan-0.24.0-2026-11-08.md.

The latest published package is **v0.24.6** (tag `v0.24.6`, target `15fcd356`; PyPI verified 2026-11-10 UTC). The previous one is **v0.24.5** (tag `v0.24.5`, target `8fd00491`); PyPI 0.24.5 verified 2026-11-10 UTC (clean upgrade with --no-cache-dir, `peerhub --version` 0.24.5, `peerhub explain cc.effort` prints the card); G3 passed bound to `8fd00491632d`. The previous verified package is v0.24.4 (tag `v0.24.4`, target `bedf6d52`; PyPI 0.24.4 verified 2026-11-09 UTC: clean upgrade, `peerhub --version`, compact monitor PACE column, `explain pace`; G3 passed bound to `bedf6d52f008`). The installed `peerhub guide` does not automatically update when main changes.

v0.24.3 published 2026-11-09 (UTC; PR #316, tag `v0.24.3`, target `ad5d1b25`, publish run 42569582779; closes #309 and #312); completeness audit Fable/Astra 100 on every axis before release; G3 passed bound to `ad5d1b25ec37`. See [live evidence](implementation/live_evidence/2026-11-09-v0.24.3.json) and the release JUnit assets.

v0.24.2 published 2026-11-09 (UTC; PRs #310/#311/#314, tag `v0.24.2`, target `a5b61f9a`, publish run 42557525466); completeness audit Fable/Astra 100 on every axis before release; G3 passed bound to `a5b61f9a354a`. See [live evidence](implementation/live_evidence/2026-11-09-v0.24.2.json) and the release JUnit assets.

v0.24.1 published 2026-11-08 (UTC; tag `v0.24.1`, target `b0b2b240`, PyPI 0.24.1 verified 2026-11-08 UTC); G3 passed bound to `b0b2b24050bd`. See [live evidence](implementation/live_evidence/2026-11-08-v0.24.1.json) and the release JUnit assets.

v0.24.0 published 2026-11-08 (PR #301, tag `v0.24.0`, target `f07d6c49`); PyPI 0.24.0 verified and G3 passed; release body = `python -m tools.release_notes --version 0.24.0` output (byte-identical after CR stripping except the trailing newline GitHub appends; verified 2026-11-09). See [live evidence](implementation/live_evidence/2026-11-08.json). Previous published release: v0.23.3.

Earlier releases (v0.23.x and older, including never-published attempts such as v0.22.1, v0.17.0 and v0.18.1) are recorded in [CHANGELOG](../CHANGELOG.md); tags and release assets are on GitHub. The removed per-release G3 bindings and the note that the packaged `ag.standard` model was not exercised in some gates (#191) are in `git show 77422a6e82cb3879de207baf9d97dd79acf76aa8:docs/STATUS.md`.

### Release attempts

| Attempt | Target commit / ref | PR | Status | Notes |
|---|---|---|---|---|
| **v0.24.6** | `15fcd356` | PR #331 | Published | Published 2026-11-10; tag `v0.24.6`; publish run 42828266303 (failed-job rerun after a flaky wall-clock test, fixed in #333); PyPI 0.24.6 verified 2026-11-10 UTC; G3 bound to `15fcd35611ef`. |
| **v0.24.5** | `8fd00491` | PR #326 | Published | Published 2026-11-10; tag `v0.24.5`; publish run 42816004629; PyPI 0.24.5 verified 2026-11-10 UTC (clean upgrade with --no-cache-dir, `peerhub --version` 0.24.5, `peerhub explain cc.effort` prints the card); G3 passed bound to `8fd00491632d` (live gate 2026-11-10, three stages) |
| **v0.24.4** | `bedf6d52` | PR #320 | Published | Published 2026-11-09; tag `v0.24.4`; publish run 42605365707; PyPI verified; G3 passed bound to `bedf6d52f008`; JUnit assets attached |
| **v0.24.3** | `ad5d1b25` | PR #316 | Published | Published 2026-11-09; tag `v0.24.3`; publish run 42569582779; PyPI verified; G3 passed bound to `ad5d1b25ec37`; JUnit assets attached; [live evidence](implementation/live_evidence/2026-11-09-v0.24.3.json) |
| **v0.24.2** | `a5b61f9a` | PR #310/#311/#314 | Published | Published 2026-11-09; tag `v0.24.2`; publish run 42557525466; PyPI verified; G3 passed bound to `a5b61f9a354a`; JUnit assets attached; [live evidence](implementation/live_evidence/2026-11-09-v0.24.2.json) |
| **v0.24.2 first attempt** | `5e575697` | PR #310/#311 | 2026-11-09: publish run 42553283723 cancelled before PyPI; release and tag deleted | owner: release only after every audit axis is 100; PyPI stays 0.24.1; [attempt evidence](implementation/live_evidence/2026-11-09-v0.24.2-attempt1.json) |
| **v0.24.1 first attempt** | `0705619e` | PR #305 | Aborted; release and tag deleted | Failed publish run 42496572307; Windows release-matrix failed in `rel_005`: sandbox lacked Git; real-Git discovery tests moved to E2E in PR #306; release and tag recreated per CONTRIBUTING |
| **v0.24.1** | `b0b2b240` | PR #305 | Published | Published 2026-11-08; tag `v0.24.1`; PyPI verified; G3 passed bound to `b0b2b24050bd`; JUnit assets attached to release; [live evidence](implementation/live_evidence/2026-11-08-v0.24.1.json) |
| **v0.24.0** | `f07d6c49` | PR #301 | Published | Published 2026-11-08; tag `v0.24.0`; PyPI 0.24.0 verified; G3 passed, bound to f07d6c493af0 (live-gate JUnit header); [live evidence](implementation/live_evidence/2026-11-08.json) |

Release attempts before v0.24.0 are in [CHANGELOG](../CHANGELOG.md).

## Remaining work

Slices S1-S4 of the [issue resolution note](design/issue-resolution-2026-10-18.md) (#117-#149) are done and released; the slice rows were removed on 2026-11-10 (see `git show 77422a6e82cb3879de207baf9d97dd79acf76aa8:docs/STATUS.md`).

| Track | Status | Design SSOT |
|---|---|---|
| Deferred by Design | Top-level `peerhub readiness` (#145; cached `peerhub diag readiness` delivered in v0.21.0 part 1), lazy imports, queue/provider timing split, db schema slices 2-5, arbitrary-model monitor/readiness integration (`ag.argon` profile and exact registry delivered in #194), TUI interactive. | [Issue Resolution Note](design/issue-resolution-2026-10-18.md), issues #145, #194 |
| Open | None. #302 (ag `read_file` denied with trusted `-w`) closed 2026-11-09 as not reproducible (3 re-tests incl. the original ag.argon shape); reopen with `--json` output if it recurs. #313 is deferred (see below). | [Issue #302](https://github.com/greatgc-flow/peerhub/issues/302) |

### Deferred backlog (start triggers)

| Item | Start trigger / decision |
|---|---|
| Interactive TUI for monitor | Owner asks after the visual refresh settles. |
| Store schema v2 slices | A feature needs a non-additive schema change; current schema is v4. |
| DB lineage/provenance | Cross-store comparison becomes a recurring need. |
| `peerhub diag peers` capability probe | [Peer limits](peer-limits.md) drifts too often. |
| `peerhub diag readiness --probe`: paid active probe + before/after benchmark (#145 part 2) | NOT implemented on purpose: diag stays read-only; the manual [smoke recipe](smoke-recipe.md) is enough. Revisit if the all-profile ping recurs often. |
| View monitor quota on a phone (Telegram bot, #313) | Owner decision plus a BotFather token; keep it outside the peerhub package unless a real need appears. |
| Stuck-streams PRs 4-6 and automated timeout settlement ([plan](design/plan-stuck-streams-2026-10-12.md)) | PRs 1-3 shipped in v0.16.0; the rest is deferred until the abandonment and recovery contracts are reviewed (renewal design) and an owner asks. |
| Monitor extensions: `monitor-expand-details`, `monitor-e2e-lineage`, `live-evidence-archive`, full `db-schema-v2` DDL ([design](design/monitor-peers-view-2026-10-16.md)) | Deferred; start when a demonstrated operator need appears, or with the store schema v2 trigger above. |
| cx/ag/cc response `final` field separating narration from the final answer (#164 part B) | NOT implemented on purpose: v0.20.0 keeps the aggregate cx response unless explicit phase metadata marks commentary; a final-only response could drop answer content. Revisit with a concrete mismatch case and a schema decision. |

Operational lessons and coordinator handoff: [HANDOFF](HANDOFF.md).

The former "Archived v0 status" section (governance gateway, `tools/peerhub_facts/`, hub.py replacement, v0.3-v0.14 records) described code that has been removed. Its last full copy is in Git history: `git show 77422a6e82cb3879de207baf9d97dd79acf76aa8:docs/STATUS.md`.

## Known deferred defects (triage #241, 2026-10-20)

Valid-but-deliberately-unimplemented findings; each is closed `not planned` with label `deferred`. Library-only components (A2A, MCP, ExtensionHost, Work/Skills, Search, Approval, durable plans) have no CLI wiring. **Gate:** #225, #234 and #238 must be fixed before any A2A/Skills/ExtensionHost wiring merges; #231 is the first to promote if large probe output is reported.

| Issue | Revisit trigger / reason |
|---|---|
| #202 | L: Router ignores cost sensitivity; revisit when an actual consumer requires defined cost semantics. |
| #203 | L: zero-quota candidate demonstrably wins, but Router never dispatches; revisit before automated routing adoption. |
| #206 | C: known AG error flags are rejected; revisit only with a supported, sanitized flat-status failure fixture. |
| #207 | Closed-loop balancing is unimplemented product scope; revisit only on an explicit routing requirement. |
| #208 | C: watch/snapshot already work; revisit interactive TUI only after a demonstrated operator workflow need. |
| #209 | Universal ledger/benchmark expansion is disproportionate; revisit after a concrete escaped regression exposes a missing gate. |
| #211 | C: fresh catch-up retains all redirect pins before trimming; revisit on measured memory/latency trouble in realistic streams. |
| #212 | L: late EOF bypasses deadline recheck; revisit before A2A deployment or support for affected response wrappers. |
| #213 | L: budget omission and conservative byte accounting are intentional; revisit when a consumer requires pin-coverage reporting. |
| #214 | L: runtime temp output is unbounded until completion; revisit before adopting ProcessRuntimeAdapter for unattended commands. |
| #216 | L: host transition has a real stale-state race; revisit before concurrent ExtensionHost use. |
| #217 | L: MCP duplicates result data, but compatibility is intentional surface area and token impact unproved; revisit with measured client overhead. |
| #218 | L: sanitizer demonstrably drops prefix wildcard; revisit when shipped Search has an actual user or CLI integration. |
| #219 | L: scores collapse but SQL ranking survives; revisit when a consumer uses numeric relevance. |
| #220 | L: metadata search fetches all lexical hits; revisit on an adopted search workload showing material memory cost. |
| #221 | C: ask guard scans history under write lock; revisit on measured latency/contention rather than add binding projections speculatively. |
| #222 | L: replay and N+1 reads exist; revisit with substantial Work/Skill usage and measured bottlenecks. |
| #223 | L: source iterator eagerly loads stream IDs; pinned snapshot is intentional; revisit on large exports or WAL pressure. |
| #224 | L: re-index falsely adopts changed content, but execution integrity still rejects drift; revisit when SkillCatalog is adopted. |
| #225 | L: reducer accepts initial ACTIVE state; revisit before Skills authorization consumes externally authored/imported records. |
| #226 | C/L: constructor reinstalls triggers, but CLI readers bypass it; revisit on measured ask/refresh lock overhead. |
| #227 | L: rebuild mixes snapshots under concurrent writes; current restore requires quiescence; revisit before supporting online rebuild. |
| #228 | L: YAML/fallback meanings diverge; revisit before portable Skills manifests become a supported user workflow. |
| #231 | C: probe buffers lack byte caps, but a cross-provider streaming rewrite lacks demonstrated need; revisit on measured excessive output/RSS. |
| #233 | L: malformed proof raises AttributeError, but verification still stops before replacement; revisit on supported backup-import use. |
| #234 | L: A2A owner check is outside append transaction; revisit before concurrent durable A2A deployment. |
| #235 | L: A2A/plan journals repeatedly replay history; revisit with adopted workloads demonstrating coordination latency. |
| #236 | L: observations grow indefinitely; revisit before long-lived A2A monitoring is deployed. |
| #237 | L: approval can cross TTL, but consumption checks expiry atomically; revisit when stale approval state affects a real operator flow. |
| #238 | L: concurrent same-status evidence can be falsely acknowledged; revisit before multi-writer durable A2A use. |
| #243 | Monitor UX v2 design spec: no demonstrated reading problem (existing monitor covers most of it). Revisit when the owner supplies a recurring confusion or a disliked capture; then one S-sized follow-up with visual signoff (SOURCE/SIGNAL columns, attention strip). |

Closed follow-ups #248, #249, #256, #258, #259 and #260 are on GitHub (fixed, or closed as not planned with start triggers recorded). Backlog with triggers (#260): P0-2 (evidence chain), P0-3 (semantic outcome versus delivery), and remaining P1/P2 findings are deliberately not implemented. Keep scope indexed by #248; revisit evidence/credit/quota work with #258 and #215 only for an owner-approved consumer and evidence contract. Pricing stays research in #259, triggered by a concrete pricing decision. Routing/cost/balancing stays under #202/#203/#207 until automated routing is adopted with defined cost semantics. Monitor UX stays under #243 until recurring confusion or an owner-provided capture warrants one small change. Closed #260 as review completed with mappings and triggers recorded; closure does not imply implementation.
