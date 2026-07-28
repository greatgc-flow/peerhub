# Runtime health drift addendum: authentication, network, and missing CLI

> Status: open design input. Read with `RUNTIME-HEALTH-DRIFT-2026-07-28.md`.

## Re-check result

The second no-spend AG catalog probe did not confirm a login. `agy.exe models`
returned exit 1 because its eligibility request could not connect through the
configured loopback proxy. Together with the earlier `Please sign in` result,
this means there is no current successful authentication/readiness receipt.
The two failures must remain distinguishable: `AUTH_UNAVAILABLE` and
`NETWORK_UNAVAILABLE` have different operator remediation and retry policy.

## Required PeerHub v1 readiness outcomes

`EXECUTABLE_UNAVAILABLE` is independent of stale/auth/network/provider state.
It is returned when a configured executable reference cannot be resolved to a
compatible local executable. Its receipt must include the configured reference,
platform/architecture, resolver result, configuration revision, and probe
time. It must not attempt a network, auth, or provider probe.

PeerHub does not install, update, repair, or authenticate vendor CLIs. Those
are host/operator actions. After an operator repairs an installation, any
executable path or fingerprint change creates a configuration revision change;
the command is re-planned and must obtain fresh executable, authentication,
and readiness evidence before dispatch.

The admission taxonomy is therefore at minimum:

- `READINESS_STALE`: prior evidence expired; no current failure inferred.
- `EXECUTABLE_UNAVAILABLE`: binary path/resolution/compatibility failure.
- `AUTH_UNAVAILABLE`: current authentication check failed.
- `NETWORK_UNAVAILABLE`: required readiness probe could not reach its service.
- `PROVIDER_UNAVAILABLE`: authenticated probe reached the provider and failed.
- `READY`: all command-required evidence is current for the sealed runtime
  configuration revision.

Only `READY` permits a provider effect. `READINESS_STALE` may permit a declared
zero-cost revalidation attempt. Every negative result preserves its evidence;
administrative recovery may authorize such a probe but cannot write `READY`.

## Fixture implication

The controlled fake-adapter matrix for HR-02 through HR-06 must contain an
absent executable case and must assert that no subprocess/network/provider
attempt occurs after `EXECUTABLE_UNAVAILABLE`. It also needs separate
authentication and proxy/network failures, plus a successful recovery-probe
receipt under a changed configuration revision.
