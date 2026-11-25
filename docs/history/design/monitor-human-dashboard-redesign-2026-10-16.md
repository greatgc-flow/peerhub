# Human-First Monitor Dashboard Redesign (2026-10-16)

## Status

**Design approved unanimously by CC, CX, and AG after four dialectical review rounds.** This document supersedes the human-output portion of [Monitor Group Usability Review](monitor-groups-usability-review-2026-10-16.md). The earlier decision treated colored headings plus indented JSON as a human dashboard; inspection of the actual CLI proved that this was not a usable visual dashboard.

No implementation is covered by this approval until the RED tests below are written and reviewed. The group catalog, seven packaged defaults, bounded sampling rules, and truthful delivery-certainty semantics from the earlier review remain in force.

## Problem and evidence

The grouped renderer in `peerhub/cli/monitor.py` prints each group's `data` with `json.dumps(..., indent=2)`. `--view rich` only colors group headings. The `FrameOutput` redraw keeps this text in place but does not make it a visual dashboard. The old schema 1.1 route has the only actual visual renderer (`peerhub/cli/view.py`): aligned rows, capacity bars, status markers, and terminal redraw. That route is isolated behind `--legacy` and does not consume selected monitor groups.

As a result, bare `peerhub monitor` gives people a raw data dump instead of the requested dashboard. Group discovery and selection exist, but selecting groups does not make their contents readable. The fix is a visual renderer over the grouped read model, not a new data model, provider adapter, or database schema.

## Final user contract

### Commands and responsibility boundaries

| Command | Responsibility | Human output | Machine output |
|---|---|---|---|
| `peerhub monitor` | Watch effective default groups | Visual grouped dashboard on a capable TTY; aligned plain group sections when redirected | — |
| `peerhub monitor --group sessions --group execution` | Watch an invocation-specific selection | Same renderer, only selected groups | — |
| `peerhub monitor --group all` | Watch every catalog group | Same renderer, all groups in catalog order | — |
| `peerhub monitor --snapshot` | Read one committed snapshot; do not refresh providers or mutate state | One static grouped dashboard | — |
| `peerhub monitor groups` | Discover all stable group IDs, meanings, and effective-default membership | Ordered catalog table | Add `--json` for catalog JSON |
| `peerhub monitor defaults show\|set\|reset` | Inspect or change persistent display selection | Preference summary | Add `--json` for structured output |
| `peerhub monitor --json` | Watch using the schema 2.0 machine contract | — | One schema 2.0 NDJSON frame per cycle, without ANSI/cursor controls |
| `peerhub monitor --snapshot --json` | Read one machine snapshot | — | One schema 2.0 JSON object |
| `peerhub monitor --json --schema-version 1.1` | Compatibility watch for the 0.16.1 flat contract | — | One schema 1.1 NDJSON frame per cycle |

Discovery, configuration, and invocation remain separate: `groups` answers **what groups exist**, `defaults` answers **which groups should appear by default**, and `--group` answers **which groups this invocation monitors**. Explicit groups override preferences. `--group` is repeatable, deduplicated, and rendered in canonical catalog order. `--group all` is exclusive with other group IDs. Invalid IDs produce a concise usage error listing valid IDs.

The packaged default remains `overview`, `execution`, `sessions`, `usage`, `context`, `capacity`, and `coverage`. `runtime` and `store` remain opt-in. Workspace preferences override global preferences, which override packaged defaults. `monitor groups` reports this effective selection and its source.

### Human rendering

- `--view auto` (the default) selects the visual renderer when stdout is an interactive TTY with usable terminal support. A TTY render is a real dashboard: stable group panels, aligned values, status words/glyphs, and charts/meters only for numeric measures that support them. It uses the existing terminal primitives where correct and adds group-aware presentation over the schema 2.0 read model.
- The visual dashboard redraws in place no more than once per second. Redraw ticks advance age/countdown presentation from the same frame and do not query SQLite or call providers. Provider collection occurs only at the existing configured cadence.
- Redirected/non-TTY human output is append-only, compact, aligned, and free of ANSI and cursor-control sequences. It uses the same group-specific formatters; it never falls back to JSON dumps. A watch emits a timestamped section for each completed frame.
- `--view plain` keeps the group layout but uses ASCII-safe markers and bars without color. `--view rich` requests visual glyphs and color where the terminal supports them; redirected output still contains no terminal control codes. `NO_COLOR`, `TERM=dumb`, Windows VT support, and the active code page are respected.
- Narrow widths switch to a stacked/compact layout. Every selected group remains identifiable and represented; layout must not silently omit data. Color is never the only encoding of state. Unknown, stale, partial, and unavailable evidence remain explicit.
- A bar is shown only for a bounded numeric quantity such as quota used/remaining or a measured context percentage. Execution displays Bridge delivery certainty and measured latency; it never labels terminal delivery as task success or invents a success rate.

`--snapshot` is a one-shot, read-only view and does not refresh provider quota, write preferences, or update projections. A normal watch retains current quota refresh behavior and safety stops.

### Machine contract and compatibility

- `--json` selects machine rendering regardless of TTY and emits no human text, ANSI, cursor controls, or deprecation warnings on stdout.
- Schema 2.0 remains the default machine schema. Watch is NDJSON (one frame per line); `--snapshot --json` is one object.
- `--schema-version` is valid only with `--json`. Schema 1.1 preserves the 0.16.1 watch frame contract and is available only without `--group` and `--snapshot`, because schema 1.1 cannot express selected groups or snapshot framing. Unsupported combinations fail before reading data; no selection is silently discarded.
- The `--legacy` option is removed from public help and display semantics. Because the schema 2.0 JSON default is a breaking output change from released v0.16.1, ship this contract in v0.18.0. Retain `--legacy` as a hidden deprecated alias through 0.18.x; remove it in 0.19.0. Without `--json`, it renders exactly the new default grouped human dashboard and writes a one-line deprecation warning to stderr. With `--json`, it maps to schema 1.1. It conflicts with explicit schema 2.0. The warning is emitted once before output and never contaminates JSON stdout.
- Schema 1.1 support has a separate documented retirement policy, governed by the **Machine contract and compatibility** section of this document: a future proposal must state the target release, migration note, and deprecation window, and receive cross-peer approval before removal. `--schema-version 1.1` has no sunset in this change. Removing the `--legacy` alias in 0.19.0 does not silently remove the explicit machine schema.

## Architecture

The grouped read model is the single source of display facts for **schema 2.0 JSON, the interactive dashboard, and redirected plain text**. These renderers consume the same selected `groups` payload and preserve the same group order, status, completeness, and values. Rendering must not issue additional provider requests or independently reinterpret evidence.

Introduce a small presentation boundary (module placement to follow existing CLI architecture review) that maps each canonical group ID to a focused formatter. Formatters render known domain fields as tables/cards and give unknown fields safe readable labels rather than serializing nested dictionaries. Unknown future fields remain visible through a bounded key/value fallback so additions are not silently lost, but the fallback is never JSON syntax. Keep the legacy schema 1.1 JSON adapter separate from the human renderer and constrain it through parser validation.

The renderer receives width, height, TTY, color/Unicode capability, and one immutable schema 2.0 monitor frame. It cannot access the database, provider CLIs, or preference writer. Existing refresh, snapshot, and projection boundaries remain unchanged.

**Explicit compatibility exception:** schema 1.1 is a frozen 0.16.1 wire adapter, not a grouped display renderer. It retains the legacy diagnostic projection needed to reproduce that exact flat frame contract. It cannot represent group selection or snapshot framing, so both are rejected. Golden output captured from 0.16.1 protects the exception from silent drift. This adapter is explicitly separate from the shared schema 2.0 group read model; any future retirement is governed by the documented machine-schema policy.

The golden fixture was captured by installing the published `peerhub==0.16.1` wheel into an isolated directory, creating a minimal CoreStore database, patching only provider quota refresh, and invoking that wheel's actual `monitor --json --cycles 1` command. The test normalizes only the temporary database path and read timestamp; frame shape, field ordering, and values remain captured output. The fixture is not hand-authored.

The human renderers must not import or call this schema 1.1 adapter. Parser validation isolates it to `--json --schema-version 1.1` (or the deprecated alias) before database access. A golden fixture is the frozen behavioral specification; new fields belong only in schema 2.0.

## Resolved dialectical points

1. **A colored heading is not a visual dashboard.** All three peers confirmed that the current grouped path is formatted JSON and that the old route contains the actual visual primitives.
2. **Do not drop group selection to preserve the old dashboard.** Promote the useful visual primitives into a grouped renderer over the shared monitor read model.
3. **Do not overload a display mode with a machine schema.** The public path is now the normal dashboard; the short-lived `--legacy` alias is hidden/deprecated, and schema 1.1 is selected explicitly under `--json`.
4. **Keep 0.16.1 machine consumers viable.** Preserve schema 1.1 NDJSON watch frames explicitly; reject combinations the schema cannot represent.
5. **Do not claim semantic task success.** Bars and meters represent measured bounded quantities; execution reports only delivery certainty/latency.
6. **Do not let terminal geometry change data selection.** Narrow layouts compact or stack; selected groups are never silently dropped.

CC, CX, and AG each returned a final `APPROVE` after the schema 1.1 watch ambiguity was resolved: schema 1.1 watch is one frame per cycle, while schema 2.0 snapshot is one object. No peer reported a remaining design blocker.

## RED-first acceptance plan

Write these tests before implementation and observe the expected failures:

1. **Default TTY visual contract:** fake interactive TTY with VT support; bare `monitor --cycles 1` renders group panel boundaries, aligned values and meaningful bars/markers, uses cursor redraw, and contains no raw JSON blocks. A live-terminal smoke confirms the view is legible.
2. **Redirected human contract:** bare monitor and `--snapshot` produce aligned human group sections with no ANSI/cursor controls, no JSON objects, and all selected group IDs. Watch appends timestamped frames.
3. **Renderer parity:** for a fixed frame fixture, human rich, human plain, and schema 2.0 JSON expose the same selected group IDs, order, statuses, and underlying values. A guard ensures the human renderer never calls `json.dumps` on group data.
4. **Group selection:** repeated IDs, duplicates, canonical ordering, `all` exclusivity, explicit selection overriding workspace/global/package defaults, and rendering exactly the selected groups.
5. **Group catalog/preferences:** all nine IDs, descriptions, default membership, preference source/reason, reset/set round trips, and catalog availability without opening the database.
6. **Machine schema 2.0:** JSON on a TTY stays valid and unstyled; watch is one valid NDJSON object per line; snapshot is exactly one JSON object.
7. **Machine schema 1.1:** use captured 0.16.1 golden output (do not hand-author the fixture); compare the existing frame shape and NDJSON behavior. Reject schema selection without `--json`, schema 1.1 with `--group` or `--snapshot`, `--legacy --json --group`, and contradictory `--legacy --json --schema-version 2.0`. Verify the human renderer never imports or calls the schema 1.1 adapter.
8. **Alias:** `--legacy` is absent from help, command inventory, and completion but accepted through 0.18.x. Without JSON, its stdout equals bare monitor stdout and only stderr gains the warning. With JSON, its stdout equals schema 1.1 output. Add a version-keyed test requiring removal at 0.19.0.
9. **Width/height/accessibility:** widths 20, 40, 60, 80, 120, and 200; narrow layouts stack/compact; every selected group remains present; no color-only state; ASCII fallback, `NO_COLOR`, `TERM=dumb`, and Windows/non-UTF console behavior.
10. **Snapshot/watch isolation:** snapshot does not refresh quota or mutate DB/config; one-second redraw does not query DB or providers; collection still occurs at the configured interval; existing agy token-use stop remains intact.
11. **Partial/error truth:** unavailable, stale, truncated, and partial groups render explicit status. Execution text never says task success or success rate.
12. **CLI contract/docs:** update `command_inventory.json`, README and changelog; prove catalog/default/selection/schema flags and alias behavior. If adding a new extension module, update both architecture registries. No `docs/m1_spec` edit is planned.

## Implementation and release sequence

1. Write/update this decision record and make the prior group review explicitly historical.
2. Add RED tests and the captured schema 1.1 fixture from the previous stable release.
3. Implement the shared group-aware human presentation boundary without changing the DB schema, group catalog, default group selection, provider collection, or read model.
4. Update CLI help, alias validation, command inventory, README, changelog, status, and docs map. Use `v0.18.0` for this breaking correction because released v0.16.1 users receive a new default JSON contract and the `v0.17.0` tag already exists for the superseded draft; keep that tag fixed and retain the alias through 0.18.x, removing it in 0.19.0.
5. Run focused monitor tests, deterministic suite, pyright, inventory checks, and hosted CI. Obtain final peer code cross-review.
6. From a clean merged release SHA, rerun G3 and G4 for that exact candidate, attach candidate-bound evidence, and publish only after all release gates pass.
