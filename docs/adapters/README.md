# docs/adapters/ — Index

This directory documents the current peer CLI adapters supported by PeerHub and
their invocation and decoding contracts. Runtime truth is jointly defined by the
adapter source, packaged model defaults, the model profile manifest,
and reviewed observations; no document contains a second independent
configuration surface.

## 1. Active Peer Adapters

| Peer | Adapter Doc | Target Peer Binary |
|---|---|---|
| **ag** | [ag.md](ag.md) | `agy.exe` (Antigravity CLI) |
| **cc** | [cc.md](cc.md) | `claude.cmd` (Claude Code) |
| **cx** | [cx.md](cx.md) | `codex.cmd` (Codex CLI) |
| **ca** | [API peers](#4-api-peers-ca-oa-and-ga) | `ant` (Claude API CLI) |
| **oa** | [API peers](#4-api-peers-ca-oa-and-ga) | none: one HTTPS request to the OpenAI Responses API |
| **ga** | [API peers](#4-api-peers-ca-oa-and-ga) | none: one HTTPS request to the Gemini API |

Ordinary `peerhub ask` calls reuse the latest compatible retained native session for the
peer/Stream. Otherwise the Bridge creates a fresh generation with bounded
catch-up. Mutually exclusive `--fresh` / `--session HANDLE` allocate a new
generation or reuse one exact compatible retained session. Exact rejection
never substitutes another session. Fresh still receives Stream context; use a
dedicated Stream for an independent task. The API peers `ca`, `oa` and `ga` have
no retained session: one ask per Stream (section 4).

## 2. CLI Reference & Contracts

- [peer-cli-reference.md](peer-cli-reference.md) — Comprehensive reference of supported flags, subcommands, and invocation patterns across all three peers.

## 3. Historical Migration Checkpoints

Location: [checkpoints/](checkpoints/)

Contains verbatim reference snapshots ported from Engram during the 2026-09-03 separation:
- `agy.md` — Antigravity CLI known bugs & update checkpoint snapshot
- `cc.md` — Claude Code known bugs & update checkpoint snapshot
- `codex.md` — Codex CLI known bugs & update checkpoint snapshot

> Files under `checkpoints/` contain historical paths and workflow descriptions
> from the pre-separation repository. They are preserved for provenance and must
> not be used as current operational guidance.

## 4. API peers: ca, oa and ga

`ca` (Claude API) and `oa` (OpenAI Responses API) were added in 0.24.18 (#378),
`ga` (Gemini API) in 0.24.19 (#381).
They are text-only peers paid from API credits; they have no quota pool, no
observed CLI version and no retained session. The source is
`peerhub/extensions/adapters/api.py`; setup and outcomes for users are in the
[README](../../README.md#api-peers-ca-oa-and-ga). The live canaries of `ca`,
`oa` and `ga` (canary stage only; they spend API credits) are described in
[CONTRIBUTING](../../CONTRIBUTING.md#local-live-gate).

Shared contract:

- One ask is one API call, never retried. No tools, no files, no workspace
  access (the workspace must exist but is not read), no session.
- One ask per Stream: any other record on the Stream (another ask in any state,
  or a record put there some other way, which would be delivered first as one
  more API call) refuses the ask before anything is stored or sent
  (`request_rejected`: `ca is stateless; use a new stream`). Only the same ask
  may come again: the same request-id AND the same author (`--author-peer`)
  replay its stored result, because a request-id is unique per Stream and
  author; another author with that request-id is refused. The default Stream is
  `peer:<peer>:<request-id>` (other peers: `peer:<peer>:chat`), so an ask
  without `--stream` gets its own Stream; the text output prints it as a
  `stream:` line before the `session:` line.
- Also refused with `request_rejected` (exit 2): `--writable`, `--effort`, no
  `--profile` and no `--model`, and a model that does not match
  `^claude-[a-z0-9.-]{1,64}$` (`ca`), `^gpt-[a-z0-9.-]{1,64}$` (`oa`) or
  `^gemini-[a-z0-9.-]{1,64}$` (`ga`). The whole value must match.
- Inline prompt limit: 1,000,000 UTF-8 bytes. Reply limit: 16,000 output tokens
  for `ca` and `oa`, 32,000 for `ga`. It is the class attribute
  `max_output_tokens`: the request and the `truncated` message read it.
- A complete reply is `delivered`; usage keeps `input_tokens` and
  `output_tokens` (telemetry source `ca.message.usage`, `oa.response.usage` or
  `ga.response.usage`).
  A reply cut at the token limit is `truncated`; a reply that ended for another
  reason is `incomplete`. Both are STARTED, exit 1, keep the text in
  `partial_response` and store no response Record (`peerhub explain truncated`,
  `peerhub explain incomplete`). The Bridge settles them like a refusal; if an
  ask is interrupted after the result was stored but before it was
  acknowledged, the same request finishes that settlement and returns the saved
  status with `partial_response`, without calling the provider again.
- Credits used up: `not_executed` with `reason: quota_exhausted` and no reset
  time. For `ca` and `oa`, an HTTP 400, 401, 403, 404, 413 or 429 answer with
  an error object: `failed_not_started` (no inference); `ga` has its own table
  (below). Anything else: `uncertain`, including a reply of the wrong shape
  (see below).
- Profiles: `ca.standard`, `ca.effort`, `ca.deepthink`, `oa.standard`,
  `oa.effort`, `oa.deepthink`, `ga.standard`, `ga.effort`, `ga.deepthink` in
  the packaged `model-defaults.toml`, without pools; `peerhub explain <profile>`
  shows each card.
- The credit balance is not shown: no API gives it to the login or key that
  PeerHub uses. No usage total is built either (see the CHANGELOG of 0.24.19).

`ca` invocation is equivalent to:

```text
ant messages create --format json   (request body as JSON on stdin)
```

The body has the model, `max_tokens` 16,000 and one user message whose content
is the fixed line `PeerHub ask (text only, no tools, no files).`, an empty line
and the prompt. `ant` replaces a string that starts with `@` by the content of
that file; the fixed first line keeps a prompt that starts with `@` literal.
Every `ANTHROPIC_*` variable is removed from the child environment, so `ant`
uses its own login (`ant auth login`) and the official endpoint. A reply is
complete only with exit code 0, one JSON object of type `message` and a
`stop_reason` of `end_turn` or `stop_sequence` (empty text is `delivered` with
`empty_response`); `max_tokens` is `truncated`, any other stop reason is
`incomplete`. `content` must be a list of objects and the `text` of a `text`
block a string; any other shape is never a reply and ends `uncertain`. The text
"credit balance is too low" in an error with HTTP 400, 401, 403, 404 or 413
means the credits are used up. Without a valid login the API answers HTTP 401
`authentication_error` (measured with `ant` 2.9.0: `x-api-key header is
required`); the error then reads `ant is not signed in or its login expired
(run: ant auth login): HTTP 401: authentication_error: ...` and the ask is
`failed_not_started`. Output without an HTTP status and an error object ends
`uncertain`.

`oa` and `ga` share one HTTP exchange (`HttpRuntimeTarget._call`). It reads the
key from the environment at the call, refuses a key that cannot be a header
value (`failed_not_started`), adds the key to the sanitizer, and sends one POST
with the standard library. Proxy variables are ignored, redirects are not
followed, and TLS is verified with the default context; a network that needs a
proxy is not supported. `--timeout-seconds` is a total over the whole call
(connect, headers, body): the request runs in a worker thread and the ask waits
for it at most that long, then ends `uncertain` (`runtime_timeout` evidence; a
late answer is discarded). `--silence-timeout-seconds`, when given, limits one
socket operation. Each of the two peers adds three hooks only: the name of the
key variable (`key_env`), the request (`_request`) and the outcome of one HTTP
answer (`_classify`).

`oa` sends its POST to the fixed URL `https://api.openai.com/v1/responses` with
`{"model", "input", "max_output_tokens": 16000, "store": false}` and the key
from `OPENAI_API_KEY` in the `Authorization` header only. `OPENAI_BASE_URL` is
ignored. A reply is complete only with HTTP 200 and `status: completed` (empty
`output_text` is `delivered` with `empty_response`); `incomplete` with the
reason `max_output_tokens` is `truncated`, any other status or reason is
`incomplete`. `output` must be a list of objects with a `type`, the `content`
of a `message` item a list of objects and the `text` of an `output_text` block a
string; any other shape ends `uncertain`, and so does an answer that is
`completed` but carries an `error` object. In an error answer (an HTTP status other
than 200 with an error object) the error codes `credit_balance_exhausted` and
`insufficient_quota` mean the credits are used up, whatever that status is. At
HTTP 200 an error code is never read: only the status and the shape of the
response decide.

`ga` sends its POST to
`https://generativelanguage.googleapis.com/v1beta/models/<model>:generateContent`
with `{"contents": [{"role": "user", "parts": [{"text": <prompt>}]}],
"generationConfig": {"maxOutputTokens": 32000}}` and the key from
`GEMINI_API_KEY` in the `x-goog-api-key` header only, never in the URL. The
host is fixed. The model is one path segment of the URL, and the model pattern
keeps `/`, `:`, `?`, `#`, `%`, whitespace and uppercase out of it. Thinking
counts against `maxOutputTokens`, which is why the limit is 32,000.

At HTTP 200 only the shape and the finish reason decide, in this order:

1. The body is not one JSON object, or it has an `error` that is not null:
   `uncertain`.
2. `candidates` is missing or an empty list, and `promptFeedback.blockReason`
   is a string: `incomplete` with that reason. `candidates` missing, empty or
   not a list otherwise: `uncertain`.
3. The first candidate is not an object, or its `finishReason` is not a string:
   `uncertain`.
4. `content` is present and not an object, `parts` is present and not a list, a
   part is not an object, or a part's `text` is present and not a string:
   `uncertain`.
5. The reply text is the joined `text` of the parts without `thought: true`.
   A missing `content` or missing `parts` is no text.
6. `finishReason` `STOP` is complete (empty text is `delivered` with
   `empty_response`, so a reply that only thinks is an empty reply);
   `MAX_TOKENS` is `truncated`; any other finish reason is `incomplete` with
   that reason. The text is kept in both cases.

Usage is measured only when `usageMetadata` is an object, `promptTokenCount` is
an integer of 0 or more, and `candidatesTokenCount` and `thoughtsTokenCount`,
when present, are integers of 0 or more (true and false do not count as
integers). Then `input_tokens` is `promptTokenCount` and `output_tokens` is
`candidatesTokenCount` plus `thoughtsTokenCount`; only these two count as 0
when missing, and every other key is ignored. Otherwise the usage is absent,
never zero.

An error answer is an HTTP status other than 200 with an `error` object. The
pair of the HTTP status and `error.status` decides:

| HTTP status | `error.status` | Outcome |
|---|---|---|
| 429 | `RESOURCE_EXHAUSTED` | `not_executed`, `quota_exhausted`, no reset time, the message kept |
| 400 | `INVALID_ARGUMENT` or `FAILED_PRECONDITION` | `failed_not_started` |
| 401 | `UNAUTHENTICATED` | `failed_not_started` |
| 403 | `PERMISSION_DENIED` | `failed_not_started` |
| 404 | `NOT_FOUND` | `failed_not_started` |
| 413 | any | `failed_not_started` |
| 400, 401, 403, 404, 413, 429 | missing | `failed_not_started` |
| every other pair | | `uncertain` |

`RESOURCE_EXHAUSTED` covers a rate limit and a spent or missing quota alike; no
inference ran. "Every other pair" includes an unknown status name, a pair that
contradicts itself (for example 500 with `RESOURCE_EXHAUSTED`) and any 5xx.
`error.status` is missing only when the key is absent: a status that is null or
not a string is no listed pair. Measured on 2026-11-16 with direct API calls:
an unknown model gave 404 `NOT_FOUND`, an invalid key 401 `UNAUTHENTICATED`
(400 `INVALID_ARGUMENT` is reported for other key forms), and a model without
quota for the key 429 `RESOURCE_EXHAUSTED`. Not measured: a rate limit, a
safety block and a 5xx answer.
