# Adapter capabilities vs vendor capabilities (cc / cx / ag)

Two different things are reported separately (ACCEPTANCE_DOD, LIVE-004):
- **Vendor capability**: what the vendor CLI can do (observed, e.g. `--help` mentions a resume flag). Reported as `cli_status` evidence only.
- **Adapter capability**: what the M1 adapter (`peerhub/extensions/adapters`) actually implements and the Bridge will use. This is what counts.

| Capability | cc (claude) | cx (codex) | ag (agy) | M1 adapter status |
|---|---|---|---|---|
| deliver (fresh vendor call per delivery) | yes | yes | yes | supported |
| resume | CLI `--resume` observed | CLI `resume` observed | CLI `--conversation` observed | **unsupported** (`resume_session` -> "unsupported", spawns nothing) |
| interrupt | vendor-dependent | vendor-dependent | vendor-dependent | **unsupported** |
| terminate | local process tree | local process tree | local process tree | **supported** (active local delivery only; observed exit required) |
| steer | vendor-dependent | vendor-dependent | vendor-dependent | **unsupported** |

Rules in M1:
- Fallback for resume = a fresh session generation plus Stream catch-up (bounded, TD-12). Vendor session ids are not persisted or mapped.
- Control intent is committed before any runtime effect. Pause/redirect retain unsupported interrupt/steer outcomes.
- Cancel invokes terminate for the persisted target: a thread-safe registry maps external_session_id to the active local BoundedProcess.
  Windows uses `taskkill /T /F`; POSIX starts a private session and kills its process group with `killpg`.
  The runtime waits for observed process exit before reporting success; an unconfirmed kill raises RuntimeTargetError (Bridge outcome `failed`).
- Cancellation marks the in-flight delivery aborted; it raises RuntimeTargetError after start, leaving uncertainty and no response or auto-replay.
- No active local process raises RuntimeTargetError("nothing running"), never a successful kill. The Bridge records `failed` if durable
  STARTED/MAY_HAVE_STARTED evidence still targets that session; its existing absent/stale target checks retain `nothing_running`/`stale_target`.
- The registry belongs to the runtime instance owning delivery. Another runtime instance/process cannot claim to kill an unregistered invocation.
- Capabilities are discovered with timestamped evidence; unavailable or unsupported is reported, never synthesized.

Coverage: `tests/communication/adapters/test_t1_adapters.py` (capability discovery, unsupported pause,
cancel without a local process, running CLI cancel with uncertain delivery and no replay), and
`tests/communication/adapters/test_native_terminate.py` (real child/grandchild tree, cross-thread termination, failed kill/exit observation).

## M1 Spec Alignment (2026-10-09)
CLI adapters support native terminate for active local deliveries: control.cancel kills the process tree and waits for observed exit. Resume, interrupt, and steer remain unsupported (peerhub/extensions/adapters/base.py). Bridge control intents are capability-gated (peerhub/extensions/bridge.py). Cancelled deliveries remain uncertain and cannot auto-replay; a missing local process raises RuntimeTargetError("nothing running"), recorded as failed when durable delivery evidence still identifies a target. Bridge targets already known to be absent or stale retain nothing_running/stale_target outcomes. Durable fresh-session catch-up provides continuity but does not itself stop a provider invocation.
