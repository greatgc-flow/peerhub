# Model Pools, Drift Detection & Guide Design (2026-10-17)

**Status:** Unanimous CC + CX + AG consensus; implementation slices S1–S5 on branch `feat/pools-guide`.

## 1. Context & Motivation
Following v0.18.2's `peers` group and model-fact provenance, multi-pool quota accounting (e.g. per-model quota windows) and CLI version/pool drift required structured handling without runtime provider network calls or complex catalogs.
Additionally, users needed a concise, authoritative offline quick reference (`peerhub guide`) to avoid common mistakes (e.g. blind retries, misconfigured session reuse).

## 2. Design Principles & Architecture

### S1: Per-Profile Pools & Packaged Metadata
- Per-profile `pools` mapping in `docs/model-profiles/model-profiles.json` and packaged `peerhub/config_data/model-defaults.toml`.
- Mappings apply ONLY to the exact configured profile model.
- Each profile includes `source` and `checked` date (format: `cli:<cmd>@<ver> (<YYYY-MM-DD>)` or `doc:<url>@<YYYY-MM-DD>`).
- Packaged TOML includes `observed_versions` alongside profiles.
- Strict parity tests ensure manifest and packaged TOML stay synchronized across all keys.

### S2: Monitor Peers View Multi-Pool Selection
- Mapped model: peers view selects the minimum remaining quota across all fresh measured pools listed in the model's profile `pools`.
- Pool name is labelled (e.g., `5h (pool: sonnet)`).
- When a required pool is missing or stale, status displays `incomplete` (or `STALE`).
- Unknown models or manual overrides display `account-wide` or `unmapped` (falling back to lowest measured pool, labelled accordingly).

### S3: Quota Probe Drift Report Persistence
- `quota_probes.py` preserves unknown bucket IDs in raw probe results instead of silently dropping them.
- `check_model_facts_drift()` compares live CLI versions against packaged `observed_versions` and checks for unrecognized pools.
- Exactly ONE latest bounded drift report is persisted inside the existing quota observation payload under `drift_report`.
- Unchecked components are marked `unknown`.
- Failed or partial refreshes never clear prior drift status; replacements are atomic.

### S4: Monitor Facts Line & JSON Detail
- Monitor displays a single clipped line when drift is detected:
  `Facts: needs_recheck (<summary>) - refresh-model-profiles`
- If no drift is present, the facts line displays `Facts: ok (<age>)`.
- Full detail is available via `peerhub monitor --json` under `model_facts`.
- Zero extra probes on render; no ask warnings, no exec-model-vs-binding detector, no unbounded history.

### S5: Quick Reference Guide (`peerhub guide`)
- Concise, offline-first command: strictly <=60 lines and ~900 tokens.
- Works in an empty directory without database or provider credentials.
- Profile table lists `.standard` profiles first, detailing model, pools, and provenance source.
- Contains required operational guidance:
  - Two-asks session reuse with `--stream`.
  - Delivery safety: never blindly retry on uncertain delivery/timeout.
  - Discovery commands: `monitor`, `diag`, `profile list`, `session list`, `observation refresh`.
  - Single recovery step: `refresh-model-profiles`.
- Pointers added to `peerhub --help` and `README.md`.

## 3. Explicit Non-Goals & Cut List
- No runtime external network calls or web scraping for model facts.
- No auto-refresh daemons or background workers.
- No dynamic execution-model sniffing or heuristic binding detectors.
- No ask-time intrusive warning popups.
- No database migrations or schema alterations (utilizes existing observation payload).
