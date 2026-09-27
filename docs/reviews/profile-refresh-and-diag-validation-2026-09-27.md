# Profile refresh and diagnostic validation — 2026-09-27

## Scope and runtime evidence

The production adapters, packaged model defaults, live CLI catalogs, minimal
dispatches, model-status output, and diagnostic output were checked together.

- Codex CLI 0.157.1: `codex debug models`
- Claude Code 2.1.283: current command surface plus live minimal invocations
- Antigravity CLI: `agy.exe models` plus live minimal invocations and backend
  model-override logs
- peerhub 0.7.0 reinstalled from this checkout before the live checks

## Active profile matrix

| Profile | Resolved model | Effort | Live ping |
|---|---|---|---|
| `ag.standard` | `gemini-3.8-flash-low` | embedded | `SUCCEEDED_VERIFIED` |
| `ag.effort` | `gemini-3.8-flash-high` | embedded | `SUCCEEDED_VERIFIED` |
| `ag.deepthink` | `gemini-3.1-pro-high` | embedded | `SUCCEEDED_VERIFIED` |
| `cc.standard` | `claude-haiku-4-5-20251001` | model default | `SUCCEEDED_VERIFIED` |
| `cc.effort` | `claude-sonnet-5` | `high` | `SUCCEEDED_VERIFIED` |
| `cc.deepthink` | `claude-opus-5` | `high` | `SUCCEEDED_VERIFIED` |
| `cx.standard` | `gpt-6-luna` | `low` | `SUCCEEDED_VERIFIED` |
| `cx.effort` | `gpt-6-sol` | `high` | `SUCCEEDED_VERIFIED` |
| `cx.deepthink` | `gpt-6-astra` | `xhigh` | `SUCCEEDED_VERIFIED` |

Every ping used `peerhub ask` with an explicit profile pin and returned exactly
`PONG` (AG includes its normal trailing newline), exit code 0, terminal
execution certainty, and no normalized error code.

The execution records independently confirmed all three Codex model/effort
pairs and all three Claude model/effort pairs. Antigravity backend logs recorded
the expected display labels for each configured slug:

- `gemini-3.8-flash-low` -> `Gemini 3.8 Flash (Low)`
- `gemini-3.8-flash-high` -> `Gemini 3.8 Flash (High)`
- `gemini-3.1-pro-high` -> `Gemini 3.1 Pro (High)`

The Antigravity catalog also lists other models. They are catalog entries, not
profiles advertised by peerhub's production adapter.

## Corrections applied

1. All nine advertised profiles now have explicit packaged defaults. The
   wildcard remains only for custom or future adapters.
2. The Claude adapter now emits the configured `--effort` operand for profiles
   that advertise effort support.
3. `node model-status` uses the same layered model resolver as dispatch and
   reports `model_source`; unbound profiles no longer appear with blank models.
4. `diag` resolves current `.engram` runtime homes through `CODEX_HOME`,
   `CLAUDE_CONFIG_DIR`, and `GEMINI_DIR` when no workspace-local legacy `_sys`
   tree exists.
5. Missing Codex/Claude context is no longer fabricated as `15k / 258k` or
   `0k / 1M`. Status input older than 24 hours is excluded.
6. The non-advertised, stale `ag.opus (Claude 3.7)` routing row was removed.
   Routing rows resolve their model, effort, and source from the model SSOT.
7. A failover recommendation is emitted only when both fresh quota and context
   evidence support a numeric headroom calculation.

## Diagnostic cross-check

Fresh and cached diagnostics agreed after the pings:

- No current quota projection was available, so `has_data=false`, quota and
  headroom remain `--`, and `failover_profile=null`.
- Fresh Claude and Codex context observations remain visible.
- The stale AG status input is not presented as current telemetry.
- Full headroom output reports one successful definitive attempt and zero
  failures for each of the nine profiles.
- Workspace status reports 36 schema migrations, zero active leases, and
  `Status: OK`.

`UNKNOWN`/`PROBING` health remains an honest health-projection state; a
successful dispatch is recorded separately as 24-hour reliability evidence and
is not rewritten into a fabricated health measurement.

## Regression validation

- Targeted profile/model/telemetry tests: 131 passed, 1 deselected
- Full suite: 2,378 passed, 7 skipped, 15 deselected, 13 subtests passed
- pyright: 0 errors, 0 warnings, 0 informations
