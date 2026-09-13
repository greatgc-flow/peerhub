# Quota-efficient dispatch: independent round and final ratification

Author: cx.astra. Task date: 2026-09-13. Scope: the seven requested questions; documentation and local commit only.

Part 1 was written and sealed before opening ag's independent report. Part 2 is the subsequent adjudication. This is the explicitly requested substitute for the unavailable cx.deepthink round, not a claim of a third independent voter or newly obtained R:10 unanimity. No extra model calls were made for this investigation.

Evidence convention: `[cli_live]` means retained machine-produced CLI events; `[empirical_probe]` means a locally executed reproduction or the supplied observation, identified accordingly. Code references establish implementation, not vendor entitlement. Vendor documentation and configuration are **declared, unverified on this account** unless paired with a measurement. Numerical policy thresholds below are proposed operating rules, not measured cost optima. `TEST NEEDED` marks unresolved runtime behavior.

Inspected revisions: PortableDev `1aad4f64ce92cc61028ee0495f3676f68a238d6b`; PeerHub `4b2668c826c547a734d4edef56b18cdba6cbb802`. Paths beginning `_sys/` are relative to PortableDev; product paths are relative to PeerHub. The requested `P:\workspace\peerhub` resolves to the same writable physical checkout under `D:\Engram&Peerhub\PortableDev (v2.1)\workspace\peerhub`.

## Part 1: independent answers, before reading ag

### 1. Session lifecycle policy

**Adopt fresh sessions at independent increment boundaries for all peers. Reuse only for a continuation of the same unfinished unit of work.** For R1, completing and committing an extraction is the boundary; the next extraction starts fresh even if context utilization is low. A repair of that extraction may reuse its session after verifying the base commit and session fingerprint.

| Peer | Reuse rule | Rotation rule for genuinely continuous work |
| --- | --- | --- |
| cc | Reuse within one unresolved task; retain the adapter's autocompact setting. | Keep the existing 90% measured context backstop. If compaction loses an essential constraint, checkpoint explicitly and start fresh earlier. |
| cx | Reuse for focused repair/review follow-ups on the same increment. | Keep the existing 75% measured backstop; do not wait for it before starting an independent increment fresh. |
| ag | Same task-boundary rule as cx. | Keep the existing 75% measured backstop. Treat absent utilization as unknown; it does not justify indefinite reuse. |

If utilization is unavailable, permit at most two follow-up dispatches after the initial dispatch for a single increment, then checkpoint and start fresh. This is a conservative, explicitly chosen operational fallback, not a claim that turn three is intrinsically expensive. Never rotate in the middle of an unrecorded edit or transaction: first record the exact workspace state and remaining work. New model, incompatible fingerprint, or a different task scope also requires fresh initialization.

Implementation references: `_sys/core/hub_peer.py` adapter `build_session_cmd` methods; `_sys/core/hub.py:4280` and `:6790`; `_sys/ai/protocol.json:270`. The supplied 79% >= 75% rotation is `[empirical_probe, supplied]`. The 90% cc override is present in configuration, not a newly measured optimum.

Two refinements to the supplied baseline surfaced during this independent read. Only cc's adapter explicitly requests autocompact, as stated. However, the cx R1 rollout contains a `compacted` event at `2026-09-13T05:42:02.786Z` (line 851) `[cli_live]`. No equivalent CLI flag does **not** establish that native Codex never compacts; this event does not establish whether compaction was automatic or manually triggered. The policy above therefore rests on task relevance, not an absolute no-compaction claim.

Also, PeerHub has `peerhub/application/session_saga.py:85`, containing a `SessionRotationSaga` with 90%/75% logic at `:131`, introduced in `6eae2f9`. Repository search finds no production caller of that class or `evaluate_and_claim` outside its definition; unit tests call it. `SessionPolicy` remains a mapping at `peerhub/application/commands/__init__.py:40`. Thus the useful operational conclusion is **no demonstrated connected product rotation enforcement**; the literal claim of zero rotation code is stale at the inspected revision. Do not mistake an isolated saga for an active dispatch safeguard.

### 2. Per-turn context for R1

**Use `--session-policy fresh` for each bounded R1 extraction, with a short, explicit state block.** Keep the incremental state block within 500 words, excluding mandatory standing directives. Include:

- Exact repository/base commit and the single extraction objective.
- Allowed files or module boundary, relevant source locations, and constraints.
- Completed prerequisite commits; only decisions needed by this extraction.
- Acceptance checks and expected output/commit form.
- Unresolved dependencies, or explicitly none.

Reference authoritative files and narrow code ranges instead of replaying prior debates, terminal transcripts, or every earlier extraction. Read the current files before editing. Persist the next checkpoint alongside the resulting commit. Reuse remains appropriate when fixing a failed check from that same extraction; it is not the default carrier for the entire R1 series.

This preserves the required state explicitly and bounds obsolete context. It does not promise a particular quota saving: fresh initialization and repeated file discovery also cost work. Judge efficiency per **accepted extraction including retries**, not per individual dispatch or cache percentage. Keep mandatory directives intact; this recommendation does not authorize changing their injection policy.

### 3. Vendor caching and fresh dispatch

**Fresh does not inherently forfeit caching. There is direct evidence tonight.** The fresh astra probe, session `01a09b2a-f7e4-7843-b027-6d908bf9fa44`, has one `token_usage_record`: input 18,124, cached input 12,288, output 45. Its request, turn, and thread totals are identical `[cli_live]`. A cache hit therefore occurred on its first request, without prior turns in that conversation. This establishes possibility, not a guaranteed hit rate or the identity of the reused prefix.

OpenAI documents reuse of matching rendered prefixes subject to model, cache boundaries, lifetime, and relevant settings. For GPT-5.6 and later, a shared text prefix alone may lack a reusable breakpoint when suffixes change. Stable developer instructions and separate stable content blocks help; CLI control of API cache options must not be assumed. These are **declared, unverified** mechanics for this CLI path. [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)

Claude's API likewise caches prefixes with eligible breakpoints; Gemini's API offers implicit and explicit caching, including reuse of common prompt prefixes. These API descriptions do not prove identical behavior through Claude Code or agy's subscription service. cc/ag fresh-versus-reuse cache measurements remain `TEST NEEDED`. [Claude prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching), [Gemini context caching](https://ai.google.dev/gemini-api/docs/generate-content/caching?hl=en)

Recommendation: retain stable instructions/tool configuration and place changing extraction details afterward where the existing interface permits. Fresh dispatch removes the old conversation suffix; it can lower the cache-hit percentage while also lowering absolute input. Conversely, repeated discovery can increase uncached work. Do not pad prompts to chase a percentage, assume cross-model reuse, or promise retention of the R1 session's 98.2% hit rate. `_sys/core/hub_peer.py:788` sets cx `query_first=True`, so a stable block inside the user query cannot by itself guarantee a stable full provider prefix.

### 4. Telemetry: actual root cause and concrete fix

**Fix both token semantics and scope; adding only `token_scope` is insufficient.** I reproduced the pipeline locally and matched all six affected cost rows to raw usage snapshots `[empirical_probe + cli_live]`.

In `_sys/core/hub_peer.py:907`, `CodexAdapter.extract_usage` special-cases a top-level `token_count` containing `info.last_token_usage`, returns `token_scope="turn"`, and otherwise calls `_usage_from_obj` (`:154`). That generic function passes a `usage` object to `_normalize_usage` (`:119`), which sums input, cached input, and cache creation at `:142`. That convention cannot be applied to Codex's inclusive `input_tokens`. The generic result has no scope or cached-token field, and later parsed events overwrite earlier results.

A minimal reproduction using `{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":90,"output_tokens":12}}` returned `input_tokens=190`, `output_tokens=12`, and no `token_scope`. The retained completion stdout for each old ask was not recovered in this pass; the exact event name for each historical stdout remains unverified. The numerical route through the generic normalizer is corroborated by the following exact matches:

| Ask | Raw cumulative input I | Cached subset C | Logged input I+C |
| --- | ---: | ---: | ---: |
| ask-aa21 | 15,659,250 | 15,439,616 | 31,098,866 |
| ask-c9c4 | 19,637,961 | 19,294,208 | 38,932,169 |
| ask-1b60 | 24,447,727 | 23,986,432 | 48,434,159 |
| ask-3553 | 27,598,121 | 27,108,864 | 54,706,985 |
| ask-41fe | 31,381,885 | 30,859,776 | 62,241,661 |
| ask-3e68 | 36,051,888 | 35,401,728 | 71,453,616 |

The affected `cost-log.jsonl` lines are 13747, 13760, 13761, 13763, 13764, 13765. Their sum is exactly **306,867,456**. Merely marking those rows cumulative would select **71,453,616**, still wrong.

Both hub logging branches already forward `usage.get("token_scope")`: `_sys/core/hub.py:7136-7153` and `:7330-7343`. `HubLogger.log_cost` persists the value (`_sys/core/hub_logging.py:150`). It does not drop a populated scope. `_sys/cli/diag.py:2369` treats everything except explicit `session_cumulative` as summable turn usage; distinct ask IDs prevent deduplication from saving these six rows. This is a producer semantic bug compounded by permissive consumer handling of unknown scope.

There is a third coverage issue. The last legacy `total_token_usage` reports 36,051,888 input, while the last `token_usage_record.payload.thread_token_usage` reports **36,269,828**. Summing `usage` across all **292 unique response IDs** independently yields 36,269,828 input, 35,611,136 cached input, 106,632 output, and 30,500 reasoning output. Fresh input is exactly **658,692**. The legacy cumulative view is lower by 217,940 input and 1,969 output; compaction accounting is a candidate explanation, not proven here. Do not silently equate the two sources. The user's corrected total and cache result are confirmed.

Proposed implementation contract:

1. Give Codex its own normalizer: preserve inclusive `input_tokens`; retain `cached_input_tokens` and cache-write fields separately; preserve inclusive output and its reasoning subset. Never add subsets twice. Leave other vendors' normalizers unchanged.
2. Prefer deduplicated per-response `token_usage_record.usage` for ask accounting when its exact thread/root-turn boundary is available; alternatively use the matching `turn_token_usage`. Sum the whole hub ask, not just the last model request. The existing `last_token_usage` shortcut can undercount a tool-heavy ask and must not be relabeled as the complete ask without aggregation.
3. When only a verified thread/session cumulative value is available, emit `token_scope="session_cumulative"`, exact session identity, provenance, and coverage. Prefer the complete measured thread ledger when available. A native CLI event's name is not sufficient evidence that its usage is an increment. Preserve the distinction between CLI event envelopes and rollout `event_msg.payload` envelopes.
4. If complete cumulative snapshots must become ask deltas, difference against the exact pre-dispatch baseline for the same session/generation; guard resets, compaction, concurrency, retries, and missing baselines. Never subtract the preceding log row merely because it is nearby. Unknown is not zero.
5. Harden diag so absent/unknown scopes are shown as incomplete or quarantined, not silently counted as independent turns. Define mixed-scope coverage explicitly; do not add cumulative and incremental data covering the same requests. Extend logger/schema only as needed to retain cache/provenance fields.
6. Repair historical data through a reviewable, versioned reconciliation output from raw records, retaining the original log. Do not bulk relabel every null-scope cx row; their source semantics and coverage must first be established.

Required regression fixtures before shipping: these six cumulative snapshots; the 100/90 subset example; a multi-request ask; duplicate response IDs; identity mismatch; compaction/source disagreement; failed/partial asks; mixed/unknown scopes; and a Claude usage fixture preventing cross-vendor regression. If a hub public API changes, update `_sys/tests/unit/test_contracts.py` in the same implementation commit (DIR-003).

**Freeze ruling:** this request explicitly authorizes the report and its commit, and asks for a fix proposal/recommendation. It does not request applying the runtime fix to frozen P:. Recommend a narrowly scoped follow-up implementation under explicit user authorization and the applicable consensus process. No `_sys` code, configuration, raw cost log, or governance state is changed by this report.

### 5. Quota-family routing and the apparent cross-profile contradiction

**Existing family sharing works; the real gap is loss of provider limit identity and applicability.** `_quota_family_for_profile` (`snapshot.py:1304`) reads `quota_families` from orchestration. `_filter_profile_buckets` (`:1341`) selects corresponding label prefixes. `_build_profile_rows` (`:1465`) supplies those buckets from the same collected peer quota to every matching profile. `pacing_admission_for_profile` (`:1578`) evaluates that profile's supplied bucket data, returns `unknown` when valid data is absent, and returns `over_cap` when the configured pacing rule fires. It is not meant to rediscover families.

A local execution with the actual orchestration and a synthetic X bucket over its pacing cap returned the same X bucket and `over_cap` for **standard, effort, deepthink, and astra**; unrelated G data was excluded `[empirical_probe, code fixture]`. This proves the sharing/filtering mechanics, not that the X declaration matches every actual account limit. `_sys/tests/unit/test_load_balancer.py:893` also contains existing family coverage, although its expected list predates astra.

The main ask path invokes admission on a raw profile (`hub.py:6562-6579`), and load balancing does so at `snapshot.py:2447-2463`. The current `hub_profile_router.py` is a task/profile selector using health gates; it contains **no** `pacing_admission_for_profile` call. Citing that file as the missing-family implementation would mislocate the behavior.

The concrete defect is upstream: `_codex_rate_limits` returns `rateLimitsByLimitId` as well as `rateLimits` (`snapshot.py:255`), but normalization consumes only `rl.get("rateLimits")` (`:1134`). `_codex_quota_buckets` (`:682`) labels windows `X-*` without retaining `limitId`. Correct family filtering cannot recover limits discarded before it receives them. Pacing is also not a complete model/account entitlement test; profile health, data freshness, and actual hard-limit outcomes remain necessary.

Tonight's relevant machine evidence `[cli_live]`, all local times KST:

| Event | Observed evidence |
| --- | --- |
| Earlier R1 capture | `limit_id="codex"`, primary reset epoch 1789309648 = **2026-09-13 23:27:28**. |
| deepthink failure, about 23:27:15 | Model `gpt-5.6-sol`; error says usage limit, try again at 11:27 PM. Rollout reports `limit_id="premium"`, with no primary/secondary measurements. |
| astra probe, started 23:27:59, succeeded 23:28:07 | Model `gpt-6-astra`; `limit_id="codex"`, primary 0%, weekly 68%, primary reset 2026-09-14 04:28:02. |

**Conclusion:** one universally exhausted pool is not established, and neither is an independent astra allocation. Different limit IDs warrant preserving provider identity. But the two calls straddle the recorded 23:27:28 reset, so a window reset is a concrete competing explanation. No successful same-window deepthink retest or contemporaneous complete per-model limit map is available.

The supplied block is real. Its **24-hour duration** is not independently verified: `_parse_reset_time` (`hub.py:1812`) converts time-only 11:27 PM to the next day whenever today's 23:27:00 is already past. A controlled reproduction at 2026-09-13 23:27:15 returns **2026-09-14T23:27:00**, exactly the health-file value `[empirical_probe]`. Minute precision can therefore manufacture a day-long cooldown near a reset boundary. This is evidence against treating the parsed duration as authoritative; it does not prove the vendor's intended reset. Prefer exact machine reset epochs over date inference from prose.

Retain family sharing, but represent quota applicability using provider/account scope, provider limit ID, window/reset, and measured model/profile association. Apply all known applicable constraints, share each actual bucket once, and retain unknown associations explicitly. Do not invent a separate astra family or quarantine all cx profiles on this evidence alone. Keep astra opt-in as configured. A read-only app-server query in this pass returned no result; a full current bucket map is **absent**, not inferred. A later useful authorized dispatch can record its admission/outcome and reset data; no repeated quota-testing model pings are warranted.

### 6. Dispatch granularity

**Keep asks bounded independently of the fresh/reuse decision, but do not sell five items as a measured token threshold.** `hub.py:4494` counts lines matching bullets or numbered items; it does not count semantic tasks, files, tool calls, or tokens. Configuration sets five items and 8,000 characters. The guard's comments describe an output-loss/PTY reliability concern. The 500-character IPC previews also do not provide a complete historical prompt corpus for a trustworthy matched cost comparison (`hub_logging.py:132`). No measured cost discontinuity at item six was established.

For R1, use one coherent extraction per dispatch, with at most five independently actionable substeps and a bounded output contract. Split at independent module/acceptance boundaries, not mechanically at punctuation. Batch several tiny related checks when they share setup; excessive splitting can repeat the same instructions and investigation. Fresh versus reuse controls retained history; granularity controls requested work and recovery scope. Their effects overlap, but neither replaces the other.

The supplied six-row cost sum cannot establish a causal relationship between item count and token cost: it is corrupted and counts cumulative session observations. After fixing accounting, compare accepted-work totals across matched tasks, including tool results, reasoning/output, cache categories, and retries. Until then the specific material-cost correlation is **TEST NEEDED**.

### 7. Vendor plan mechanics: caching and the cap

**Reject both “cache is free quota” and “cache only affects hypothetical dollars.”** Current official Codex pricing says caching is among the factors affecting allowance usage, alongside model/context/reasoning/tools. It separately lists lower cached-input credit rates. Thus caching is relevant to included-plan consumption according to the vendor, but this does not publish or measure the conversion formula for this account's cap. **Declared, unverified account effect.** [Codex/ChatGPT pricing and usage limits](https://learn.chatgpt.com/docs/pricing)

OpenAI's API documentation says cached input still counts toward API tokens-per-minute limits. That is a different meter from a ChatGPT subscription allowance; it cannot establish identical cap treatment. **Declared, unverified here.** [OpenAI prompt-caching FAQ](https://developers.openai.com/api/docs/guides/prompt-caching)

Tonight's 98.2% cache hit and later usage block do not isolate a cache discount: no controlled cap delta or counterfactual exists. The 658,692 fresh-input count is therefore not a justified quota-consumption total. The astra success adds no causal evidence about caching's effect on the cap because model, request size, limit identity, and reset timing differ. Exact cached/uncached/reasoning conversion against included caps remains **OPEN / TEST NEEDED**; do not estimate it from API prices or credit rates. Prefer compact relevant context and cache-friendly stable material together.

**End of independent Part 1.** The text above was saved before opening ag's report and is preserved unchanged in Part 2's comparison.

## Part 2: final ratified verdict

Independence record: Part 1 was sealed at **2026-09-13T23:37:42+09:00**, before ag's report was opened. Its complete file bytes through the end-of-Part-1 line, including the final LF, have SHA-256 `4725ed4e97c7b00fc793fec0df04a6759c96cd991f71471af872e16966434d98`. The subsequently read [ag independent report](quota-efficiency-discussion-ag-2026-09-13.md) has SHA-256 `8fee6366a39ffe0d2f350bc3b264be17470941aa4efd61e247c06aa9a00f9c5f` and is committed in `4b2668c`. The analysis below compares those two fixed answers.

One final verdict per question:

| Q | Adopted verdict |
| --- | --- |
| 1 | **Adopt the common fresh-per-independent-increment recommendation, with my concrete continuation/backstop rules:** cc 90%, cx/ag 75%, and a bounded follow-up fallback when context is unknown; ag's categorical claim that cx retains every turn unchecked is contradicted by the observed compaction event. |
| 2 | **Adopt the shared fresh-plus-explicit-state approach for R1:** use the 500-word state-block budget and exact base/acceptance contract from Part 1; reject ag's universal claims of strict superiority, deterministic output, and guaranteed drastic savings because none were measured. |
| 3 | **Adopt my qualified caching recommendation; material disagreement with ag's guarantee:** fresh can cache, proven by the first-request astra record, but an identical text prefix does not guarantee an eligible cache hit, let alone preservation of 98.2%; measure absolute accepted-work consumption. |
| 4 | **Adopt my complete fix specification and the shared defer-implementation decision; material disagreement with ag's one-line fix:** relabeling alone would still leave the latest row at 71,453,616 input, so Codex subset normalization, provenance/scope, full-ask coverage, and historical reconciliation must be handled together. |
| 5 | **Adopt my finding and reject ag's specific router-failure claim; material disagreement:** family filtering already shares the X buckets across all four cx profiles; retain it and address discarded provider limit IDs/applicability plus ambiguous reset parsing, without asserting a universal exhausted pool or a separate astra allocation. |
| 6 | **Adopt bounded coherent dispatches as independent guidance, using my qualification:** retain the existing five-item/8,000-character guard for its stated reliability purpose, but there is no measured item-six cost cliff and arbitrary fragmentation can repeat setup. |
| 7 | **Adopt my narrower open question; material disagreement with ag's “undocumented” premise:** official documentation identifies caching as an allowance factor, while the exact account-cap conversion remains unmeasured; neither the 98.2% hit rate nor astra's success determines that conversion. [Official pricing](https://learn.chatgpt.com/docs/pricing) |

### Specific ruling on ag's question 5

**Ag's cited claim is incorrect, not merely an alternative interpretation.** Its argument assumes each profile's `quota` field is an independent budget. The inspected code populates those fields by filtering the same root-peer bucket data according to each profile's declared family. The local four-profile fixture demonstrates the consequence: one over-cap X bucket produces `over_cap` for every cx profile. Additionally, the cited `hub_profile_router.py` does not call the pacing function; admission resides in hub/snapshot. A new `shared_pool_id` is therefore not justified as a remedy for an alleged absence of family sharing.

There **is** a supported structural concern, but it is a different one: the collector receives a per-limit map and normalizes only the default entry, while bucket labels erase limit identity. That can produce incomplete admission decisions despite working family sharing. A future implementation should preserve canonical provider/account/limit identities and measured applicability, with family metadata expressing group membership. Any shared accounting or reservation layer must count an actual bucket once rather than add its repeated profile projections. This report establishes the normalization gap; it does not claim to have audited or proved every concurrent-admission reservation failure.

The strongest explanation of tonight's asymmetric outcome remains unresolved. The observed `premium`/`codex` IDs support examining multiple constraints, while the reset at 23:27:28 makes a before/after reset explanation plausible. The parser reproduction explains why local health can call this a 24-hour block without a vendor-supplied date. These facts defeat ag's confident narrative of a fallback hammering a known universally exhausted pool. They do not establish that every fallback is safe. Preserve the actual failure and the later success as separately scoped observations.

### Adoption boundary and follow-up priority

Adopt fresh R1 increments with explicit state and bounded scope now as dispatch guidance. Retain native continuation for genuine continuations and existing measured rotation backstops. Do not change frozen runtime/configuration in this documentation task.

For a subsequently authorized maintenance task, prioritize the reproducible accounting defects and reset-date ambiguity, followed by preserving the per-limit quota map and its applicability through routing. Use the existing product saga as an implementation artifact to inspect when integrating product rotation; do not commission a duplicate based on the stale “zero code” premise. No exact cap-saving percentage, separate astra allocation, or blanket safe cross-profile fallback is ratified.

Validation for this report: six raw/log numerical matches; 292 response-ID uniqueness and usage-total reconciliation; Codex cache-subset reproduction; four-profile family/admission fixture; reset-parser reproduction at the incident time; review of first-request astra usage; production-caller search for the isolated product saga; UTF-8, whitespace, seal, and commit-scope checks. No runtime implementation tests or paid cache/quota experiments were needed for this documentation-only deliverable.
