# peer CLI observations

Durable home for empirically-discovered facts about the external peer CLIs,
so they stop being rediscovered by hand every session
(`docs/design/FACT-REFRESH-PROCEDURE-R1.md`, "Where the expected facts live").

One entry per discovery. Trace units follow
`docs/design/TRACEABILITY-CONVENTION-R1.md` (`PH-<WORKSTREAM>-<UNIT>`).
Entries are `ACTIVE` until something replaces them, then `SUPERSEDED` with a
pointer — never deleted, because the reason a fact stopped being true is
itself a fact worth keeping.

Per DIR-004, every entry carries a source tag. The tags in use here are:

- `empirical_probe` — measured on a real invocation made for this purpose.
- `cli_live` — read out of a live invocation of the vendor CLI itself,
  including its `--help` output and captured failure transcripts. A
  project-wide DIR-004 tag; first used in this file by OBS-0007.
  Prefer it over `empirical_probe` when the evidence *is* the vendor's own
  output rather than a probe this project constructed.
- `declared, unverified` — read only out of a doc, a config file, or
  someone's summary, and never confirmed against a running CLI.

A fact with no evidence is recorded as `absent` or `TEST NEEDED`, never
estimated.

---

## OBS-0001 — `--version` probes are free and instant

- Trace unit: `PH-FACTS-OBS1`
- Peer / CLI version: all three (`agy.exe` 1.1.12, `claude.cmd` 2.1.222,
  `codex.cmd` 0.147.0)
- Observed: 2026-08-12 · source tag: `empirical_probe`
- Status: ACTIVE

**Reproducer**

```
agy.exe --version
claude.cmd --version
codex.cmd --version
```

**Observed behaviour** — each returns exit 0 in well under a second and
consumes no model quota. This is what makes the default fact-refresh routine
cheap enough to run at the start of every session; only `--live` spends real
peer usage.

**Depends on it** — `tools.peerhub_facts.collectors.probe_cli`, and the
procedure's decision to separate the default routine from `--live`.

**Regression test** — `tests/unit/tools/test_peerhub_facts.py::test_version_probe_is_wired_through_the_supervised_runner`
(pipe-level; the free/instant property itself is a cost claim, not asserted
in CI).

---

## OBS-0002 — Codex emits warnings *before* its real version line

- Trace unit: `PH-FACTS-OBS2`
- Peer / CLI version: `cx` / `codex.cmd` 0.147.0
- Observed: 2026-08-12 (rediscovered; known informally since 2026-08-09)
  · source tag: `empirical_probe`
- Status: ACTIVE

**Reproducer**

```
codex.cmd --version
```

**Observed behaviour** — on this workstation the banner is a clean
`codex-cli 0.147.0`, but codex is known to prepend sandbox/config warning
lines depending on environment. Those warning lines carry filesystem paths,
and a path segment can be semver-shaped. Any exact-match against the whole
`--version` output therefore reports drift on every run, and even a naive
"first number in the output" scan can lock onto a path fragment.

**Depends on it** — `collectors.parse_version`, which drops noise lines
(`warning`/`sandbox`/`error`/…) first, then prefers the line carrying the
vendor's own product marker (`codex`), and only then falls back.

**Regression test** — `test_codex_version_survives_a_sandbox_warning_prefix`.

---

## OBS-0003 — Claude prints its version with a trailing product name

- Trace unit: `PH-FACTS-OBS3`
- Peer / CLI version: `cc` / `claude.cmd` 2.1.222
- Observed: 2026-08-12 · source tag: `empirical_probe`
- Status: ACTIVE

**Reproducer**

```
claude.cmd --version
```

**Observed behaviour** — emits `2.1.222 (Claude Code)`, not a bare semver.
Separately, `claude.cmd -p ... --output-format json` can print
`Warning: no stdin data received` *before* its JSON object, which is why
`ClaudeOutputDecoder` slices between the first `{` and the last `}` instead
of parsing the stream whole.

**Depends on it** — `collectors.parse_version`, and
`peerhub.adapters.claude_adapter.ClaudeOutputDecoder.finalize`.

**Regression test** — `test_claude_version_ignores_the_trailing_product_name`
and `test_decoder_conformance_tolerates_claude_warning_prefix`.

---

## OBS-0004 — executable resolution must try the literal name before appending PATHEXT

- Trace unit: `PH-FACTS-OBS4`
- Peer / CLI version: all three, Windows
- Observed: 2026-08-12 (rediscovered) · source tag: `empirical_probe`
- Status: ACTIVE

**Reproducer**

```python
from peerhub.adapters.registry import resolve_peer_target
resolve_peer_target("ag").executable_path   # ...\agy\agy.exe
```

**Observed behaviour** — the adapters plan argv with names that already
carry an extension (`agy.exe`, `claude.cmd`, `codex.cmd`). A resolver that
unconditionally appends every `PATHEXT` entry would look for `agy.exe.com`
and miss the real file, so `_resolve_executable_path` checks the literal
candidate first and only extension-expands when the name has no recognised
suffix. It also avoids `shutil.which`, which is cwd-hijackable on Windows.

**Depends on it** — `peerhub.adapters.registry._resolve_executable_path`.

**Regression test** — `tests/unit/test_adapter_registry.py` (pre-existing).

---

## OBS-0005 — `codex --json` is a subcommand flag, not a top-level one

- Trace unit: `PH-FACTS-OBS5`
- Peer / CLI version: `cx` / `codex.cmd` 0.147.0
- Observed: 2026-08-12 · source tag: `empirical_probe`
- Status: ACTIVE

**Reproducer**

```
codex.cmd --help        | grep -c -- --json    # 0
codex.cmd exec --help   | grep -c -- --json    # >0
```

**Observed behaviour** — `RealCodexAdapter.plan_invocation` builds
`codex.cmd exec --json <prompt>`, but `--json` appears only in the help for
the `exec` subcommand. Checking required help tokens against codex's
top-level help reports `--json` as missing and produces a **false DRIFT** on
a perfectly healthy install. Found while seeding
`peer-cli-contracts.toml` — the first fact this routine caught about itself.

**Depends on it** — the `help_argv = ["exec", "--help"]` entry for `cx` in
`peer-cli-contracts.toml`; `compare.compare_help` reads it per peer rather
than assuming `--help`.

**Regression test** — `test_cx_contract_probes_exec_help_not_top_level_help`.

---

## OBS-0006 — the three decoders implement three different protocols

- Trace unit: `PH-FACTS-OBS6`
- Peer / CLI version: all three, as of the versions above
- Observed: 2026-08-12 · source tag: `empirical_probe`
- Status: ACTIVE

**Reproducer** — see `collectors._DECODER_FIXTURES`; each fixture is
transcribed from the decoder it exercises, not invented.

**Observed behaviour**

| peer | protocol | how the response is found |
| --- | --- | --- |
| `ag` | flat JSON | top-level `response` key |
| `cc` | result JSON | `result`, gated on `is_error`, after `{`…`}` slicing |
| `cx` | JSONL events | line with `type == "item.completed"` and `item.type == "agent_message"`, then `item.text` |

Every cell answers the same question for its peer. Session identity is a
different question with a different answer per peer and lives in OBS-0007;
it was briefly folded into this table's `ag` row and has been moved out.

A green `--version` and a green `--help` say nothing about whether these
shapes still hold, which is why the procedure keeps decoder conformance as a
separate check that runs even for an ABSENT peer.

**Depends on it** — all three `*OutputDecoder.finalize` implementations.

**Regression test** — `test_decoder_conformance_passes_for_every_peer` and
`test_decoder_drift_is_detected_when_the_protocol_shape_changes`.

---

## OBS-0007 — session resume and session-ID capture are asymmetric across the three CLIs

- Trace unit: `PH-FACTS-OBS7`
- Peer / CLI version: all three (`agy.exe` 1.1.12, `claude.cmd` 2.1.222,
  `codex.cmd` 0.147.0)
- Observed: 2026-08-13 · source tag: `cli_live`
- Status: ACTIVE

**Reproducer**

```
claude.cmd --help          | grep -e --session-id -e --resume
codex.cmd exec resume --help
agy.exe --help             | grep -e --conversation -e --continue
```

**Observed behaviour** — all three CLIs support resuming a prior session,
but they differ on *who mints the session ID*, and that difference decides
whether the adapter has anything to capture.

| peer | resume invocation | where the session ID comes from | decoder capture |
| --- | --- | --- | --- |
| `cc` | `--resume <id>` | **the caller.** `claude.cmd --session-id <uuid>` takes a caller-chosen UUID ("must be a valid UUID") for the conversation | none, and none is possible — the ID is already known before the process starts |
| `cx` | `resume <id>` placed after `exec` | **the server**, announced in the JSONL stream | `thread.started` line → `thread_id` → `SESSION_IDENTITY` event |
| `ag` | `--conversation <id>` | **the server**, echoed in the flat JSON response | top-level `conversation_id` → `SESSION_IDENTITY` event |

Two consequences worth stating outright, because both were previously got
wrong on `--help`-only evidence:

- Claude's empty capture cell is permanent, not a deferred gap. Trying to
  make `ClaudeOutputDecoder` emit `SESSION_IDENTITY` would mean re-parsing
  an ID the caller supplied.
- Agy's ID needs no filesystem workaround. An earlier `--help`-only
  investigation concluded Agy had "no deterministic session-ID mechanism";
  reading actual response JSON showed `conversation_id` sitting at the top
  level. `--help` text is evidence about flags, not about output shape.

Agy's `--continue` is deliberately **not** used: it resumes ambient
most-recent state, which cannot be bound to a specific `SessionHint`.

**Depends on it** — `RealClaudeAdapter.plan_invocation`,
`RealCodexAdapter.plan_invocation`, `RealAgyAdapter.plan_invocation`,
`CodexOutputDecoder.finalize`, `AgyOutputDecoder.finalize`, and all three
descriptors' `Capability.SESSION`.

**Regression test** — resume argv, per peer:
`test_claude_plan_invocation_session_resume`,
`test_codex_plan_invocation_session_resume_uses_exact_argv`,
`test_agy_plan_invocation_session_resume_uses_exact_argv` (each with a
`_missing_id`/`_requires_id` negative and a `_session_none` unchanged-path
case). Session-ID capture:
`test_codex_decoder_emits_session_identity_from_thread_started` and
`test_agy_decoder_emits_session_identity_from_top_level_conversation_id`,
each paired with a `_without_..._is_unchanged` negative. Capability
advertisement: `test_codex_descriptor_advertises_session` and
`test_agy_descriptor_advertises_session` — **Claude has no equivalent
descriptor test**, an asymmetry in coverage rather than in behaviour.

---

## OBS-0008 — real vendor failure output did not match the shapes the decoders assumed

- Trace unit: `PH-FACTS-OBS8`
- Peer / CLI version: `ag` / `agy.exe` 1.1.12, `cx` / `codex.cmd` 0.147.0
- Observed: 2026-08-13 · source tag: `cli_live`
- Status: ACTIVE

**Reproducer** — provoke a real failure rather than constructing one:
request a nonexistent model, or resume a nonexistent conversation ID.
The captured bytes are transcribed verbatim into the fixtures cited below.

**Observed behaviour** — three assumptions held by the first vendor-error
implementation were wrong against live output:

- Agy's JSON `error` field is **not always an object**. A real auth failure
  returned a bare string, so `error_val.get("type")` was unreachable.
- Agy prepends stderr text before its JSON on a failed resume (e.g.
  `warning: conversation "..." not found` followed by the JSON object), so
  parsing the whole buffer with `json.loads` fails. It needs the same
  `find("{")`/`rfind("}")` slicing `claude.cmd`'s warning prefix already
  forced on `ClaudeOutputDecoder` (OBS-0003).
- Codex emits a **flat** `{"type":"error","message":...}` for some failures,
  not only the nested `error.code` shape, and separately emits
  `turn.failed` with the message nested under `error.message`. The two
  branches therefore read the message from different places — a live-observed
  fact, not a bug, but a fragile one.

**Depends on it** — `AgyOutputDecoder.finalize` and
`CodexOutputDecoder.finalize`, specifically their JSON slicing, their
non-dict `error` guards, and Codex's flat-error/`turn.failed` branches.

**Regression test** — `test_agy_decoder_live_preamble`,
`test_codex_decoder_live_flat_error`, `test_codex_decoder_live_turn_failed`
(all tagged `[cli_live] 2026-08-13` inline). Agy's string-`error` path could
not be reproduced live in this environment and its fixture
(`test_agy_decoder_string_error_auth`) stays marked `TEST NEEDED` rather
than being backfilled with an unverified claim.
