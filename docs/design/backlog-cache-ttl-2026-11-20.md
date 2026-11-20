# Backlog: prompt-cache TTL per peer (reviewed 2026-11-20)

Status: BACKLOG. Nothing ships in 0.25.0 except the telemetry that decides it (per-ask tokens incl. cache read/write, session-cumulative deltas, `peerhub profile stats`). Reviewed by cx and ag (both APPROVE-PLAN); owner asked for a careful review and a documented backlog.

## Decision

Raising a cache TTL is a targeted opt-in worth testing after telemetry, never a default. Cost: a longer cache write costs more, and a CLI that receives an unsupported parameter can fail every turn. Savings are claimed only after the measurement below (plan decision D4).

## Per peer

| Peer | Now | Backlog (trigger) | Never |
|---|---|---|---|
| cc | telemetry only | Pilot an opt-in that sets `ENABLE_PROMPT_CACHING_1H=1` in the child process only (`PEERHUB_CC_CACHE_1H=1` style switch; honor `FORCE_PROMPT_CACHING_5M=1`). Start when >= 20% of same-stream gaps are 5-60 min AND cache-read/cache-write >= 2.0 AND cc PACE is below cx and ag. | global env changes; automatic enabling |
| cx | telemetry only (reads already 95-98%) | Only if telemetry shows expiry misses AND the installed Codex CLI documents a setting for the model (not yet found: `prompt_cache_key` is set by the CLI per session; `prompt_cache_retention` broke every turn on gpt-5.6-sol in Codex 0.148, openai/codex#39397; on GPT-5.6+ the API uses `prompt_cache_options.ttl`, default 30 min) | injecting API parameters into CLI requests |
| ag | telemetry only; report missing counters separately from zero | Only if the CLI documents a TTL knob and counters are reliable | inferring expiry from zero reads; attaching Gemini API cache objects to CLI sessions |
| ca / oa / ga | telemetry only (no keys, no baseline) | Once keys and measured misses exist, one provider at a time: ca `cache_control {ttl:"1h"}`, oa `prompt_cache_retention` where supported, ga bounded-TTL `cachedContent`. PeerHub sends none of these today. | blanket parameters; unbounded paid storage |

## Guardrails (all changes)

- Opt-in per peer, default off; verify model, auth mode and CLI version.
- A same-prefix probe, run with owner approval (quota), must show more cache-read tokens after the baseline expiry before the option is enabled.
- On an explicit pre-execution rejection: disable the option and retry once with defaults; never loop.
- Never infer expiry from zero cache reads (a peer may simply not report them).

## Measurement (decides the trigger)

Per ask on one stream: the gap to the previous completion of that stream, input/read/write/output tokens (session-cumulative deltas), provider/model/CLI version/auth. Decide with: share of gaps in (5 min, 60 min] >= 20%, >= 30 matched comparisons, lower net cost including write premium and storage. Break-even from the pricing: a 1-hour write (2x) pays off after two reads, a 5-minute write (1.25x) after one.

Ad-hoc query until `profile stats` shows it: group asks by stream and peer, order by end time, take the difference to the previous ask's end time.

## Sources (2nd-hand where marked; re-verify before building)

- Claude Code prompt caching and env vars: https://code.claude.com/docs/en/prompt-caching ; https://github.com/anthropics/claude-code/issues/32671 ; bug "no effect for API-key user": https://claudeissues.com/issue/49139-bug-enable-prompt-caching-1h-is-not-working-api-key-user (2nd-hand)
- Anthropic pricing multipliers (5 min 1.25x, 1 h 2x, read 0.1x; Opus 5.5 read 0.05x; Fable 5.1 read 0.025x): https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- OpenAI prompt caching (`prompt_cache_retention`, `prompt_cache_options.ttl`): https://developers.openai.com/api/docs/guides/prompt-caching ; Codex issues https://github.com/openai/codex/issues/39397 and https://github.com/openai/codex/issues/35300
- Gemini context caching (implicit, no TTL control; explicit default TTL 1 h): https://ai.google.dev/gemini-api/docs/caching
