# Context Continuity and Token Efficiency (2026-10-09)

Question: Do we need native session control (resume, interrupt, steer), or is the current design of injecting bounded context into a fresh session each time appropriate?
Method: independent opinions from ag·cx → mutual rebuttals → direct measurements and official documentation research by cc. The conclusions follow.

## Conclusions
- Keep **Records + bounded catch-up + a fresh session** as the default (preserves provenance, avoids hidden state, prevents context decay).
- Resuming does not reduce model input tokens. The API is stateless, so the CLI resends the previous conversation each time. The only difference is **price** (prompt caching).
- Interrupting (cancelling) addresses latency, stops work that is no longer useful, and prevents side effects; it is not about tokens. Implemented (process tree termination, termination observation).
- Do not implement steering or native resume. If usage measurements confirm a benefit, consider an explicit per-thread opt-in for cc·cx only. Defer ag until its backend TTL and billing contract are confirmed.

## Measurements (haiku, approximately 30,000 tokens of context, same question)
- Both resumed and fresh sessions receive approximately 30,000 input tokens per turn.
- Per-turn cost with a warm cache: approximately $0.0037 for resume, $0.0216 for fresh-session injection (approximately 5.8 times). A fresh session writes the approximately 9.6k injected tokens to the cache anew each time (write multiplier 1.25, read multiplier 0.1).
- The benefit lasts only within the cache lifetime (Anthropic default: 5 minutes; main subscription conversation: 1 hour). With longer gaps, resume also rewrites the entire context.
- Codex fresh sessions with the same prefix had automatic cache hit rates varying from 0~99%.
- Fixed CLI overhead is approximately 14,000 (Codex)~25,000 (Claude) input tokens per fresh session and also applies to resume requests.
- Roughly, resume is cheaper when "warm resume history × 0.1 < size of newly injected catch-up".

## Changes Implemented
1. Per-request usage records: `usage` in `ask --json` and response Record metadata (`input_tokens`, `cached_input_tokens`, `cache_write_tokens`, `output_tokens`; values the CLI does not report are omitted rather than set to 0).
2. Include record locations, IDs, and kinds in the catch-up prompt; if truncated, put an omission notice on the first line. Place the current question last.
3. Add a 24,000-byte cap to the default catch-up budget (within ag's 30,000-byte Windows inline limit).
4. Implement `control.cancel` through process tree termination (`supports_terminate`). After termination, an in-progress delivery does not finish as a normal response.

## Follow-up Candidates (After Measurement)
- Stable prefix layout: fixed instructions + a versioned checkpoint (summary Record) → changing recent records → current question. A sliding window changes the prefix and invalidates the cache.
- Compare fresh sessions and resume in paired measurements at 2·10·50 turns, with intervals within and beyond the cache lifetime (input, cache reads/writes, output, latency, correctness when files change).
- Measure subscription quotas separately from API cost conversion.
