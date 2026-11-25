# API peers and credits

Applies to the API peers `ca` (Claude), `oa` (OpenAI) and `ga` (Gemini). Billing evidence is labelled: **measured** (by PeerHub, 2026-11-16, Windows, the stated tool versions), **reported** (by the maintainer, from a console screen, not reproduced by PeerHub) or **vendor** (the vendor's own documentation, not re-checked). A grant that a console displays is not proof that the API accepts it: *connected* is not *granted*, and *granted* is not *applied to a request*.

## Setup and first ask

Use these peers for one text-only ask paid from API credits. See the [installation table](../README.md#what-each-peer-needs) for official downloads and key setup.

- `ca` runs the `ant` CLI. Put it on PATH and sign in once with `ant auth login`. PeerHub leaves the login credential with `ant` and removes every `ANTHROPIC_*` variable from its child environment so the call uses the `ant` login and the official endpoint.
- `oa` reads the key from the environment variable `OPENAI_API_KEY` at the moment of the call. Set it in the environment of the process that runs PeerHub, for example from your secret store. There is no option for the key: never put it on a command line or in a file for PeerHub. The key is sent only in the `Authorization` header and is removed from any error text.
- `ga` reads the key from the environment variable `GEMINI_API_KEY` at the moment of the call, in the same way: there is no option for the key, so never put it on a command line or in a file for PeerHub. The key is sent only in the `x-goog-api-key` header, never in the URL, and is removed from any error text. `GOOGLE_API_KEY` is not read.
- CLI peer processes can inherit API keys from the environment. The `ca` child environment excludes `OPENAI_API_KEY`, `GEMINI_API_KEY` and the reserved `PEERHUB_BILLING_*` prefix. Account-bound cc and cx peers exclude `OPENAI_*` variables. Set API keys only where they are needed. See [account environments](accounts.md).

```powershell
peerhub ask ca --query-file review.md --profile ca.effort                        # its own stream: peer:ca:<request-id>
peerhub ask oa --query-file review.md --stream vote-oa-001 --profile oa.standard
peerhub ask ga --query-file review.md --stream vote-ga-001 --profile ga.standard
```

See [API peer canaries](../CONTRIBUTING.md#local-live-gate) for live validation.

## Reading the balance

PeerHub does not query providers for the remaining credit. Read it by hand:

| Provider | Where | Why PeerHub cannot |
| --- | --- | --- |
| Claude | https://platform.claude.com/settings/billing | **measured**: the cost and usage report endpoints answer 403 to the `ant auth login` login that `ca` uses (they need an admin key, and report spend, not the balance) |
| OpenAI | https://platform.openai.com/settings/organization/billing/overview | **measured**: `/v1/organization/costs` answers 403 to a project key (it needs the scope `api.usage.read`); the credit grants answer 403 to a key and are shown in the browser only |
| Gemini | https://aistudio.google.com/ (billing of the project) | no balance endpoint for a Gemini API key is known; **measured**: Cloud Billing answers 401 "API keys are not supported" |

A spend report is not a balance: do not compute the balance as "grant minus spend". Never probe the balance with paid requests.

## Stored billing observations

`peerhub diag --json` and `peerhub monitor --snapshot --json --group peers`
show the billing observations for each API peer. Each peer view shows up to
five recent observations per kind and indicates truncation. The time, source,
units and account scope describe each fact independently of any grant.
`MEASURED` evidence ages to `STALE` after 24 hours. Staleness leaves the
original evidence intact.

- API replies provide token usage. A timeout, uncertain execution, missing or
  partial usage, or an unverified account scope leaves an unreconciled
  observation. The delivery reference is a digest of the Bridge delivery ID.
  Any reported counters are retained. A proven pre-inference failure produces
  no billing usage fact.
- `oa` and `ga` fingerprint the exact key used for the call. A different key
  gets a different scope even when both keys draw on the same ledger. A replay
  keeps the original scope and observation. The managed `ca` login exposes no
  verified identity to PeerHub, so its call evidence has a null account scope.
  Asks recorded before scope capture have no billing observations.
- Trusted offline collectors can import a balance, estimate or charge with
  `peerhub.extensions.billing.record_amount`. The collector supplies the
  source (`provider_console` or `provider_statement` for a balance,
  `collector_estimate` for an estimate, `provider_statement` for a charge),
  a stable source evidence ID, source time as Unix seconds, and an integer
  amount in millionths of the stated three-letter currency. Build a
  `BillingScope` with `from_verified_identity` from the account or key identity
  of the reported fact. A credential that merely grants access to a report
  does not establish the identity of the reported account.
- Balance, estimate and charge observations remain separate from each other
  and from token usage. The consumer does no automatic reconciliation or
  pricing. An import of the same evidence is a no-op. A replay that changes
  the scope or contents is rejected. Keep the source evidence ID stable
  across credential changes.

The existing `core.db` observation tables retain one row per distinct fact
for the life of the store, allowing later replay and inspection of unresolved
evidence. The collector keeps credentials in process memory. PeerHub retains
fingerprints and normalized facts, without raw reports or credential material.

## Answers that mean "no inference ran because of the account"

PeerHub ends such an ask `not_executed` with the reason `quota_exhausted` and keeps the provider's message (`peerhub explain quota_exhausted`):

- `ga`: HTTP 429 or 402 with the status `RESOURCE_EXHAUSTED`. **Measured** on one key and project within one day, in this order: 429 for every pro-class model while the project had no paid quota; 402 "Your prepayment credits are depleted" after the project's billing was changed, then normal answers about an hour later; 429 "Your project has exceeded its monthly spending cap" (an account setting, changed at https://ai.studio/spend). Other 402 answers stay `uncertain`.
- `ca` and `oa`: see the provider responses under [Invocation and outcomes](#invocation-and-outcomes).

## Subscriptions and API credits are separate ledgers

- **Reported**: a chat subscription (Claude Ultra, ChatGPT Pro) shows an API credit entry in its billing page, but a monthly grant did not arrive for the maintainer at the time of writing (Claude: "Processing" with a date; OpenAI: only a small one-time grant). **Vendor**: the Claude support article on monthly API credits says the credit is claimed by linking an organization in the claude.ai billing settings, and that it does not cover interactive Claude Code.
- **Reported**: a Google Cloud free trial, a developer program grant and the AI Studio prepay balance are three separate ledgers; the free trial did not apply to the Gemini Developer API on the maintainer's screen.
- **Measured**: with an unusable `OPENAI_API_KEY` or `GEMINI_API_KEY` in the environment, the Codex and agy CLIs (logged in with accounts) still answered normally. This does not tell whether those CLIs ignore the keys or fall back to their account login.

## Not used by PeerHub

`ga` uses only the environment variable `GEMINI_API_KEY` (header `x-goog-api-key`). Google application default credentials, `gcloud` and OAuth scopes are not used, and PeerHub does not need them. **Reported**: an OAuth token without the Generative Language scope answers 403 `ACCESS_TOKEN_SCOPE_INSUFFICIENT` on that API; a 404 during a `gcloud` setup script was not diagnosed. Never put a key, a token or an ADC file into an issue, a log or a command line.

## Invocation and outcomes

The adapter implementation is [api.py](../peerhub/extensions/adapters/api.py).

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
  `oa.effort`, `oa.deepthink`, `ga.standard`, `ga.effort` in the packaged
  `model-defaults.toml`, without pools; `peerhub explain <profile>` shows each
  card. Every `ca`, `oa` and `ga` profile is tagged `measurement = "unmeasured"` (no recorded
  measurement of its model id; `peerhub profile list` shows it in `NOTES`). `ga` has no deep profile: a stronger model is used with `--model <id>`
  when the key has quota for it. The class attribute `profile_tiers` names the
  packaged tiers of a peer for the refusal message.

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

`oa` and `ga` share one HTTP exchange. It snapshots the key from the
environment for the invocation, refuses a key that cannot be a header
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
| 402 or 429 | `RESOURCE_EXHAUSTED` | `not_executed`, `quota_exhausted`, no reset time, the message kept |
| 400 | `INVALID_ARGUMENT` or `FAILED_PRECONDITION` | `failed_not_started` |
| 401 | `UNAUTHENTICATED` | `failed_not_started` |
| 403 | `PERMISSION_DENIED` | `failed_not_started` |
| 404 | `NOT_FOUND` | `failed_not_started` |
| 413 | any | `failed_not_started` |
| 400, 401, 403, 404, 413, 429 | missing | `failed_not_started` |
| every other pair | | `uncertain` |

With 429, `RESOURCE_EXHAUSTED` covers a rate limit and a spent or missing quota
alike; with 402 it is a depleted prepay balance (#384). No inference ran.
"Every other pair" includes an unknown status name, a pair that contradicts
itself (for example 500 with `RESOURCE_EXHAUSTED`), any 5xx, and 402 with
another status name or without one.
`error.status` is missing only when the `status` member is absent: a status
that is null or not a string is no listed pair.

## Dated observations

`ca.standard` answered a real call on 2026-11-15, and a real prompt whose first line was `@<path>` came back as the literal path, without the file's content. `oa` was checked against a local test server and without a key; a real reply through `oa` has not been recorded yet.

Measured on 2026-11-16 with one key, through PeerHub on a temporary store and
directly against the API:

- `ga.standard` (gemini-3.5-flash-lite) answered through PeerHub (9 input
  tokens, 1 output token).
- `ga.effort` (gemini-3.6-flash) answered 4 of 4 calls (1.6 s, 1.6 s, 3.5 s
  and 17.5 s). A fifth call through PeerHub at 01:46Z was delivered (9 input,
  112 output tokens). In the same minutes gemini-3.5-flash answered 4 of 4 (1.1 s,
  1.1 s, 4.1 s and 9.8 s).
- gemini-3.8-flash answered 1 of 10 tries between 00:29Z and 01:41Z: once in
  12.7 s; HTTP 503 `UNAVAILABLE` ("This model is currently experiencing high
  demand") twice; no answer within 40 to 90 s seven times, three of them
  through PeerHub, which ended `uncertain` by the timeout.
- gemini-3.7-flash answered HTTP 503 `UNAVAILABLE` once.
- That is why `ga.effort` is gemini-3.6-flash. It says which model answered for
  that key, not which model is better: no benchmark was run through the API,
  and every pro-class model answers 429 for that key, so the result may be
  particular to that key's tier. gemini-3.8-flash is used with
  `--model gemini-3.8-flash`.
- gemini-4-argon, gemini-4-carbon, gemini-pro-latest and gemini-3.1-pro-preview
  answered HTTP 429 `RESOURCE_EXHAUSTED` ("You exceeded your current quota,
  please check your plan and billing details") for that key. PeerHub ends such
  an ask `not_executed` with `quota_exhausted` and keeps the message. This
  status does not tell a rate limit from a key without quota for the model.
- On 2026-11-16 04:24Z, after the billing setup of the key's project changed,
  every model answered HTTP 402 `RESOURCE_EXHAUSTED` ("Your prepayment credits
  are depleted") in 0.3 to 0.4 s. 0.24.19 ended such an ask `uncertain`; from
  0.24.20 it ends `not_executed` with `quota_exhausted` and keeps the message
  (#384). The prepay balance is managed in Google AI Studio.
- gemini-2.5-flash and gemini-2.5-pro answered HTTP 404 `NOT_FOUND` ("no longer
  available to new users").
- An invalid key gave HTTP 401 `UNAUTHENTICATED` in a direct call (400
  `INVALID_ARGUMENT` is reported for other key forms).
- Without `GEMINI_API_KEY` the ask ended `failed_not_started`. A model name
  with `/` was refused before anything was sent. The key's bytes were not in
  the store.

Not measured: a rate limit as such, and a safety block.
