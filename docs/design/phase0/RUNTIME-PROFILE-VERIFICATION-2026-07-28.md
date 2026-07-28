# Runtime Profile Verification — 2026-07-28

## Scope and method

This is an execution record, not a configuration change.  Every enabled AG/CX
profile was dispatched once through the production Hub adapter with a fresh
session and an exact sentinel response.  AG calls used the configured PTY
adapter; CX calls used the configured JSONL Codex adapter.  No CC profile was
called, preserving the terminal quota for later ratification.

Before the Hub sweep, a host-side CX probe executed
`_sys/env/nodejs/npm-global/codex.cmd exec` with `CODEX_HOME` pinned to
`_sys/codex/config`, the inherited proxy variables removed only for that child,
and the configured Windows sandbox set to `elevated`.  `gpt-5.6-terra` returned
`CX_READY`.  This establishes that the former TLS failure was an
`unelevated`-sandbox execution-boundary issue, not an account or model failure.

## Results

| Profile | Configured runtime contract | Result | Elapsed | Classification |
|---|---|---:|---:|---|
| `ag.standard` | `gemini-3.6-flash`, `--effort low` | `AG_STANDARD_READY` | 19 s | available |
| `ag.effort` | `gemini-3.6-flash`, `--effort high` | `AG_EFFORT_READY` | 16 s | available |
| `ag.deepthink` | `gemini-3.1-pro`, `--effort high` | `AG_DEEPTHINK_READY` | 17 s | available |
| `ag.opus` | `claude-opus-4-6-thinking`, no effort flag | `AG_OPUS_READY` | 16 s | available |
| `ag.gptoss` | `gpt-oss-120b`, `--effort medium` | nonzero exit | 19 s | provider-unavailable |
| `cx.standard` | `gpt-5.6-luna`, effort `low` | `CX_STANDARD_READY` | 2 s | available |
| `cx.effort` | `gpt-5.6-terra`, effort `high` | `CX_EFFORT_READY` | 5 s | available |
| `cx.deepthink` | `gpt-5.6-sol`, effort `xhigh` | `CX_DEEPTHINK_READY` | 4 s | available |

The AG GPT-OSS failure is specifically classified from the Antigravity CLI log,
not inferred from the Hub exit code: the selected `gpt-oss-120b-medium` reached
the provider, retried, then received `INTERNAL (code 500): Failed to process
request`, followed by `model unreachable`.  Authentication subsequently
succeeded via keyring and the other four AG profiles completed in the same
sweep.  It is therefore neither an authentication failure nor evidence that
the G-family profiles are unavailable.  No automatic retry was made.

## Health-state finding

Two legacy health-model defects were observed:

1. A successful host-side CX probe plus `hub health-update --status GREEN`
   still left `availability.quarantined=true`.  Hub dispatch consequently
   treated CX as RED despite `health-check` reporting GREEN.  The subsequent
   `peer-recover` was evidence-backed by the recorded successful probe and
   immediately enabled `cx.standard`.
2. A single profile-scoped, retryable provider 500 for `ag.gptoss` quarantined
   the AG root, blocking its independently healthy G-family and Opus profiles.
   The root was reopened only after the four positive AG observations above;
   the GPT-OSS failure remains a negative observation.

PeerHub must replace this with a signed probe receipt and explicit state
transitions: a root gate may reopen only from a successful current probe, while
a provider/model failure affects the failing profile and its quota family unless
there is root-level evidence.  `health-update` must not present GREEN while a
quarantine remains effective.

## Operational conclusion

AG and CX can collaborate now through their verified profiles:

- AG: `standard`, `effort`, `deepthink`, and `opus` are available.  Keep
  `gptoss` out of automatic routing until a later, separately recorded probe
  succeeds; do not spend another immediate retry on a known provider 500.
- CX: `standard`, `effort`, and `deepthink` are available through the Hub after
  the elevated Windows sandbox repair.

The old Hub CLI-reality warnings (`STALE_LAST_KNOWN_PRESENT` for AG and
`UNMEASURED` for CX) are metadata freshness states, not invocation results.
This record's live dispatch evidence supersedes them for current availability
only; it does not erase their need for an automated freshness mechanism.

## Direct AG CLI revalidation (2026-07-28)

The host executable was directly measured as `agy.exe 1.1.8`. Its local
`agy models` catalog contained `gemini-3.1-pro-high`. A minimal, non-mutating
canary using the decomposed invocation

```text
agy.exe --model gemini-3.1-pro --effort high --dangerously-skip-permissions -p <exact-sentinel>
```

returned the exact sentinel `AGY_DEEPTHINK_MODEL_EFFORT_OK` with exit code 0.
This revalidates the configured `ag.deepthink` contract as base model
`gemini-3.1-pro` plus `high` effort. It does **not** prove the model identity
of an older active-session statusline; diagnostic output must label that as
an observed session value with its own source and timestamp, rather than
overwriting the declared profile contract.

The same direct, non-mutating canary form was then run for the two remaining
native-Gemini profiles and returned their exact sentinels:

| Profile | Invocation contract | Exact result |
|---|---|---|
| `ag.standard` | `gemini-3.6-flash` + `low` effort | `AGY_STANDARD_MODEL_EFFORT_OK` |
| `ag.effort` | `gemini-3.6-flash` + `high` effort | `AGY_EFFORT_MODEL_EFFORT_OK` |

Together with the DeepThink canary, this is current direct-dispatch evidence
for the three G-family configured model/effort pairs. It is not evidence of a
separate per-profile provider quota, nor does it change the distinct 3P-pool
contracts for `ag.opus` and `ag.gptoss`.
