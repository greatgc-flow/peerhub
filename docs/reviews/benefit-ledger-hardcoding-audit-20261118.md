# PeerHub: benefit inventory & hardcoding audit — 2026-11-18

> Source: current default-branch docs/api-credits.md, peerhub/extensions/quota_probes.py, quota_capture.py, adapters/api.py, cli/monitor_view.py, config_data/model-defaults.toml, docs/accounts.md, docs/STATUS.md; current official vendor docs. This is a **read-only review** — no changes to code, runtime, cloud, account or billing. Repo is public; no personal account IDs, credentials, private screenshots, receipt or support IDs included.

## Executive judgment

- Retain the existing quota-monitor architecture. API money, paid balance, promotion grant, subscription weekly quota, reset coupon, GCP free trial, G1 credits, Google Skills credits and Colab compute units are **separate resource kinds**.
- Add a **manual-first, opt-in benefit ledger as an extension** only if a real consumer approves; no new Core objects and no automatic paid model calls.
- Avoid hardcoding subscription amounts, country eligibility, expiration and discontinued products into Python. These are dated provider policies, not timeless facts.
- Do **not** make fixed API URLs arbitrarily configurable in response to this audit: host pinning, strict model-path validation and no redirects are security boundaries.

## Evidence-based code review

| Source reference | Finding | Severity / action |
| --- | --- | --- |
| docs/api-credits.md lines 5-15, 24-32 | Honest as-is: no wallet balance accessible by ca/oa/ga current credentials; connected != issued != applied. | Preserve and link new catalogue. |
| peerhub/extensions/quota_capture.py lines 156-175 | refresh_quota explicitly only cx/cc/ag; ca/oa/ga intentionally have no quota pool. | Preserve. Do not fake a 7D quota for API money. |
| peerhub/extensions/quota_probes.py lines 20-25, 383-430 | AGY_QUOTA_FAMILIES, _AGY_GROUP_PREFIX_MAP and registered IDs are Python constants. Unknown IDs already fail closed as drift. | P1: provider-schema drift fixtures; consider versioned data mapping without unsafe fallback. |
| peerhub/extensions/quota_probes.py lines 72-76, 241-309 | Claude /usage parser recognizes literal English headings and fixed 5h/168h periods; unknown sections treated as drift. | P1: renamed/translated/new-window fixtures; UNKNOWN not zero. |
| peerhub/config_data/model-defaults.toml lines 1-16 and 184-280 | Model profiles deliberately pinned, override precedence documented, source/checked timestamps present. | Not a defect: add expiry/model-retirement checks rather than blanket removal of pins. |
| peerhub/extensions/adapters/api.py lines 45-62, 440-510 | API endpoints and model patterns fixed for security; output ceilings 16k/32k. | Keep hosts pinned; P1 consider separate opt-in max-spend guard for API calls with verified pricing. |
| peerhub/cli/monitor_view.py lines 16-20 | _NOT_REPORTED, _DISABLED, _LEAD_MARK_PCT intentionally mirror other modules. | P2: parity tests or dependency-neutral shared contract; avoid accidental drift. |
| peerhub/extensions/diag_quota.py lines 21-24 | 72h credit-expiry warning. | P3: intentional policy threshold; configure only if needed. |
| docs/accounts.md lines 53-55 | Account-bound peers do not have measured quotas. | P1: never assign default-account usage/grants to bound account. |
| docs/STATUS.md lines 7-18 | Current source v0.24.22; #386 was reflected in docs/api-credits.md and closed at v0.24.21. | New enhancement scope should be tracked separately, #386 kept as background. |

## Benefits review and corrections to previous conversation

| Claim | Classification and correction | Source |
| --- | --- | --- |
| OpenAI Pro 200 $200/month API credits | Eligible plan, claim after 7 paid days, linked organization; does not expand ChatGPT/Codex plan quotas. Grant issuance must be observed. | https://help.openai.com/en/articles/20001626-api-credits-benefit-for-eligible-chatgpt-subscriptions |
| Claude Ultra $200/month API credits | Link Console org; initial payout is not proven by Connected/Processing; not interactive Claude Code extra usage. | https://support.claude.com/en/articles/17154008-monthly-api-credits-for-max-ultra-and-team-plans |
| Google AI Pro GDP Cloud credit | Monthly plan-dependent credit; requires eligible linked billing account, distinct from AI Studio prepay/GCP Trial. | https://developers.google.com/program/plans-and-pricing |
| Google Colab 200 CCU | Not guaranteed for all countries/accounts: official supported-country list inspected does NOT list Republic of Korea. Do not recommend as guaranteed user benefit. | https://support.google.com/googleone/answer/14534406?hl=ko |
| Google Skills 35/month | Available through GEAR enrollment and same Skills login; not automatic by virtue of paid Pro. | https://developers.google.com/program/gear?hl=ko |
| Firebase Studio 30 workspaces | **Retired recommendation.** New workspace/signup disabled 2026-06-22; product sunsets 2027-03-22. Older GDP FAQ card is stale. Recommend migration, not new workspaces. | https://firebase.google.com/docs/studio/migrating-project |
| Google AI Pro storage/family sharing | Plan may provide 5TB (or variant 10TB), share with up to 5 others; verify actual Google One account plan and regional terms. Not API money. | https://support.google.com/googleone/answer/9004013 |
| Three vendors' Batch API | Pricing optimization (typically ~50% discount for supported async workloads), NOT an additional credit grant. Must use independent latency-tolerant requests and preserve result provenance. | https://platform.claude.com/docs/en/build-with-claude/batch-processing |

## Suggested data contract: benefits observation is separate from quota

- identity: provider, account_slot, opaque local org/project/billing scope, benefit_id; never publicize raw identifiers.
- type: API_PLATFORM_CREDIT, PAID_PREPAY, PROMO_CREDIT, SUBSCRIPTION_RATE_LIMIT, BANKED_RESET, G1_OVERAGE, CLOUD_FREE_TRIAL, COMPUTE_UNIT, TRAINING_CREDIT, PRICE_DISCOUNT, RETIRED_FEATURE.
- value: native unit and currency; actual_balance_ui vs estimated_balance vs spend_api vs grant_amount; no fungibility or cross-unit sum.
- lifecycle: CLAIMABLE, CONNECTED_NOT_GRANTED, GRANTED_NOT_APPLIED, ACTIVE, EXPIRED, INELIGIBLE, RETIRED, UNKNOWN, STALE, ERROR.
- provenance: source_kind, source_link, observed_at, checked_at, expires_at, account scope, policy country/plan eligibility, confidence and evidence. Missing != zero.
- secrets: no browser-token scraping, no credentials in logs, no automatic purchase/claim or paid model requests to probe wallet.

## Implementation scope & acceptance (future only)

**P0** Add a policy/evidence catalogue in a declarative versioned file, with per-field provenance/freshness and discontinued product state; manual snapshot schema and fail-closed fixture tests (unit mismatch, org mismatch, stale grant, unknown plan, region ineligible).

**P1** Test provider quota parser drift (new Claude section/AG pool) without inventing percentages; verify pinned model profiles against actual installed CLI provider data at explicit refresh. Optional official read-only spend collectors later, using opt-in admin credentials. Billing export applied credits != remaining grants.

**P2** Only if demand demonstrated, an optional benefits monitor group/command with verified balance age, claim link, expiry action and clear UNKNOWN. Keep compact monitor and existing quota observations unchanged by default.

**Gates:** no main merge, no runtime behavior change in this documentation branch, no cloud resource creation, no key capture, no API billing charges, no disabled host safety. Tests must distinguish scope, money, units, states and provider-specific lifecycle.

## Follow-up

Keep #386 for context. If an owner-approved implementation is requested, create a narrowly scoped follow-up issue and avoid reopening completed #258/#386 for unrelated functionality. No testing or implementation was claimed in this review.