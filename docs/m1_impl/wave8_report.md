# Wave 8 + T1 report (LIVE-CC-001, LIVE-CX-001, LIVE-AG-001, LIVE-004, LIVE-005, LIVE-006)

Production (extension side, Core never imports it): `peerhub/extensions/adapters/{process,sanitize,base,__init__}.py`. `CliRuntimeTarget(kind in cc|cx|ag)` implements RuntimeTarget over the vendor CLI: explicit argv (no shell; `.cmd` wrappers reject cmd.exe metacharacters), prompt on stdin for cc/cx and `-p` argv for ag, bounded time and bytes (kill on overflow/timeout, process tree killed on Windows and POSIX), `deliver` = PrespawnError only before a process exists (missing binary, bad argv, oversize prompt, missing workspace), then `started`, then terminal / `timeout` event / RuntimeTargetError (uncertain). Not resumable (`resume_session` -> "unsupported"), interrupt/steer unsupported; Stage 2 adds native process-tree terminate with observed exit (Bridge falls back to a fresh generation + catch-up for resume). `discover()` runs `--version`/`--help` (no tokens): timestamped observed version, availability, capabilities with `source`, `cli_status`, `fallback`. Sanitizer: env secrets (names KEY/TOKEN/SECRET/PASSWORD/CREDENTIAL/AUTH), token patterns, `key=value` assignments, the prompt itself in error text; redact-then-truncate.

Tests: offline `tests/m1/adapters/{test_t1_adapters,test_t1_live_gate}.py` (40) against `tests/m1/harness/fake_cli.py` (modes ok/nonzero/hang/huge/garbage/secret_echo/secret_fail/vendor_error + version/help); through the real Bridge with raw-SQL oracles. Live `tests/m1/live/{live_support,test_live_canary}.py` (6 ids).
Shared change: `tests/conftest.py` hermetic `shutil.which` patch now also exempts `live`-marked tests (otherwise real CLIs were invisible).

## Probes (foreground, restored): 14 run, 14 killed
spawn failure as non-prespawn, nonzero exit as PrespawnError, response/stderr unsanitized, output bound off, no kill on timeout, resume "ok", hardcoded version, no abort on generator close, truncate-before-redact, interrupt claimed, garbage accepted, vendor is_error accepted, env secrets ignored.

## Live canary (run once, 2026-10-04 local / evidence dated UTC 2026-10-03; `peerhub diag --fresh` first: cc/ag/cx OPEN)
Evidence: `docs/m1_impl/live_evidence/2026-10-03.json` (sanitized: no prompt, no response text, no secrets). Prompt `Reply with OK`, lowest tier = packaged `<kind>.standard`, one call per provider, no retries.
| provider | observed version | model/effort | result | duration |
|---|---|---|---|---|
| cc | 2.1.288 | claude-haiku-4-5-20251001 | delivered, TERMINAL, durable response Record | 4.7 s |
| ag | 1.2.16 | gemini-3.8-flash-low | delivered, TERMINAL | 11.9 s |
| cx | 0.160.0 | gpt-6-luna / low | delivered, TERMINAL | 4.6 s |
LIVE-004: resume unsupported by adapter for all (CLI help advertises resume for all three: cli_status supported), interrupt/terminate/steer unsupported in this historical capture (Stage 2 now supports CLI terminate; captured evidence remains unchanged). LIVE-005: versions parsed from observed output, timestamped. LIVE-006: provider cwd outside project and empty after the run, project tree snapshot unchanged (positive control: snapshot detects a created file). cx ran exactly once (cx + 006 invocation); cc/ag/004/005 in the first invocation.

## Gate
Per directory green: architecture 5, meta 6, schema 16, property 13, migration 53, core 35, integration 22, observation 32, diag 14, bridge 78, control 134, security 13, concurrency 21, fault 43, e2e 12, adapters 40, unit/m1 12, package 60 + 14 CI-only skips. Default run deselects live (6); `-m live` without opt-in skips with `LIVE-OPT-IN[env=PEERHUB_M1_LIVE;required=1;marker=live]`. traceability --upto 8 ok (wave 8: 6/6); validate_package PASS.
Not done: SOAK (wave 9), session resume/interrupt/steer in real adapters, ag/cc/cx review gates, cx final review.
