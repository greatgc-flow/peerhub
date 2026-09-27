# Specific — cx (Codex / OpenAI CLI)
> Delta from general/*. Load after general/. Status: ACTIVE.

> **Ported from Engram 2026-09-03** (was `_sys/docs-v2/...`; see Engram's `_sys/data/sessions/2026-09-03_docsv2-disposition-proposal.md` for the full disposition). Content is otherwise verbatim from the original -- some internal path references (e.g. `_sys/ai/orchestration.json`, `_sys/ai/model-registry.json`, `P:\`) point at Engram's now-deleted `_sys/ai/` tree or the frozen `P:\` checkout and describe the OLD pre-separation update-checkpoint workflow; they have not been individually rewritten for peerhub's own conventions yet -- treat any such reference as historical context, not a current instruction, until this doc gets a real pass.

---

## Directory Layout

```
_sys/codex/
├── config/
│   ├── CODEX.md            ← system instructions
│   └── tmp/
├── health.json             ← peer health
├── goals_1.sqlite          ← goals database
├── logs_2.sqlite           ← log database
├── memories_1.sqlite       ← memory database
└── state_5.sqlite          ← state database
```

Environment variable: `CODEX_HOME` → `_sys/codex/config/`. Hub IPC pins this via
`peers.json` `codex.env_vars.CODEX_HOME = "config"` (resolved to `_sys/codex/config`).
Without the pin, `codex.cmd` falls back to the host home `~/.codex` — non-portable and
a cold-cache re-sync that can silently stall an ask until the zombie timeout. Interactive
launch pins the same home via `codex_entry.py`.

---

## Permission Flags (delta from general/permissions.md)

```
codex exec -s workspace-write --json
```

FORBIDDEN: `--dangerously-bypass-approvals-and-sandbox`, `-s full-auto`.

## Runtime Profiles

`cx.standard`, `cx.effort`, and `cx.deepthink` are the complete profile set
advertised by the production Codex adapter. Model selection resolves through
the peerhub workspace/global/packaged configuration chain.

`codex debug models` enumerated the current account/runtime catalog on
2026-09-27 using Codex CLI 0.157.1. The GPT-6 family supersedes the previous
GPT-5.6 defaults:

| Profile | Model | Reasoning | CLI context |
|---|---|---|---:|
| `cx.standard` | `gpt-6-luna` | low | 272k |
| `cx.effort` | `gpt-6-sol` | high | 272k |
| `cx.deepthink` | `gpt-6-astra` | xhigh | 272k |

The local catalog records `low`, `medium`, `high`, `xhigh`, and `max` for all
three GPT-6 models. Astra and Sol additionally support `ultra`; Luna does not.
Each catalog row reports a 272,000-token context window.

## Context and Collaboration

Local Codex memory is not shared directly; the hub injects durable room
references and records promoted outputs.

---

## Session Policy

cx session reuse is enabled (`session_mode: reuse`). While `codex exec resume` rejects the `-s workspace-write` CLI flag, it accepts the equivalent configuration override via `-c sandbox="workspace-write"`. Hub invocations now use this syntax to maintain context continuity.

---

## Entry Point

`codex_entry.py` (peerhub package; removed from Engram in the Engram/peerhub separation):
1. Calls `hub.py init-session`, `hub.py context-fill`
2. Launches `codex.cmd`
3. Updates `availability.last_invocation_duration_ms` after each run

Direct invocation:
```
_sys\cli\codex.bat
_sys\cli\codex.bat --no-alt-screen
```

---

## Key Files

| File | Role |
|------|------|
| `health.json` (removed from Engram in the Engram/peerhub separation) | Health manifest — was updated by BOTH hub.py AND codex_entry.py |
| `CODEX.md` (removed in Engram/peerhub separation) | System instructions |
| `health.json["availability"]["authenticated"]` | OAuth auth status |
| `health.json["availability"]["entrypoint_ok"]` | Smoke test pass status |

---

## Token Constraint

cx has limited token budget — avoid large corpus analysis tasks. Prefer cc/gc for document-heavy work.
