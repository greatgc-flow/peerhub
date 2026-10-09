# Session Bridge

## Responsibilities
- deliver Records to runtime;
- create/resume provider session;
- stream output;
- interrupt/steer when supported;
- append response Record;
- capture execution evidence.

## Provider/session mapping

```text
peer_id
stream_id
runtime_kind
external_session_id
session_generation
adapter_fingerprint
model/profile binding
resumable
state
last_seen
```

## Single-active delivery claim
To prevent two Bridges from executing the same unread Record twice:

```text
(workspace, stream, peer)
owner
generation
heartbeat/expires
```

use only these fields. Do not carry over the existing generic lease platform.

## Execution certainty
Reuse a reduced set of semantics that proved useful through earlier trial and error:

```text
NOT_STARTED
MAY_HAVE_STARTED
STARTED
TERMINAL
```

No blind replay of `MAY_HAVE_STARTED`.

## Future Harnesses
OpenAI Agents API/SDK, Claude Managed Agents, Microsoft Harness, ADK, and others
become `RuntimeTarget` adapters for Session Bridge.

## M1 Spec Alignment (2026-10-09)
CLI adapters support native terminate for active local deliveries: control.cancel kills the process tree and waits for observed exit. Resume, interrupt, and steer remain unsupported (peerhub/extensions/adapters/base.py). Bridge control intents are capability-gated (peerhub/extensions/bridge.py). Cancelled deliveries remain uncertain and cannot auto-replay; a missing local process raises RuntimeTargetError("nothing running"), recorded as failed when durable delivery evidence still identifies a target. Bridge targets already known to be absent or stale retain nothing_running/stale_target outcomes. Durable fresh-session catch-up provides continuity but does not itself stop a provider invocation.
