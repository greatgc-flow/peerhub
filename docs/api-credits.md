# API credits: what can be read, and what the answers mean

Applies to the API peers `ca` (Claude), `oa` (OpenAI) and `ga` (Gemini). Each statement is labelled: **measured** (by PeerHub, 2026-11-16, Windows, the stated tool versions), **reported** (by the maintainer, from a console screen, not reproduced by PeerHub) or **vendor** (the vendor's own documentation, not re-checked). A grant that a console displays is not proof that the API accepts it: *connected* is not *granted*, and *granted* is not *applied to a request*.

## Reading the balance

PeerHub cannot read the remaining credit of any of the three. Read it by hand:

| Provider | Where | Why PeerHub cannot |
| --- | --- | --- |
| Claude | https://platform.claude.com/settings/billing | **measured**: the cost and usage report endpoints answer 403 to the `ant auth login` login that `ca` uses (they need an admin key, and report spend, not the balance) |
| OpenAI | https://platform.openai.com/settings/organization/billing/overview | **measured**: `/v1/organization/costs` answers 403 to a project key (it needs the scope `api.usage.read`); the credit grants answer 403 to a key and are shown in the browser only |
| Gemini | https://aistudio.google.com/ (billing of the project) | no balance endpoint for a Gemini API key is known; **measured**: Cloud Billing answers 401 "API keys are not supported" |

A spend report is not a balance: do not compute the balance as "grant minus spend". Never probe the balance with paid requests.

## Answers that mean "no inference ran because of the account"

PeerHub ends such an ask `not_executed` with the reason `quota_exhausted` and keeps the provider's message (`peerhub explain quota_exhausted`):

- `ga`: HTTP 429 or 402 with the status `RESOURCE_EXHAUSTED`. **Measured** on one key and project within one day, in this order: 429 for every pro-class model while the project had no paid quota; 402 "Your prepayment credits are depleted" after the project's billing was changed, then normal answers about an hour later; 429 "Your project has exceeded its monthly spending cap" (an account setting, changed at https://ai.studio/spend). Other 402 answers stay `uncertain`.
- `ca` and `oa`: see the outcome tables in [adapters](adapters/README.md) and the README section "API peers".

## Subscriptions and API credits are separate ledgers

- **Reported**: a chat subscription (Claude Ultra, ChatGPT Pro) shows an API credit entry in its billing page, but a monthly grant did not arrive for the maintainer at the time of writing (Claude: "Processing" with a date; OpenAI: only a small one-time grant). **Vendor**: the Claude support article on monthly API credits says the credit is claimed by linking an organization in the claude.ai billing settings, and that it does not cover interactive Claude Code.
- **Reported**: a Google Cloud free trial, a developer program grant and the AI Studio prepay balance are three separate ledgers; the free trial did not apply to the Gemini Developer API on the maintainer's screen.
- **Measured**: with an unusable `OPENAI_API_KEY` or `GEMINI_API_KEY` in the environment, the Codex and agy CLIs (logged in with accounts) still answered normally. This does not tell whether those CLIs ignore the keys or fall back to their account login.

## Not used by PeerHub

`ga` uses only the environment variable `GEMINI_API_KEY` (header `x-goog-api-key`). Google application default credentials, `gcloud` and OAuth scopes are not used, and PeerHub does not need them. **Reported**: an OAuth token without the Generative Language scope answers 403 `ACCESS_TOKEN_SCOPE_INSUFFICIENT` on that API; a 404 during a `gcloud` setup script was not diagnosed. Never put a key, a token or an ADC file into an issue, a log or a command line.
