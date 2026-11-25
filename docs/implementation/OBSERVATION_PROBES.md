# Observation probes: what each peer probe consumes

Explicit collection uses `peerhub observation refresh`. Automatic collection uses the same probe layer. See [diagnostics](../commands.md#diagnostics-and-observation) and the [monitor guide](../monitor.md) for when each command calls it.

| Peer | Probe | Consumes model quota? |
|------|-------|-----------------------|
| cc | `claude /usage` (terminal screen scrape) | No model turn |
| cx | Codex app-server JSON-RPC `initialize` + `account/rateLimits/read` | No model turn; the same response also carries `rateLimitResetCredits` (see below) |
| ag | `agy -p /usage` for quota and `agy -p /credits` for account status, both with `--output-format json --print-timeout Ns` from the workspace root | No model turn when agy recognises the slash command |

## agy slash-command guard

From the wrong directory, agy can dispatch a model turn instead of recognising the slash command. One such `/usage` call consumed about 94k tokens. The probe applies these checks.

- refuses to spawn when the resolved cwd (parent of `_sys`) is not an existing directory or is the system temp directory (evidence_ref `agy_usage_cwd_refused`, nothing is started);
- evaluates the raw JSON envelope after the run, whatever the exit code: `usage.total_tokens > 0` or `num_turns > 0` -> ERROR `agy_usage_consumed_tokens`; non-numeric token fields -> ERROR `agy_usage_unverifiable_tokens`; `command.name != "usage"` -> ERROR `agy_usage_not_a_command`. The response text is never parsed as quota in these cases. Non-JSON usage output keeps the legacy statusline-log fallback. The account probe also checks for zero tokens, zero turns and the expected `credits` command.
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

A Claude probe failure records `claude_usage_timeout`, `claude_usage_spawn_failed:<ExceptionType>` or `claude_usage_unparseable`. At 80% CPU load or higher, PeerHub doubles the probe timeout and records `quota_probe_delayed_by_load` if it still times out. Unparseable output can indicate a CLI output change.

## Monitor implementation

`peerhub/extensions/monitor_read_model.py` owns the read transaction and assembles
the snapshot. `monitor_queries.py` contains the bounded SQL. `monitor_activity.py`,
`monitor_quota.py`, and `monitor_sessions.py` project the rows, and
`monitor_alerts.py` synthesizes alerts from the snapshot evidence.

`peerhub/cli/monitor_view.py` prepares the human view and selects the layout.
`monitor_compact.py` builds the shared priority sections. `monitor_expanded.py`
adds the detail sections from `monitor_sections.py`. `monitor_activity_view.py`
assembles activity sections from `monitor_running_view.py`, `monitor_recent_view.py`,
and `monitor_leads_view.py`. `monitor_alert_view.py` and `monitor_quota_view.py`
render alerts and quota. They use `monitor_format.py` for quota tables, `monitor_layout.py` for
terminal text fitting, and `monitor_links.py` for OSC 8 hyperlinks.

The dashboard and `diag --plan` use `peerhub/extensions/diag_read_model.py`
to project stored snapshots and `peerhub/cli/view.py` to render text. The plan
and grouped monitor share window and parent rules from
`peerhub/extensions/quota_windows.py`. Terminal drawing and VT handling live in
`peerhub/_console.py`.

See the [monitor guide](../monitor.md) for refresh, redraw and quota display behavior.
