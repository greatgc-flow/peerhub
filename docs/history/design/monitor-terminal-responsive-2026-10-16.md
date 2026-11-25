# Responsive and legible monitor terminal dashboard (2026-10-16)

## Status and scope

**Approved by CC, CX, and AG.** CX approved without constraints; CC and AG's constraints are included below. This is a terminal-presentation correction to the v0.18.0 grouped monitor. Implement after the v0.18.0 tag/release pipeline is complete, on a separate patch branch. Do not move or rewrite the published tag.

The user reported a dashboard that showed zero peers, unknown usage/context and group errors without saying which database was selected or why reads failed. Inspection confirmed the discovered root database was the selected store and genuinely had zero peer rows and lacked Bridge session/delivery tables; the discovered quota data was valid. The screen's lack of provenance and error context made the truthful zero values difficult to interpret. A repo-local development database has separate Bridge data and must not be silently substituted.

Included: TTY width/height-aware layout, clear visual hierarchy, cached reflow, visible store selection and safe read errors, in-frame refresh state, and terminal cleanup. Excluded: store selection policy, read-model/schema/provider changes, group defaults, JSON schema, redirected-output format, paging/input mode, and extra dependencies.

## Agreed interaction and visual contract

- Keep a single pure layout planner: immutable schema-2 frame + terminal columns/rows + capabilities -> bounded display lines. It performs no filesystem/database/provider access. `monitor_view.py` is the current pure renderer boundary; terminal I/O remains in `monitor.py`/`view.py`.
- Determine live geometry from the active TTY/file descriptor and refresh it at most once per one-second display tick. Do not trust stale `COLUMNS`/`LINES` when a live terminal size is available. Resize and countdown changes re-plan the cached payload only; they never call SQLite or provider CLIs.
- Apply the bounded viewport only to an interactive VT TTY. Redirected output and non-VT append mode keep the existing full human frame layout (no height-based omission) and `COLUMNS` width override; JSON keeps its exact current wire output.
- Every interactive viewport frame is limited to `rows - 1` lines and `columns - 1` terminal cells per line to prevent terminal auto-wrap/scroll. Frame writes are buffered and flushed as one update. Tiny terminals degrade deterministically; at one usable row, render a compact hidden-count/`--group all` hint, and at zero usable rows emit no frame. Never silently omit selected groups when a line can be rendered.
- Preserve a compact summary for every selected group when the available row budget allows, in canonical catalog order. Spend remaining rows on details in the same order as visible summaries. If summaries do not fit, first rank groups by severity (ERROR/FAILED, unavailable, stale/partial/unknown, then OK; stable catalog order breaks ties), then show details in that visible order plus an explicit omitted-group count and the exact snapshot/group command. No keyboard/raw mode or automatic paging.
- Visual treatment is restrained and consistent: a compact title/status line, aligned labels and values, stable spacing, and light framing only when it improves scanning. Compact mode removes borders/decoration before facts. State is always expressed in text (`OK`, `ERROR`, `STALE`, `PARTIAL`, `UNKNOWN`, `REFRESHING`); color is an accent, never the sole signal. No status emoji. Respect plain/ASCII, `NO_COLOR`, and `TERM=dumb`.
- Header includes overall state, error/partial counts when applicable, refresh countdown, and selected database path/source. Elide the path in narrow layouts but retain the source (`explicit`, `workspace`, `discovered`, etc.) if any provenance space remains. Existing JSON `store_selection` is unchanged.
- Show only safe, bounded reason identifiers/messages from an allowlist. Never render raw exception strings, SQL, arbitrary paths, or terminal control/bidi characters. For failures with no safe detail, use an explicit generic database-read failure. Do not infer or claim that missing Bridge tables can be repaired from this database.
- Before a blocking provider refresh, render `REFRESHING` in the TTY frame. When it returns, show its status and bounded, sanitized warning reason codes when space allows; do not print separate stderr lines over a full-screen display. Redirected human and JSON output retain their current behavior.
- If the existing agy token-use safety stop ends watch mode, show the existing `--allow-agy-token-use` continuation hint in the TTY frame when space permits; keep the current stderr guidance for append-only output.
- Restore cursor visibility, SGR state, and any changed console mode in `finally` on normal exit, exceptions, and interruption. If VT initialization fails or `TERM=dumb`, use append/plain mode without escape sequences. Resize is polled; do not install platform-specific raw input or resize signal handlers.
- Text-width accounting must handle combining marks and wide characters; ZWJ/variation selectors have zero display cells, unsafe control/bidi/unknown characters become a visible placeholder, and fallback remains conservative. No new third-party dependency.

## TDD acceptance plan (RED before implementation)

1. **Pure planner geometry:** parameterize columns (15, 20, 40, 60, 80, 120, 200) and rows (1, 2, 3, 4, 5, 8, 12, 24, 60). Assert no more than `rows - 1` physical lines and each line at most `columns - 1` display cells after SGR removal. Include Unicode line separators U+2028/U+2029, long unbroken fields and borders; decoration truncates before facts.
2. **Height priority/overflow:** test all groups, selected subset, equal-priority catalog ordering, ERROR/FAILED > unavailable > stale/partial/unknown > OK, every fitting summary included, exact hidden count/command otherwise, and no silent omission. Verify additional height reveals details without changing summary/order/status.
3. **Visual and accessibility contract:** lock readable title/header, label alignment, consistent spacing/framing in wide mode, decoration-first compact mode, textual states, no emoji, ASCII and `NO_COLOR`/`TERM=dumb`, and no raw JSON. Exercise CJK, combining accents, ZWJ emoji, bidi/control and unknown characters at the 20-column edge.
4. **Store/error disclosure:** path and source appear in normal layout; narrow layout elides path but retains source; dependent error groups show only an allowlisted safe reason or generic reason; a secret/path/SQL embedded in the payload error is absent from output. Refresh warning codes are similarly bounded and sanitized.
5. **TTY lifecycle and resize:** fake TTY geometry changes across ticks; the renderer replans from the last payload. Assert no extra read-model/provider call on resize/countdown and `REFRESHING` precedes blocking refresh. Interrupt/exception tests assert cursor/SGR cleanup and no terminal escapes for redirected output.
6. **Contracts:** keep JSON byte-compatible and redirected human layout full/unbounded with exactly one final newline; no ANSI/cursor sequences in either. Snapshot remains read-only. PTY smoke on Windows and POSIX verifies live geometry and appearance.
7. Run focused monitor tests, deterministic full suite, pyright, architecture/inventory gates as applicable; obtain a code review after implementation. No schema migration, command inventory change, extension module, or extra dependency is anticipated.

## Sequencing

1. Finish and verify the already-published v0.18.0 release workflow.
2. Create `fix/monitor-terminal-geometry` from the clean release `main`.
3. Add RED tests and observe expected failures before renderer/TTY changes.
4. Implement pure bounded layout and TTY lifecycle changes in the existing CLI presentation modules.
5. Validate focused tests, full suite and pyright; inspect a real terminal-size smoke test.
6. Commit and report release/merge status separately; do not include these changes in v0.18.0 artifacts.

## Validation record

- Windows ConPTY snapshot smoke passed on 2026-10-16 at 80×24 with `TERM=xterm`. The dashboard stayed within the viewport, showed the selected database path, and converted missing-Bridge-table failures into bounded `OperationalError: database read failed` summaries. A normal redirected snapshot retained the full frame.
- A POSIX PTY subprocess test now sets an 8×40 slave terminal while stale `COLUMNS=120`/`LINES=60` are present; hosted Ubuntu CI must run it before merge. The local Windows run skips this POSIX-only test.
- Local focused monitor/CLI validation after the PTY test was added: 187 passed, with that POSIX-only case skipped on Windows. Pyright passed with 0 errors and warnings.
