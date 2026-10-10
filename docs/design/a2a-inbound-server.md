# Inbound A2A server

Status: **PROPOSED, NOT IMPLEMENTED**. Deferred by the maintainer on 2026-10-10: **“guide and design only”**. This record authorizes no
implementation.

## Purpose

Send work to the server with the right resources/environment: one owner, 2–3 servers on a private network.

## Decision

Use [SSH first](../remote-ask.md). Build only if a condition below is met and the blockers are resolved. Keep the Core unchanged; any server
belongs in extensions. All behavior below is proposed, not a claim of current server behavior.

## Why A2A

A2A was the only protocol considered for delegation because it supplies a task lifecycle and Agent Card and already has an outbound client in
`peerhub/extensions/a2a_http.py` (Review finding (cx)); MCP is tool/context access (**unverified** characterization in the permitted sources).

## Prior decision

The [2026-07-19 review](../history/from-engram-repo/protocol-docs/engram-peer-governance/external-server-ization-proposal-review-2026-07-19.md)
rejected turning Engram into a network-facing multi-tenant backend. Its findings were generic/domain separation, peer equality/no tenant-isolated
channels, minimal credential custody, scope mismatch and overstated operational readiness.

For this single-owner private network, separation, credential minimization, honest readiness claims and history isolation still apply.
Third-party business schemas, independent tenants and their consent/compliance requirements do not describe the proposed scope. Private
networking does not solve execution isolation or credential exposure. This applicability assessment is a proposed decision, not proof that old
Engram governance applies unchanged to current code. An explicit maintainer ADR line identifying the superseded decision and narrow replacement
scope is the way to supersede it; this PROPOSED record does not.

## Proposed design

### Scope and wire

Stdlib-only extension; one `run_prompt` skill dispatches an immutable local runtime into a fixed workspace. Proposed CLI: `peerhub a2a serve` and
`peerhub a2a send --to <server>` (Review finding (ag)). No multi-tenancy, remote executable/argv/cwd/env selection,
remote-requested writable mode, resume, streaming, push, SubscribeToTask, multi-turn, ListTasks, extended cards, embedded TLS, scheduler,
failover or unrestricted shell API.

| Endpoint/method | Proposed contract |
|---|---|
| GET `/.well-known/agent-card.json` | One skill; declared tags remain untrusted claims |
| POST `/a2a`: SendMessage | Validated `run_prompt` data; returns `result.task` |
| GetTask / CancelTask | Caller-scoped Task; wrong caller gets not-found |

Freeze full card/skill/auth fields, `A2A-Version: 1.0`, `ROLE_USER`, envelopes, text artifact parts and unsupported-operation errors before
implementation (Review finding (cx); `peerhub/extensions/a2a_http.py:37–38,160–177`). SendMessage must honor `returnImmediately`, default false in A2A 1.0
§3.2.2: wait by default, or change the client to request async explicitly (Review finding (cx)). Every Task response echoes `history=[{"messageId":
message_id}]` for the existing lookup check (`peerhub/extensions/a2a_http.py:192–196`; Review finding (ag)). SDK acceptance is pending. CancelTask on a terminal task
returns that Task. TaskNotCancelable `-32002` is confirmed in A2A 1.0 in Review finding (cx), but the client raises `RemoteRpcError` for it
(`peerhub/extensions/a2a_http.py:106,177`; Review finding (ag)); verify this terminal-return policy during interop.

| Local state | A2A state |
|---|---|
| SUBMITTED / RUNNING | TASK_STATE_SUBMITTED / TASK_STATE_WORKING |
| COMPLETED | TASK_STATE_COMPLETED; bounded text artifact |
| Invalid accepted task / execution failure | TASK_STATE_REJECTED / TASK_STATE_FAILED |
| CANCELLED | TASK_STATE_CANCELED; only after observed termination |

Mappings follow the draft design and `peerhub/extensions/a2a_http.py:37–38`.

### Acceptance, identity and recovery

Derive a deterministic task ID from an unambiguously encoded caller/messageId tuple and a collision-resistant digest. A lost SendMessage reply
can then be reconciled with GetTask and history; the current client still needs the candidate remote task ID supplied (`peerhub/extensions/a2a_http.py:180–196`; Review finding (ag); Review finding (cx)). Not-found never authorizes re-execution. Same tuple/digest returns the same Task; changed payload conflicts. Do not use
ambiguous delimiter concatenation or a short unchecked hash.

Atomically persist caller/message binding, task ID, payload digest and exclusive execution claim before spawning or responding. Only the claim
winner launches. Durable tombstones survive lease expiry and task-output retention (Review finding (cx)). Use an extension `a2a_inbound_tasks` table, not
Records as the mutable task store. Extension tables need no Core schema bump: precedents `peerhub/extensions/bridge_claims.py:50` and `peerhub/extensions/approval.py:88` (Review finding (ag));
migration policy details remain **unverified**.

Pass `stream_id="a2a:<task_id>"` with locally controlled membership, never `peer:<id>:chat`: shared catch-up exposes history and two tasks for
one peer can raise ClaimHeldError (`peerhub/extensions/ask.py:71,80`; `peerhub/extensions/bridge_claims.py:127–128`; both reviews). Use a fixed local service author. Remote caller
identity is external provenance and authorization metadata only, never a registered Peer or local authority.

Persist task/execution binding and server ownership generation. On restart, fence late transitions, supervise/terminate orphan execution, mark
unresolved tasks FAILED with “server restarted; outcome unknown”, and never rerun them. Terminal state does not prove no side effects. Reconcile
process termination before claiming cancellation (Review finding (cx)); lease expiry must not erase duplicate suppression.

### Security, limits and audit

Require an isolated low-privilege account/container, allowlisted readable mounts and environment, separate provider home/credentials, controlled
tools/MCP/egress, and verified runtime restrictions. Reject providers without verified enforcement. `writable=False` alone supplies neither
confidentiality nor explicit-runtime policy.

Use per-caller high-entropy bearer tokens, digest storage, constant-time comparison, rotation/revocation, one bounded Authorization header,
uniform failures and redacted application/proxy logs. TLS-only remote ingress; successful-request quotas as well as auth throttling. Idempotency
is not bearer replay protection. Accept Tailscale Serve identity headers only behind a loopback-only backend with a Host allowlist and a trusted
proxy boundary; authorize callers explicitly and distrust generic forwarded headers/local spoofing (Review finding (cx)).

Reject duplicate Content-Length, Transfer-Encoding and Authorization headers, ambiguous framing, unexpected Host/path/content type and
unsupported transfer encoding. Strict JSON rejects duplicate keys/nonfinite values and unknown fields. Bound total header/body deadlines,
pre-auth connections/threads and audit growth; close rejected connections and test proxy/backend agreement (Review finding (cx)). Proposed caps: body 64
KiB, prompt 32 KiB, timeout 600 s, concurrency 2, bounded output; reject over-cap work without silently queueing. Append
bounded audit Records for acceptance/rejection/transitions/cancel with caller, messageId, digest and outcome; exclude tokens and confidential
output.

### Capability and selection

V1 uses static per-server configuration plus explicit `--to`; tags are declarations. NO status endpoint, observation ingestion or Router.
Relabeling a remote self-report as requester “measured” evidence violates Invariant 6 (Review finding (cx); Review finding (ag)). Both reviews cut that draft flow.
Optional future capability selection needs a separate trust/provenance decision; no GPU probe or measured-routing claim is accepted here.

## Blockers that must be solved first

| cx NO-GO blocker | Evidence and required resolution |
|---|---|
| Execution boundary; read-only is not confidentiality | Review finding (cx); `peerhub/extensions/adapters/base.py:111–120,164,206–215,317`: isolate reads/env/tools |
| Explicit runtime bypasses ask policy | Review finding (cx); `peerhub/extensions/ask.py:37–38,58–63`, `peerhub/extensions/adapters/base.py:453`: immutable restricted inbound runtime |
| Shared-stream exposure | Review finding (cx); `peerhub/extensions/ask.py:71,115–118`, `peerhub/extensions/adapters/base.py:432–435`: per-task streams |
| Identity quarantine at execution boundary | Review finding (cx); `peerhub/extensions/ask.py:69–70`: fixed local author, external provenance |
| Atomic acceptance | Review finding (cx); `peerhub/extensions/a2a_journal.py:137–149`, `peerhub/extensions/bridge_claims.py:126–129`: transaction/claim/tombstones |

## Reviewer verdicts

cx: **NO-GO** (Review finding (cx)); ag: **GO-WITH-CHANGES** (Review finding (ag)). They agree on stream isolation, reconciliation and cutting dynamic
status routing. ag sees a small server as viable after changes; cx requires proof of execution, identity and durable-acceptance boundaries first.
ag's read-only justification, short delimiter-based task hash and `curl -k` recipe are not adopted as security evidence. The proposed changes do
not close cx's blockers by documentation alone.

## Build only if

Require server-side concurrency caps, structured polling/cancel/artifacts, or non-SSH clients that SSH cannot adequately serve (Review finding (ag)).
Obtain a maintainer decision and resolve the blockers before either implementation slice.

| Requirement ID | PROPOSED only |
|---|---|
| REQ-A2A-S01 | Stdlib extension and bounded wire contract |
| REQ-A2A-S02 | Quarantined caller identity; fixed local authority |
| REQ-A2A-S03 | Verified isolated execution; no remote writable selection |
| REQ-A2A-S04 | Atomic idempotency, deterministic IDs and reconciliation |
| REQ-A2A-S05 | Restart fencing, orphan supervision and no re-execution |
| REQ-A2A-S06 | Auth, HTTP framing, concurrency and resource limits |
| REQ-A2A-S07 | Redacted, bounded audit and caller-scoped task access |
| REQ-A2A-S08 | No self-report promotion to measured evidence |
| REQ-A2A-S09 | Static explicit selection; selection never executes |

These IDs are proposals, not catalog entries or satisfied requirements. Do not change `requirements.m3.json` or the test catalog for this
documentation decision.

## Unverified

- a2a-sdk 1.2.x acceptance of the card and terminal-cancel interoperability.
- Extension migration policy details; ClaimStore lease versus restart rule.
- GPU probe fit in observation kinds (cut from v1).
- MCP characterization above.

## Two PR-sized slices, if authorized

1. Inbound serve, frozen card/auth/limits, isolated dispatch, extension task table,
   SendMessage/GetTask/CancelTask; loopback interop and acceptance/crash/framing/
   cancel fault tests. Resolve blockers before exposure.
2. Client send via HttpA2ATransport, explicit static configuration and async-mode
   choice, private-network docs and two-process interop. Maintain M3 traceability
   with each future contract-changing PR (Review finding (ag)).

Findings come from two independent read-only reviews (cx: security and protocol; ag: simplicity and operations) of a draft design on 2026-10-10; code references are relative to the peerhub package and were checked against version 0.13.0.
