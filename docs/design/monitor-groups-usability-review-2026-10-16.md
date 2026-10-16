# Monitor group usability and bounded sampling review (2026-10-16)

> **Historical decision, superseded for human rendering.** The initial review below
> approved colored headings with indented JSON as the grouped human view. Real CLI
> inspection showed this was not a visual dashboard. The current unanimous decision,
> including the corrected renderer, CLI contracts, compatibility policy, and RED-first
> plan, is in [Human-First Monitor Dashboard Redesign](monitor-human-dashboard-redesign-2026-10-16.md).

## Decision

The peer review converged on the following user contract:

1. Bare `peerhub monitor` uses the effective selected monitor groups in both human and JSON modes. Human mode shows each group heading and description followed by indented JSON data; `--view rich` colors headings and uses TTY redraw. This replaces the old compact human dashboard. `peerhub monitor --legacy` preserves the established ungrouped schema 1.1 dashboard for one compatibility release. Explicit `--group` already selects grouped schema 2 and continues to do so; `--legacy` cannot be combined with `--snapshot` or `--group`.
2. Keep the seven packaged defaults: `overview`, `execution`, `sessions`, `usage`, `context`, `capacity`, and `coverage`. `runtime` and `store` remain available on demand. The `sessions` group already exposes captured and configured profile/model/effort; `runtime` adds current-profile provenance and may read workspace configuration.
3. `execution` reports Bridge delivery certainty and measured elapsed time. A terminal delivery is not semantic provider/task success. PeerHub can expose evidenced certainty states (`not_started`, `may_have_started`, `started`, `terminal`) and explicit `NOT_EXECUTED` only where the evidence source records it. The current monitor projection does not classify task success or task error; those rates require typed outcomes plus a completeness-aware projection and are not part of this change.
4. `peerhub monitor groups` is the catalog discovery command. It lists every stable ID, its concise description, and whether it is selected by effective defaults; `--json` returns the same ordered catalog and existing `default_selected` field. On invalid preferences, human output also prints the reason code. The catalog is the single source for parser choices, help choices, and available IDs in monitor payloads.
5. Keep display bounds at three peers and twenty scopes per peer. Detect the fourth peer and twenty-first scope with existing lookahead rows, and mark `execution` and `coverage` partial/truncated whenever those bounds omit data.

## Dialectical review record

CC, CX, and AG reviewed the final proposal independently. All accepted items 1–5 after considering: schema 1.1 consumers may break when the default changes; `--legacy` and an explicit release note preserve a migration path; `runtime` overlaps session binding data and is opt-in; delivery certainty cannot establish task success; a fourth peer must not be silently omitted. The group catalog remains ordered and stable. This is a unanimous design decision for this change, not a claim that the old bare monitor format is permanently supported.

## TDD and implementation sequence

RED-first acceptance contracts:

- CLI watch: bare invocation selects the effective grouped defaults in TTY and pipe/JSON modes; human TTY output contains grouped headings/descriptions and redraws safely; workspace/global preferences apply; explicit `--group` overrides; `--legacy` returns schema 1.1 with its existing frame shape; `--legacy` plus snapshot/group is rejected. Tests that exercise legacy behavior pass `--legacy` explicitly.
- Catalog: human output lists all nine IDs, useful descriptions, defaults, and preference error reason when applicable; JSON IDs/order/descriptions/default selection match the canonical catalog; parser choices and `available_groups` derive from that catalog; catalog queries remain read-only and work without a store.
- Snapshot: remains one-shot and read-only, uses configured defaults unless groups are explicit, and is unaffected by the watch-mode default change.
- Sampling: three peers are complete; four peers return the first three and mark both `execution` and `coverage` partial with `truncated=true`; one peer with 21 scopes retains the existing partial behavior. Scope cap and peer cap share the same truthful coverage contract.
- Meaning: tests assert labels and documented wording describe delivery certainty. No test or output may call `terminal` task success or derive a task success rate.
- Docs: README documents the default view, `monitor groups`, `--snapshot`, default customization, `--legacy`, and the distinction between delivery certainty and task outcome.

Implementation keeps the existing schema-v2 grouped payload, current seven defaults, read-only snapshot boundary, provider refresh cadence, and DB schema. No new outcome projection or schema migration is introduced.

## User-facing group map

| ID | Contents | Default |
|---|---|---|
| `overview` | Cross-group peer summary and highest-priority state | Yes |
| `execution` | Bridge delivery certainty, reuse and measured delivery latency; not task success | Yes |
| `sessions` | Retained session lifecycle, fresh/reuse, configured profile/model/effort | Yes |
| `usage` | Provider-reported usage facts and reporting coverage | Yes |
| `context` | Runtime context occupancy evidence; may be unknown | Yes |
| `capacity` | Quota pools, remaining capacity, freshness and supported seven-day pace | Yes |
| `runtime` | Current profile provenance and adapter/runtime details | No |
| `coverage` | Completeness, stale/unknown evidence and sampling limits | Yes |
| `store` | Database schema and storage integrity summary | No |

## Validation

The focused monitor CLI and read-model suites are required after implementation; the hosted full suite must pass before merge. Provider live calls are not needed for the read-only grouping and bounded-query correction; watch tests stub quota refresh to verify routing/cadence without spending peer quota. The complete live G3 gate is still required for release.

The CLI contract inventory must also contain a behavioral proof for `monitor --legacy`, and the package release-notes test must bind `peerhub._version.__version__` to the matching changelog section. Collection/cadence option proofs use `--legacy` where they test the prior dashboard/refresh loop; default grouped routing is covered by the dedicated grouped watch tests.
