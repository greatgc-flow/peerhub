# CX sandbox forensics: intentional nested-network boundary

> Status: characterized external constraint. No sandbox weakening authorized.

## Evidence chain

1. The CX failure inherited `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY` set to
   `http://127.0.0.1:9`, plus `CODEX_SANDBOX_NETWORK_DISABLED=1`.
2. Removing proxy variables in a child process allows local `codex debug
   models`, but a minimal end-to-end `codex exec` still fails TLS before a model
   reply with `UnknownIssuer`.
3. The active Codex configuration declares `[windows] sandbox = "unelevated"`.
4. Existing empirical documentation dated 2026-07-23 records the same
   restricted-token, proxy-poisoning, and TLS behavior and classifies it as an
   intentional Codex Windows sandbox boundary rather than a hub or provider
   outage.

## Causality assessment

The evidence does not support AG recovery as the root cause. The current
ignored config file was created after the sandbox migration marker, but that
timestamp alone does not establish its author. It is consistent with an
idempotent re-application of the existing `unelevated` policy. Any claim that
AG caused the TLS boundary requires a separate lifecycle audit record.

## Safety-preserving design response

- Do not clear proxy variables, disable the sandbox, or use blind health
  recovery to make a nested CX provider call appear healthy.
- Classify this signature as `ENVIRONMENT_UNAVAILABLE` with a policy-boundary
  receipt, distinct from peer/model/authentication failure.
- Use nested CX only for genuinely offline/local tasks. A future networked CX
  execution path must be host-mediated through a deliberate IPC boundary; it
  may not open outbound TLS from the restricted child.
- Any CA-bundle or proxy integration is a host-security design decision, not a
  PeerHub self-remediation. It requires explicit trust provenance, narrow file
  access, configuration revision binding, and a dedicated security review.
- Health monitors must recognize the unelevated signature and suppress
  network-reachability auto-recovery loops.

## AG deepthink review

AG reviewed this evidence and agreed that the sandbox policy, not AG recovery,
explains the failure. It additionally recommended a host-mediated transport
for any future network-dependent CX work and offline-first scheduling for
nested CX tasks. These are design inputs, not implementation approval.
