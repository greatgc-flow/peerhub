# PeerHub Development Status

> **Status Authority:** This document (`docs/STATUS.md`) is the single authoritative source of truth for the remaining development queue and the deferred backlog. The release record is [CHANGELOG](../CHANGELOG.md) plus the GitHub releases. Navigation hubs (`docs/index.md`, `docs/design/README.md`) and maintainer guidance (`docs/HANDOFF.md`) point here.

Dates are UTC.

## Current source: v0.24.13

The release record is [CHANGELOG](../CHANGELOG.md) plus the GitHub release `v0.24.13`; the G3 JUnit files (`g3.xml`, `g3-slow.xml`, `g3-e2e.xml`) and the live-evidence JSON are attached to that release. The release PR sets this heading; no per-release paragraph or follow-up docs PR is kept here.

## Remaining work

Slices S1-S4 of the [issue resolution note](design/issue-resolution-2026-10-18.md) (#117-#149) are done and released; the slice rows were removed on 2026-11-10 (see `git show 77422a6e82cb3879de207baf9d97dd79acf76aa8:docs/STATUS.md`). The per-release paragraphs and the release table that followed the heading above were removed on 2026-11-14 (#366); their last copy is `git show v0.24.12:docs/STATUS.md`.

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
