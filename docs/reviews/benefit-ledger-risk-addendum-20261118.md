# Additional PeerHub review: cost certainty, account provenance, and privacy

Date: 2026-11-18. Addendum to [benefit-ledger-hardcoding-audit-20261118.md](benefit-ledger-hardcoding-audit-20261118.md); context: #386, #258, #202/#203/#207, #209, #236, #381.

**Method:** read-only GitHub source inspection of main v0.24.23 and the existing review branch; no code execution/live provider calls/financial changes. Findings below are actual source observations plus explicit **design risks** that become relevant if a new credit collector or cost gate is implemented. The existing production system does NOT claim to provide monetary budget control.

## Confirmed technical evidence (additional to the original audit)

| ID | Source-backed observation | Inferred risk / disposition |
| --- | --- | --- |
| A1 | peerhub/extensions/adapters/api.py around 177-207: ApiRuntimeTarget.fingerprint returns adapter kind only; session_binding contains profile/model/effort/writable, no API organization or billing scope. API key is read from environment at call time in HttpRuntimeTarget._call around 390-407. | **P0 for any upcoming monetary ledger:** billing account/key may change out-of-band; a stored API delivery cannot safely be assigned to a specific billed organization from peer+model alone. **Not a current proven cross-billing defect.** Require explicit local account_scope identifier with source verification and reject unknown/mismatch. Do not persist a raw key or publish a key hash. |
| A2 | peerhub/extensions/adapters/api.py around 279-310: CaRuntimeTarget merges os.environ and _env_extra, strips ANTHROPIC_* and batch metadata, but does not globally allowlist all unrelated secrets such as OPENAI_ADMIN_KEY / GEMINI_API_KEY / GOOGLE_APPLICATION_CREDENTIALS. docs/accounts.md says bound peers scrub some vendor prefixes; unbound behavior remains unchanged. | **P0 security for any admin-key integration:** a new admin credential placed in the global parent environment may reach child provider processes not intended to receive it. Use per-collector secret injection and least privilege, or introduce tested subprocess env isolation only with compatibility gates. Existing sanitizer output redaction does not prevent inherited environment exposure. #381 already flags the general shared-process-key exposure. |
| A3 | peerhub/extensions/adapters/api.py around 385-430: HTTP call is a single worker thread joined against a deadline; on timeout the worker/socket may continue briefly and the outcome is TIMEOUT/UNCERTAIN with no usage receipt. Batch.py around 160-210 similarly classifies kills/timeouts as UNCERTAIN. | **P0 accounting honesty:** uncertain delivery or missing tokens does NOT prove zero usage/zero charges. Never debit zero; mark BILLING_UNRECONCILED. Reconcile later using billed provider time windows, IDs where actually available, with a no-double-count invariant. Preserve existing no-silent-retry safety. |
| A4 | peerhub/extensions/batch.py lines 20-29 exposes MAX_TASKS=256, MAX_PARALLEL=16; API peers have max_output_tokens=16000 and Gemini=32000 (adapters/api.py lines 174+, 498+). | **P1 future spending control:** tokens cap/concurrency cap are NOT a dollar budget. Concurrent API tasks may burn promotional credit before delayed cost reports reflect it. For an opt-in cash budget, bound tasks/parallelism and per-request predicted cost conservatively; coordinate reservations atomically across processes, release or reconcile on completion and uncertain state. This is a future design, not a report that existing batch violates its own promises. #207 already discusses budget units and concurrent admissions. |
| A5 | peerhub/extensions/adapters/api.py _ga_usage reads promptTokenCount, candidatesTokenCount, thoughtsTokenCount but normalizes only input_tokens/output_tokens; peerhub/extensions/usage_normalization.py maps native counts. | **P1 estimate accuracy:** no authoritative billable cost, cache price, tier, tool-specific line item or promotional application can be reconstructed from normalized two-field usage alone. Monetary estimates must carry pricing_version + coverage/unknown and never be relabeled as observed charge. |
| A6 | peerhub/extensions/observation_model.py has measured/absent/unknown/stale provenance, times, and immutable Observation; docs/STATUS.md has deferred DB/schema expansion and #236 unbounded A2A data. | **P1 data retention:** do not store private billing ledger/purchase history in a public repo, generic monitor log, core Stream transcript or indefinitely growing observation store by default. Prefer local opt-in sidecar with retention/expiry, encryption/OS ACL guidance, export scrub and backup policy, atomic write/migration test. New Observation kind only after schema/privacy review. |
| A7 | docs/STATUS.md v0.24.23 explicitly defers #202 cost_sensitivity, #203 zero-quota routing and #207 closed-loop router until owner-approved consumer. | **P1 integration gate:** Benefit Ledger must remain advisory/read-only; do not activate automatic cheapest-model routing or credit-aware failover under this review. Existing closed design issues already own the routing semantics; avoid duplicate implementation threads. |
| A8 | Existing review branch differs from current main by one commit (base v0.24.22 → main v0.24.23). | **P2 change hygiene:** branch doc remains readable; do not assume it is current code state. Before any PR/implementation, recompare main and refresh evidence. No automatic merge/rebase required for this review. |

## MECE benefit state model correction

The first audit's single enum mixes stages ('CONNECTED_NOT_GRANTED', 'GRANTED_NOT_APPLIED', 'ACTIVE') with health ('STALE', 'ERROR'). For the next design, avoid mutually exclusive states that falsely collapse independent dimensions:

- eligibility: ELIGIBLE / INELIGIBLE / UNKNOWN
- claim: NOT_CLAIMED / CLAIMED / NOT_REQUIRED / UNKNOWN
- organization_link: CONNECTED / DISCONNECTED / UNKNOWN
- grant_issue: ISSUED / PENDING / REJECTED / UNKNOWN
- cost_application: APPLIED / NOT_APPLIED / UNRECONCILED / NOT_APPLICABLE
- observation_health: MEASURED / STALE / ERROR / UNKNOWN
- lifecycle timestamps: starts_at, expires_at, next_expected_at and last_confirmed_at, each with provider scope/source.

Actual balance and estimated balance must be separate measurements; a posted $5 grant and $200 monthly benefit are not interchangeable. Eligibility and billing period cannot be inferred from plan name or a single date. Deduplicate repeated screenshots, email notices and provider reports by immutable grant/event ID where provided; if absent, use tentative matching without collapsing unrelated promotions.

## Negative / adversarial test matrix (before implementation)

1. Switch API key/org between tasks, retry after replay, and create another account-bound peer. A cost observation with unknown scope stays UNKNOWN, never auto-assigned to the default account.
2. Run 16 parallel API jobs against one locally estimated budget: acceptance must be atomic or conservative and report possible budget overrun when another client uses the same billing account.
3. Provider HTTP timeout (and lingering request), kill after provider may have started, malformed usage, truncated body, duplicated receipt. Never record $0 actual spend just because local usage is missing.
4. Existing grant expires during a monthly cycle; grant is prorated, rolled into a new cycle, reversed/refunded or posted late; cross-currency FX and taxes. No duplicate or negative 'remaining' inferred from simple grant-minus-spend.
5. Same Google AI entitlement visible in AI Studio and Developer Program, but only one grant issued. Do not double count across provider UI pages or GCP credits export.
6. A policy document is outdated, country-ineligible or retired. No automatic user entitlement claim. Dates and links must have source+verification time.
7. Inject admin keys into a collector and child environment; assert no unrelated process receives the secret; stdout/stderr/trace/dump redaction plus no disclosure via URL parameters.
8. Read-only monitor displayed 100 times: no paid model call, no hidden login/probe, bounded writes and stable source timestamp; UNKNOWN/STale not translated to zero.
9. Multiple overlapping grant sources and money units (USD, KRW, compute credits, learning credits, quota percent). Aggregates only within an explicitly compatible resource scope and currency, never an arbitrary single 'total credits' number.

## Proposed priority and stop rule

**P0 (future integration gate)**: A1 account provenance, A2 admin-secret isolation, A3 uncertain billing vs execution. These are prerequisites only if new billing/money collection is approved; existing CLI behavior is not labeled broken solely because that functionality is missing.

**P1**: A4 conservative spend caps with concurrency, A5 pricing evidence/coverage, A6 privacy-retention, A7 router feature gate. **P2**: A8 branch refresh hygiene.

Keep the simplest first consumer: manually enter a dated, redacted grant status and show source/expiry without modifying default monitor. No implementation until a user story, permissions, and a test oracle are agreed. Do not merge main without explicit approval.
