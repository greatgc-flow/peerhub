# Interface MECE / Aesthetic / Robustness Audit (2026-08-24)

Status: kickoff. Triggered by user request: "ai cli들 및 peerhub의
cli,문서,폴더구조 등 전체 구성요소(인간 포함) 간 접점(인터페이스,
파라메터, ui 등)이 상호mece하고 알기 쉽고, 미적, 기능적으로 충분히
아름답고 견고한 지 상세 검토 및 필요시 보완해줘. 상호 이견 없을때까지
영원히 보완." (Review whether every touchpoint — interfaces, parameters,
UI — between all components, including humans, is mutually MECE, easy to
understand, and sufficiently aesthetically/functionally beautiful and
robust; fix as needed; keep refining until no disagreement remains.)

Scope: peerhub's CLI/docs/folder structure (already substantially covered
by the 7-gap reconciliation work — see `HUB-REPLACEMENT-*` docs), PLUS the
other real AI CLI entry points under `P:\_sys\cli\` (`claude_entry.py`,
`codex_entry.py`, `agy_entry.py`, `console_runner.py`), which haven't been
audited yet. This doc starts that second half.

## First concrete finding (terminal, direct read of all 3 real entry-point files)

`claude_entry.py`, `codex_entry.py`, `agy_entry.py` are structurally
near-identical (same imports, same `ConsoleSessionSpec`/`run_console_session`
pattern, same `_env()`/`_set_title()`/`main()` shape) but have real,
**unjustified inconsistencies** — not the deliberate, commented kind (ag's
`keyboard_interrupt_is_success=False` IS deliberately commented and
justified: "ag's original pre-migration convention always marked health
RED on Ctrl+C... preserve that here (independent cross-verification found
this)" — that one is fine, a real peer-specific quirk, correctly
documented).

**Unjustified inconsistencies found:**

1. **`_set_title()` is triplicated VERBATIM** across all 3 files (same 12
   lines, byte-for-byte identical logic) — a plain DRY violation. Should
   be factored into `console_runner.py` (already the shared import) or a
   new small shared module, not copy-pasted 3 times.
2. **`claude_entry.py` has NO executable-existence check** before
   launching (`_CLAUDE_CMD.exists()`), while `codex_entry.py` and
   `agy_entry.py` both check and print a clean `[ERROR] ... not found`
   message before exiting. If `claude.cmd` is missing, `claude_entry.py`
   will fail with whatever raw OS error `cmd /c <missing path>` produces,
   not the same clean message its siblings give — a real robustness/
   consistency gap, not a stylistic one.
3. **`claude_entry.py` does not pass `health_json_path`** to
   `ConsoleSessionSpec`, while `codex_entry.py` and `agy_entry.py` both
   do (pointing at `_sys/codex/health.json` and
   `_sys/antigravity/health.json` respectively). No equivalent
   `_sys/claude/health.json` path is wired in. Worth checking whether
   this is intentional (cc's health is tracked differently, e.g. via the
   terminal session itself rather than a spawned subprocess) or a real
   gap — **not yet determined, needs a `console_runner.py` read to see
   what `health_json_path` actually does when absent vs present.**
4. **`agy_entry.py`'s `_env()` loads peer-specific env vars from
   `peers.json`** (`antigravity.env_vars`), while `claude_entry.py`/
   `codex_entry.py`'s `_env()` are simpler (venv PATH only, codex adds a
   single hardcoded `CODEX_HOME`). This asymmetry might be justified (ag
   genuinely needs more env wiring) or might mean codex/claude's env
   needs are under-modeled compared to ag's more general
   config-driven approach — **not yet determined.**

## What this means for the broader audit

This is exactly the kind of finding the user is asking for across the
WHOLE system — the pattern (3 near-identical files, some real
duplication, some real unexplained divergence, some correctly-justified
divergence) is a useful template for scanning the rest of `_sys/cli/`,
peerhub's own module boundaries, and the docs/folder structure.

**This is a large, open-ended review scope** ("영원히" — forever, until
no disagreement). The terminal will continue this audit in further
rounds, delegating the broader comparative/synthesis work to `cx` (per
standing quota policy) while continuing to verify concrete claims against
real files directly, the same discipline used throughout the 7-gap
reconciliation effort.

## Next steps (not yet done)

1. Read `console_runner.py` (375 lines, the shared runner all 3 entry
   points call into) to resolve items 3-4 above and find the REAL shared
   contract these 3 thin wrappers are supposed to honor.
2. Dispatch a broader cross-CLI consistency/aesthetics review to `cx`,
   grounded in real file contents (not guessed), covering: the 3 entry
   points + `console_runner.py`, peerhub's `cli.py` (already surveyed),
   the folder structure of both `P:\_sys\` and `P:\workspace\peerhub\`,
   and doc-naming/organization conventions.
3. Extend to peerhub's own internal module boundaries (is `dispatch/`
   vs `application/` vs `governance/` a clean, MECE separation, or is
   there overlap — e.g. `application/legacy.py`'s `LEGACY_CATALOG`
   duplicating naming concerns that might belong in `core/`?).
