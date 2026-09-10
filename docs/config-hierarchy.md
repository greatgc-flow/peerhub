# Config hierarchy, precedence, privacy, and migration

Ratified 2026-09-09: [`docs/design/dotdir-consolidation-RATIFIED-2026-09-09.md`](design/dotdir-consolidation-RATIFIED-2026-09-09.md). This page is the user-facing reference; that document is the design record (citations, adjudicated questions, execution order).

## The two tiers

| Tier | Root | Owner |
|---|---|---|
| **Global** (per-installation, personal) | `~/.peerhub/config/`, overridable via `PEERHUB_CONFIG_HOME` | you, or an Engram install's `.engram/peerhub/config/` |
| **Workspace** (per-project) | `<workspace>/.peerhub/config/` | this project |

There is exactly one root per tier. A workspace never gets its own global-tier directory, and the global tier never holds workspace-specific state — see [Engram/PeerHub separation](#engram) below for why.

`PEERHUB_CONFIG_HOME`, when set, **replaces** the default `~/.peerhub/config` outright. It never silently falls back to `~` if the file you're looking for is absent there — that would defeat the CI/portable-install isolation the variable exists for. Absence at a *selected* layer means "this layer has no value", not an error and not a reason to look somewhere else.

## Precedence

> **Value precedence:** workspace > global > packaged/built-in default.
> **Path precedence:** explicit argument > environment variable > default location.
> **Absent-layer rule:** once a layer is *selected*, a missing file means "this layer has no value" — never a silent fall-through to a different default root, and never to another user's home directory.
> **Malformed-layer rule:** a malformed *selected* layer is a hard configuration error, never a reason to quietly use a lower layer.

This is the same rule for every config family below, with one deliberate exception: **`models.toml` has no workspace tier.** Workspace model bindings (`peerhub node bind-profile`) remain the sole workspace-level model authority — a `config/models.toml` at the workspace layer would create two authorities for one fact.

| File | Layers (highest first) | Notes |
|---|---|---|
| `models.toml` | global → packaged default | No workspace tier. |
| `ask.toml` | workspace → global → packaged default | Field-level merge per section (`[continuity]`, `[prompt_staging]`, `[transcript_storage]`). |
| `arbiter.json` | workspace → global → built-in safe default (`enabled=false`, empty electorate) | Lists (`triggers`, `high_risk_mutation_kinds`) replace whole; `candidate` merges field-by-field. Every layer file requires a top-level `"schema_version": 1`. |
| `proposals.json` | workspace → global → built-in empty electorate | `voters` replaces whole across layers. Requires `"schema_version": 1`. |

Run `peerhub config validate [--workspace PATH] [--json]` to see, for your own workspace right now, whether every layer of every family loads cleanly, and — for `arbiter.json`/`proposals.json` — which layer supplied each field.

## Where files actually live

`arbiter.json` and `proposals.json` moved from `.peerhub/*.json` directly to `.peerhub/config/*.json` (the same tier `ask.toml` already used). **Both locations are never read from at once**: if a legacy `.peerhub/arbiter.json` and a new `.peerhub/config/arbiter.json` both exist, every read raises an error naming `peerhub config migrate` rather than silently picking one.

- `peerhub config paths [--workspace PATH] [--json]` — reports every resolved path and why it was selected (`explicit` / `env` / `workspace` / `default`).
- `peerhub config migrate [--workspace PATH]` — validates each present legacy file with its real loader, then moves (not copies) it to `config/`, byte-for-byte. If a legacy and a new file both exist, or a legacy file is itself malformed, nothing moves and the error explains why.
- `peerhub config init --scope global|workspace [--workspace PATH]` — creates the tier's directory and seeds it with a fully-commented starter file (never overwriting one you've already started editing): `ask.toml` at both scopes, `models.toml` at global scope only.

## Privacy

A workspace's `config/` directory holds project-scoped policy (who arbitrates, who votes, dispatch-shaping bounds) — nothing that should ever need to be secret from the project's own contributors. The global tier is personal, per-installation configuration and is never committed to a workspace's own repository (`.peerhub/` is gitignored at both tiers by the packaged `.gitignore` template).

Neither tier is a place for credentials. Nothing peerhub reads from `config/` is a secret token, and `peerhub backup workspace` (see below) never bundles anything outside its own named allowlist for exactly this reason.

## Backup and restore

`peerhub backup workspace --workspace PATH --output DIR [--include-transcripts]` captures a workspace via SQLite's online-backup API — never a plain file copy of a live database, and never `-wal`/`-shm` sidecars copied independently — bundled with the workspace's `config/` files. `dispatch_transcripts` rows (raw dispatch text) are durable and sensitive, so their inclusion is never implicit: omit `--include-transcripts` and they are excluded and actually removed from the bundle (`VACUUM`ed, not merely unreferenced).

`peerhub backup restore BUNDLE --workspace PATH` validates schema version and workspace identity before activating anything — a bundle from a newer peerhub build, or belonging to a different workspace identity, is refused with nothing at `--workspace` touched.

<a id="engram"></a>
## Running inside an Engram install

[Engram](https://github.com/greatgc-flow/Engram) — the portable dev-environment package peerhub used to live inside — redirects `PEERHUB_CONFIG_HOME` (alongside `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `GEMINI_DIR`, `GH_CONFIG_DIR`) to `.engram/peerhub/config/` automatically, so the global tier lives inside the one consolidated, portable root instead of the host's real `~`. This needs zero peerhub-side code: it's exactly `PEERHUB_CONFIG_HOME` doing what it always does. See Engram's own docs for `.engram/`'s design and privacy properties — it is explicitly a **live** root (it accumulates real caches and credentials over time), never itself something to hand off or commit wholesale.
