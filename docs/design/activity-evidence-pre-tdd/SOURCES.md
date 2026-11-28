# Source baseline and evidence ledger

Inspection date: 2026-11-28 KST. Source facts are code/document observations, not live integrations. Public vendor documentation and terms can change; check exact installed versions, user country/account/organization policies and current terms at WP0 before implementation or activation. This is not a legal opinion.

## PeerHub pinned source

Repository: https://github.com/greatgc-flow/peerhub
Commit: b1d877d689a9bc79d16054de7371b28b108049fd; version 0.28.2; remote CI https://github.com/greatgc-flow/peerhub/actions/runs/45983685529 and publish https://github.com/greatgc-flow/peerhub/actions/runs/45991395950 both reported success on this head. Current design tests not run. Open PR listing empty and issues #504, #503, #502, #498 returned at inspection; NOW describes some fixes as released, so resolve discrepancies by new remote check.

All paths below link to the pinned revision:

- [CONVENTION.md](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/CONVENTION.md): Core ownership, immutable history, read-only diagnostics, tests and release policy.
- [AGENTS.md](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/AGENTS.md): Lead/lane boundaries, LF, dev test setup, architecture gates, requirements and no fabricated tests.
- [NOW.md](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/NOW.md): 0.28.2 state, #504, incomplete cc usage, deferred diagnostics.
- [README.md](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/README.md): Public CLI, store selection, request replay, session reuse, writable ag limitation.
- [docs/implementation/EXECUTION_KERNEL_BOUNDARIES.md](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/docs/implementation/EXECUTION_KERNEL_BOUNDARIES.md): Core narrow; kernel owns execution; read projection and pinned record snapshot.
- [peerhub/core/store.py#L123-L180](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/core/store.py#L123-L180): Read-only opening and restore lineage.
- [peerhub/core/store.py#L513-L545](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/core/store.py#L513-L545): Per-stream read and pinned all-record snapshot; not global change stream.
- [peerhub/core/models.py#L168-L184](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/core/models.py#L168-L184): Record ids, position, body, reply_to, metadata, time.
- [peerhub/extensions/ask.py#L505-L578](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/extensions/ask.py#L505-L578): Persisted prompt, public result and activity evidence.
- [peerhub/extensions/bridge_ledger.py#L689-L735](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/extensions/bridge_ledger.py#L689-L735): Response materialization, separate ack; metadata and usage scope.
- [peerhub/extensions/bridge_schema.py](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/extensions/bridge_schema.py): Delivery/evidence/session schema and append-only guards.
- [peerhub/extensions/ask_budgets.py](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/extensions/ask_budgets.py): Outcome table, usage delta and quota observations.
- [peerhub/extensions/profile_stats.py#L33-L85](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/extensions/profile_stats.py#L33-L85): Outcome write and latest read, binding and usage joins.
- [peerhub/extensions/usage_normalization.py](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/extensions/usage_normalization.py): Provider-specific normalized fields and source/scope/coverage.
- [peerhub/extensions/session_receipts.py](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/extensions/session_receipts.py): Terminal/session receipt and usage context separation.
- [peerhub/extensions/runtime_contract.py](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/extensions/runtime_contract.py): RuntimeTarget and event port, not a web async contract.
- [peerhub/extensions/adapters/event_parser.py](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/extensions/adapters/event_parser.py): Incremental primary parser, tool detection, terminal validity.
- [peerhub/extensions/adapters/process.py](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/extensions/adapters/process.py): BoundedProcess, stdout callbacks and parser failure.
- [peerhub/extensions/adapters/cli_runtime.py#L279-L449](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/extensions/adapters/cli_runtime.py#L279-L449): Execution, primary parser, AG separate transport, sanitization and bounded process.
- [peerhub/extensions/adapters/cc.py](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/extensions/adapters/cc.py): Native cc argument, terminal/result usage parse.
- [peerhub/extensions/adapters/cx.py](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/extensions/adapters/cx.py): Native cx argument, thread/turn and usage parse.
- [peerhub/extensions/adapters/ag.py](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/extensions/adapters/ag.py): Native ag flat/stream, fail-closed writable policy.
- [peerhub/extensions/adapters/telemetry.py](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/extensions/adapters/telemetry.py): Provider telemetry projection, finite/strict usage and cc context estimate.
- [peerhub/extensions/adapters/sanitize.py](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/extensions/adapters/sanitize.py): Limited sanitizer, redaction before truncation; not complete secret scanner.
- [peerhub/cli/core_cmd.py](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/cli/core_cmd.py): Stream list and Record read, paging.
- [peerhub/cli/session_cmd.py](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/cli/session_cmd.py): Existing session cursor/scope/lineage reference.
- [peerhub/cli/app.py#L34-L52](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/cli/app.py#L34-L52): Registered public commands, no activity/web/memory namespace at baseline.
- [peerhub/extensions/artifact.py](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/extensions/artifact.py): Existing content-addressed artifacts; reuse candidate subject to scope.
- [peerhub/extensions/eval.py](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/extensions/eval.py): Existing trace/evaluation/telemetry interfaces; not evidence of live capture wiring.
- [docs/m2/M2_6_EVAL_CONTRACT.md](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/docs/m2/M2_6_EVAL_CONTRACT.md): Trace/export-only, no authority mutation or telemetry-induced failure.
- [peerhub/extensions/memory.py](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/extensions/memory.py): Derived memory lifecycle; separate from Hindsight.
- [peerhub/extensions/host.py](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/peerhub/extensions/host.py): Extension host exists; do not assume new CLI auto-loads it.
- [pyproject.toml](https://github.com/greatgc-flow/peerhub/blob/b1d877d689a9bc79d16054de7371b28b108049fd/pyproject.toml): Dependency policy; preserve default dependencies.

Issue references: https://github.com/greatgc-flow/peerhub/issues/504 ; https://github.com/greatgc-flow/peerhub/issues/503 ; https://github.com/greatgc-flow/peerhub/issues/502 ; https://github.com/greatgc-flow/peerhub/issues/498 .

## Engram integration source

Commit c59c0e42bb6584bc873269b7128a1c94ecba3229, branch design/local-ai-pre-tdd-20261128. Read [SPECIFICATION §7–8](https://github.com/greatgc-flow/Engram/blob/c59c0e42bb6584bc873269b7128a1c94ecba3229/docs/design/local-ai-pre-tdd/rev06/SPECIFICATION.md), [CONTRACTS §5](https://github.com/greatgc-flow/Engram/blob/c59c0e42bb6584bc873269b7128a1c94ecba3229/docs/design/local-ai-pre-tdd/rev06/CONTRACTS.md), [IMPLEMENTATION_PLAN WP6/WP6b](https://github.com/greatgc-flow/Engram/blob/c59c0e42bb6584bc873269b7128a1c94ecba3229/docs/design/local-ai-pre-tdd/rev06/IMPLEMENTATION_PLAN.md), [TEST_CATALOG](https://github.com/greatgc-flow/Engram/blob/c59c0e42bb6584bc873269b7128a1c94ecba3229/docs/design/local-ai-pre-tdd/rev06/TEST_CATALOG.csv). Especially T92–T100. Engram owns memory, knowledge, skill and permission policy. Existing CLI import sufficient for early partial PoC. Export schema proposal requires both leads' review; no change to the Engram baseline.

## Provider CLI documentation (current docs; actual installed versions unverified)

- https://developers.openai.com/blog/eval-skills — official example of codex exec --json, command_execution completed items, exit_code, turn.completed usage, independent test and artifact checks. It does not imply all tools or internal contexts are exposed.
- https://learn.chatgpt.com/docs/non-interactive-mode — Codex noninteractive route (official redirect from developers.openai.com/codex/noninteractive); verify actual CLI flag/format in fixtures.
- https://code.claude.com/docs/en/hooks — official hook event fields including session_id, transcript_path, tool_use and subagent references. Do not assume full log is authorized or stable. No automatic hook installation in PeerHub.
- https://antigravity.google/docs/cli/headless/ — official agy text/json/stream-json output, step_update, session usage and cumulative counters. Actual tool payload schemas and installed version must be proven; no private endpoint.
- https://antigravity.google/docs/cli-features — review changes and execution mode. Does not establish complete external filesystem audit.

## Consumer Web and data rights (current public documentation)

- https://platform.openai.com/policies/terms-of-use/ and https://openai.com/en-GB/policies/row-terms-of-use/ (effective 2026-01-01). Consumer terms restrict programmatic extraction and rate-limit/protection circumvention. No unattended Web extraction in this revision.
- https://help.openai.com/en/articles/7260999-exporting-your-chatgpt-history-and-data — eligible user-requested exports and conversation files, availability varies by workspace.
- https://www.anthropic.com/legal — legal center; linked Consumer Terms effective 2025-10-08, page 3 items 2, 4, 7 (PDF https://assets.claude.com/ed7f1f94a2ecb7657fb4be5af6f66c66b38fb148.pdf) restrict competing development, unauthorized scraping and automated access absent permitted route. Page checked. Use official permitted integrations or user-mediated workflow only; confirm downstream usage rights.
- https://support.claude.com/en/articles/9450526-export-your-claude-data — export conversations for eligible accounts; does not include original uploaded files.
- https://policies.google.com/terms (current version checked; country-specific terms must be confirmed) — automated access restrictions and AI-generated content use restrictions. Not proof of authorization for Gemini browser automation.
- https://support.google.com/gemini/answer/16920332 — Gemini Apps data and Takeout activity export, source availability varies.
- https://code.claude.com/docs/en/claude-code-on-the-web — official cloud coding route and async session behavior; separate product from generic Claude Web chat, repo upload risk. Deferred separate design.

The manual Web route is an optional local packet and explicitly supplied reply, not a programmatically controlled consumer browser. Official export access alone is not permission for all downstream model uses. No paid access, account inspection, live query, browser automation, hidden reasoning capture or policy evasion performed.
