# Observation probes: what each peer probe consumes

`peerhub observation refresh` is the only writer of quota evidence; `peerhub diag quota` / `peerhub diag` only read it (never probe, never refresh).

| Peer | Probe | Consumes model quota? |
|------|-------|-----------------------|
| cc | `claude /usage` (terminal screen scrape) | No model turn |
| cx | Codex app-server JSON-RPC `initialize` + `account/rateLimits/read` | No model turn; the same response also carries `rateLimitResetCredits` (see below) |
| ag | `agy -p /usage --output-format json --print-timeout Ns`, cwd = workspace root | No model turn **only** when agy recognises `/usage` as a slash command |

## agy `/usage` guard

Run from the wrong directory, agy does not recognise `/usage` and dispatches a real model turn (about 94k tokens). The probe therefore:

- refuses to spawn when the resolved cwd (parent of `_sys`) is not an existing directory or is the system temp directory (evidence_ref `agy_usage_cwd_refused`, nothing is started);
- evaluates the raw JSON envelope after the run, whatever the exit code: `usage.total_tokens > 0` or `num_turns > 0` -> ERROR `agy_usage_consumed_tokens`; non-numeric token fields -> ERROR `agy_usage_unverifiable_tokens`; `command.name != "usage"` -> ERROR `agy_usage_not_a_command`. The response text is never parsed as quota in these cases. Non-JSON output keeps the legacy statusline-log fallback.
- records `reason`, `consumed_tokens`, `num_turns` in the ERROR observation payload; `refresh_quota` returns them in a `warnings` list, printed to stderr by `observation refresh` and `monitor` (use `monitor` for repeated collection).
- `peerhub monitor` stops with exit code 1 after the first token-consumption event; `--allow-agy-token-use` overrides this.

## Codex reset credits (kind `reset_credit`)

Subject `cx`, resource pool `credit:cx:reset` (kind ACCOUNT), written from the same app-server response as the rate limits.

- Payload when MEASURED: `available_count`, `credits` (sorted by `expires_at`: `id_ref`, `reset_type`, `status`, `granted_at`, `expires_at` as epoch seconds, `title`), `nearest_expires_at` (earliest expiry among credits with status `available`, else null). The free-text `description` is not stored.
- `rateLimitResetCredits` missing or null -> UNKNOWN (never "zero credits"); present with `availableCount: 0` -> MEASURED with count 0; any malformed value (wrong types, negative, NaN, `expiresAt` before `grantedAt`) -> ERROR with `payload.reason` only (no partial values).
- Freshness uses the default observation TTL like other quota evidence.
- Account-level flags (`planType`, `spendControlReached`, `rateLimitReachedType`, `ordinaryUsageAllowed`, `credits` balance) are not captured yet.

`diag quota` shows a `reset_credits` list (JSON) and a "reset credits" block (table, also in the `diag` dashboard): count, nearest expiry in ISO UTC, hours until expiry computed at read time, per-credit status, `WARN` when an available credit expires in strictly less than 72h. Credit ids are shown only as a 12-char sha256 prefix (`id_hash`).

The `reset_credit` kind extends the Observation kind list of the frozen M1 contract (docs/m1_spec is unchanged): owner decision pending.

## `peerhub monitor`

A convenience command that restores the old `diag --live` experience in ONE process: each cycle collects quota evidence (the same collector as `observation refresh`) and then renders ONE read-only dashboard frame (the same renderer as `diag --live`), then sleeps. The composition lives in the CLI layer (`peerhub/cli/monitor.py`): Diag stays strictly read-only and never refreshes, Observation stays the only writer, and extension imports are limited to the allowlist enforced by `tests/communication/architecture/test_arch_extension_graph.py`.

```
peerhub monitor [--interval-seconds SECONDS=60] [--cycles N] [--peers cx cc ag] [--timeout-seconds SECONDS=15]
                [--collect-every K=1] [--view auto|rich|plain] [--json] [--allow-agy-token-use]
```

- Omitted `--cycles` defaults to one cycle off-TTY and runs until interrupted on a TTY. `--cycles 0` watches until Ctrl-C everywhere (clean exit 0). `--collect-every K` collects every Kth cycle but redraws every cycle.
- `--json` prints NDJSON (schema 2.0): `{"schema_version": "2.0", "cycle": n, "phase": "initial"|"complete", "refresh": {...} | {"skipped": true}, "snapshot": <grouped monitor object>}`; one `initial` frame, then one `complete` frame per cycle (so the default off-TTY run prints two lines). `--schema-version 1.1` keeps the legacy diag snapshot.
- Exit codes: 0 normal/interrupted; 1 when the agy token-use guard stopped the loop, or the last collection was not OK when the cycle limit ended the run (including the implicit single cycle off-TTY); 2 usage error (nothing is collected, no database is created).
- Every cycle costs one `/usage` read per peer (no model turn; see the probe table above). Do not set intervals of a few seconds.

### `peerhub monitor --view {auto,rich,plain}`

The default view is the compact POOLS/PEERS screen (states, legend and `suggest:` are described in the [README](../../README.md) and `peerhub explain monitor`). `--expand` shows the grouped detail view: a header (status, time, DB source), then one section per selected group (`QUOTA` with `PEER POOL REM RESET-IN PACE STATE AGE PROFILES`, refresh hints and a state key; `PEERS`; `EXECUTION`; `SESSIONS`; and the other groups from `peerhub monitor groups`). `rich` (default on a terminal) and `plain` share the same layout; `plain` is ASCII without colour. `NO_COLOR` disables colour; colour otherwise requires a terminal. Non-UTF-8 terminals get an ASCII layout. Both views are pure renderers over the read-only grouped snapshot (`peerhub/cli/monitor_view.py`); they never write evidence.

#### Reading the expanded view
- **PACE** = `used% - window elapsed%`, in percentage points at measurement time. A 5-hour window that is 40% elapsed with 60% used reads `+20.0`: usage is 20 points ahead of an even burn. Pace has no independent risk colour; the compact view turns a supported threat into `fast`.
- **STATE** `THREAT` means the projection exhausts the pool before reset; `INSUFF` means too little projection data; `STALE:*` marks aged evidence.
- On a terminal the screen redraws every second between cycles (clock, evidence ages, reset countdowns and "next in" move; nothing is re-read until the next cycle). Piped output prints the initial frame, then one frame per completed cycle.
- A claude `/usage` probe that fails now stores a reason in the ERROR evidence (`claude_usage_timeout`, `claude_usage_spawn_failed:<ExceptionType>`, `claude_usage_unparseable`), and the dashboard shows it next to `ERROR`. Timeouts usually mean the machine was busy (raise `--timeout-seconds`), unparseable output means the CLI output format changed.
