# Adapter capabilities vs vendor capabilities (cc / cx / ag)

Two different things are reported separately (ACCEPTANCE_DOD, LIVE-004):
- **Vendor capability**: what the vendor CLI can do (observed, e.g. `--help` mentions a resume flag). Reported as `cli_status` evidence only.
- **Adapter capability**: what the M1 adapter (`peerhub/extensions/adapters`) actually implements and the Bridge will use. This is what counts.

| Capability | cc (claude) | cx (codex) | ag (agy) | M1 adapter status |
|---|---|---|---|---|
| deliver (fresh vendor call per delivery) | yes | yes | yes | supported |
| resume | CLI `--resume` observed | CLI `resume` observed | CLI `--conversation` observed | **unsupported** (`resume_session` -> "unsupported", spawns nothing) |
| interrupt | vendor-dependent | vendor-dependent | vendor-dependent | **unsupported** |
| terminate | vendor-dependent | vendor-dependent | vendor-dependent | **unsupported** |
| steer | vendor-dependent | vendor-dependent | vendor-dependent | **unsupported** |

Rules in M1:
- Fallback for resume = a fresh session generation plus Stream catch-up (bounded, TD-12). Vendor session ids are not persisted or mapped.
- pause/cancel/redirect only gate delivery (durable intent before any effect). The best-effort runtime effect (interrupt/terminate/steer) reports
  `runtime_outcome = unsupported`; it is never reported `done`, and the adapter is never signalled.
- An unsupported cancel never claims process termination: no evidence or result says a process was terminated. The only process kills are
  internal (output limit, timeout, consumer stopped iterating) and are reported as runtime errors with uncertainty, never as a cancel.
- Capabilities are discovered with timestamped evidence; unavailable or unsupported is reported, never synthesized.

Verified by: `tests/m1/adapters/test_t1_adapters.py` (`test_t1_capabilities_adapter_never_claims_more_than_it_implements`,
`test_t1_bridge_control_effects_report_unsupported_not_done`, `test_t1_unsupported_cancel_never_claims_process_termination[cc|cx|ag]`).
Code audit: no code path in the adapters or the Bridge reports termination of a vendor process on cancel; no change was needed.
