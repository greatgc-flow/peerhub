# Profile SSOT: add, change or delete a profile in one place (2026-10-29)

Owner ask: adding, changing or deleting a profile such as `ag.argon` must be simple (SSOT).
Evidence: the argon commit (#196) touched 31 files; the schema enumerates profile ids, tests pin
`len(profiles) == 10` and per-model values, and `model-profiles.json` duplicates the toml.
Runtime (`profiles.py`, `guide.py`, `profile list`, monitor) is already data-driven.

## Design (proposal, needs unanimous peer approval)
1. `peerhub/config_data/model-defaults.toml` is the only store of profiles, `exact_model_pools`,
   `observed_at`, `observed_versions` and `defaults_revision`.
2. `docs/model-profiles/model-profiles.json` keeps only the refresh recipe: `sources`, `tier_rules`,
   `update_targets`, `instructions`. Its `profiles`, `exact_model_pools`, `revision` and `observed_*`
   copies are removed. The schema stops enumerating profile ids.
3. Per-profile `source`/`checked`/`pools_source` stay stored in the toml (provenance differs per fact;
   `checked` is never derived). The invariant test derives only the expected `source` from `sources` +
   `observed_*` and matches `pools_source` by prefix (`unverified` or `agy /usage`).
4. Tests assert invariants, not values: every profile id is `<ag|cc|cx>.<name>`, has model, pools and
   source, its model is known or pinned, retired ids (`*.pro`) stay unknown, guide/profile-list show
   every profile. Override, unknown-id and pool-mapping tests stay, on synthetic fixtures. Remove `len == 10`, per-model value pins and argon-only schema tests.
5. Docs/guide tests: no profile counts or names in prose; point to `peerhub profile list`. Rewrite
   `README`, `docs/adapters/{ag,cc,cx}.md`, `.claude/skills/refresh-model-profiles`, count-free `STATUS`.
6. `tools/model_ping.py` uses `list_profiles(cwd)` (effective values), so opt-in profiles can be pinged.
   Manifest `schema_version` becomes 2 (recipe contract change); instruction 7 (dated observation log)
   moves to CHANGELOG. Release: v0.23.0 (documented contract change).
7. Recipe (README, 3 lines): add/change/delete = edit one `[profiles."<peer>.<name>"]` table (plus an
   `exact_model_pools` entry for a new model); run `pytest tests/communication/adapters`; nothing else.

## Also in this slice (refresh 2026-10-29)
Version drift only (claude 2.1.312, codex 0.165.0, agy 1.4.9); no packaged model/effort change.
New cx model `gpt-6.1-terra` is not packaged (tier/cost unknown); opt-in via
`[profiles."cx.terra"]` in `~/.peerhub/config/models.toml` (needs selection_mode, model, pools; labelled
 unevaluated; one test with a temporary global file). Files stay LF (.gitattributes).

## Non-goals
No generator/CLI for refresh, no new catalog, no behaviour change in `ask`.
