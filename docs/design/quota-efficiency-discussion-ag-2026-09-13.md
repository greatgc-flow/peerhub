# Quota-Efficient Multi-Peer Dispatch Usage
**Date:** 2026-09-13
**Author:** ag.deepthink
**Context:** Independent proposal round on quota-efficient multi-peer dispatch usage.

## 1. Session Lifecycle Policy
**Recommendation:** Default to `--session-policy fresh` for all bounded, independent extraction increments.
**Reasoning:** Since `cc` supports `--autocompact` but `cx` and `ag` do not, `cx` and `ag` will indiscriminately accumulate all previous turns in a reused session. For extraction tasks where increments are mostly independent, this unchecked accumulation inflates token usage linearly. A strict `fresh` policy acts as a hard boundary, preventing token bloat on peers lacking native compaction capabilities.

## 2. Per-Turn Context Content Management
**Recommendation:** Use a fresh dispatch each time with a concise, explicit state block.
**Reasoning:** Reusing a session just to maintain context relies on the LLM to separate relevant "current task" state from historical "completed task" chatter. Providing a synthesized "current baseline + what's done + what's left" state block in a fresh dispatch is strictly superior: it guarantees a small, highly relevant context window, reduces distraction, and yields more predictable and deterministic output while using drastically fewer input tokens per turn.

## 3. Vendor Caching Utilization
**Recommendation:** Switch to `fresh` dispatches with stable context prefixes.
**Reasoning:** Vendor API caching operates on the exact sequence of the prompt prefix. A `fresh` dispatch does **not** forfeit caching benefits, provided the large prefix (e.g., system prompt, untouched baseline code, tool definitions) remains identical across requests. Only the explicitly varying state block at the tail will incur cache misses. Thus, switching to `fresh` maintains high cache hit rates (like the observed 98.2%) while shedding the uncached, growing overhead of intermediate conversation turns.

## 4. Telemetry Accuracy Fix
**Recommendation:** Defer the fix to an explicit user-requested maintenance window; do not modify `hub_peer.py` autonomously in this round.
**Reasoning:** The `P:` drive is designated as a frozen repo, meaning autonomous modification is prohibited without explicit user request (DIR-002/DIR-004 principles).
When requested, the concrete fix should be applied in `_sys/core/hub_peer.py` within `CodexAdapter.extract_usage`. When `_usage_from_obj(obj)` successfully parses a usage dict, the adapter must explicitly inject `"token_scope": "session_cumulative"` into the parsed object before assigning it to `latest`. This prevents cumulative, end-of-session usage payloads from defaulting to `null` scope and being incorrectly summed by the telemetry pipeline.

## 5. Cross-Profile Quota-Pool Awareness
**Recommendation:** The current router design **fails** to account for shared pools and requires a structural update to avoid exacerbating rate limits.
**Reasoning:** `hub_profile_router.py` relies on `snapshot.pacing_admission_for_profile`, which reads the localized `quota` bucket for a specific profile (e.g., `cx.deepthink`). If `cx.deepthink` reaches its cap and the router falls back to `cx.effort`, the router sees `cx.effort` as having a valid local gate and dispatches the task. Because both profiles hit the same ChatGPT account pool, this blind fallback simply hammers the already-exhausted pool, generating 429s. Profiles sharing a vendor cap must be configured to reference a unified `shared_pool_id` for pacing admission.

## 6. Dispatch Granularity
**Recommendation:** Strictly enforce small, single-purpose dispatches ("keep dispatches small") independent of the lifecycle policy.
**Reasoning:** Oversized asks (as flagged by `hub.py`'s `_guard_oversized_ask`) mathematically require more raw input tokens and generally produce longer outputs. On peers without `--autocompact`, these large chunks permanently inflate the session context for all subsequent turns. Smaller dispatches natively constrain the token footprint per turn, which is critical for maintaining quota efficiency regardless of whether the session is fresh or reused.

## 7. Vendor Plan Mechanics
**Recommendation:** Treat the impact of caching on ChatGPT-style account caps as a genuinely open question requiring empirical confirmation.
**Reasoning:** While caching demonstrably reduces API dollar costs for pay-as-you-go tiers, it is undocumented whether "cache-discounted" tokens consume less quota against account-level rate limits (e.g., ChatGPT Plus/Pro message or token caps). We cannot confidently assume that a 98% cache hit rate extends the real-world cap limit. An empirical test—monitoring the cap consumption percentage while running a heavily cached vs. purely uncached session of identical volume—is required to definitively answer this.
