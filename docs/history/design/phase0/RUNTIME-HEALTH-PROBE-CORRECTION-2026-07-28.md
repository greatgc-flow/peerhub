# Runtime health probe correction: AG login is valid under a clean child environment

> Status: factual correction. Supersedes any inference that the earlier failed
> `agy.exe models` probes proved an AG account-login loss.

## Controlled comparison

The same `agy.exe` 1.1.7 binary was run in the same workspace twice:

1. Inherited child environment: `ALL_PROXY`, `HTTP_PROXY`, and `HTTPS_PROXY`
   were each `http://127.0.0.1:9`; catalog/eligibility probing failed.
2. Sanitized child environment: only those proxy variables were removed for
   the spawned command; `agy.exe models` returned exit 0 and listed the model
   catalog, including `gemini-3.1-pro-high`.

The user's interactive terminal independently showed the same AG account as
logged in. The correct current fact is therefore `AUTHENTICATED` for the clean
AG child invocation. The failed inherited-environment probes are valid evidence
of `ENVIRONMENT_UNAVAILABLE`, not proof of `AUTH_UNAVAILABLE`.

## Consequences

- The old host's `STALE` result remains a freshness-only label and cannot by
  itself infer account failure.
- Readiness receipts must bind the sanitized/declared invocation environment.
  A receipt obtained under a clean environment cannot authorize a child process
  launched later under a different proxy environment.
- PeerHub must retain the failed proxy receipts for diagnosis while allowing a
  separately evidenced clean-environment recovery probe to establish `READY`.
- This correction does not weaken the `EXECUTABLE_UNAVAILABLE` case, nor the
  rule that PeerHub must not install, update, or authenticate vendor CLIs.
