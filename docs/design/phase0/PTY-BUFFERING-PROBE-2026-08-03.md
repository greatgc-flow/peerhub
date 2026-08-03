# Empirical PTY / Buffering Probe Findings (2026-08-03)

**Author / Runner**: `ag.effort` (Empirical Probe)  
**Prerequisite Context**: `docs/design/SLICE5-KICKOFF-R1.md` ("Process runner backend + lease heartbeat RATIFIED" section)  
**Authorization**: `--allow-governed-mutation` scoped exclusively to this document.  

---

## 1. Executive Summary & Recommendations

An empirical measurement probe was executed on 2026-08-03 across all three active peer CLIs (`cc` / Claude, `ag` / Antigravity, `cx` / Codex) under Windows `subprocess.Popen` pipe execution to test block- vs line-buffering, ANSI escape code retention/stripping, exit/clean completion behavior, and stdin dependency/hang behavior under default and mitigated environments.

### Peer Transport Recommendations

| Peer | Recommended Transport | Mitigation Required | Real ConPTY Needed? |
| :--- | :--- | :--- | :--- |
| **`cc` (Claude)** | **Plain Pipe (`pipe.py`)** | None (Built-in 3s stdin fallback) | **NO** |
| **`ag` (Antigravity)** | **Plain Pipe (`pipe.py`)** | Stdin EOF mitigation (`stdin=DEVNULL` or explicit `close()`) | **NO** |
| **`cx` (Codex)** | **Plain Pipe (`pipe.py`)** | Stdin EOF mitigation (`stdin=DEVNULL` or explicit `close()`) | **NO** |

**Overall Finding**: No active peer CLI (`cc`, `ag`, `cx`) strictly requires Windows ConPTY (`pty.py` / `pywinpty`). Plain `subprocess.Popen` pipes (`pipe.py`) are fully sufficient for all three peers, provided `pipe.py` explicitly handles stdin EOF (e.g. `stdin=subprocess.DEVNULL` or immediate `stdin.close()`) to prevent `ag` and `cx` from waiting for stdin input.

---

## 2. Measurement Methodology & Test Matrix

- **Target Executables**:
  - `cc`: `cmd.exe /c P:\_sys\env\nodejs\npm-global\claude.cmd -p "say ok" --dangerously-skip-permissions`
  - `ag`: `P:\_sys\tools\agy\agy.exe -p "say ok" --dangerously-skip-permissions`
  - `cx`: `cmd.exe /c P:\_sys\env\nodejs\npm-global\codex.cmd exec "say ok" -s workspace-write`
- **Execution Matrix**: 6 total runs (3 peers × 2 conditions).
  - **Condition 1 (Plain Popen)**: Default environment with `PYTHONUTF8=1`.
  - **Condition 2 (Mitigation)**: Same environment + `PYTHONUNBUFFERED=1`, `FORCE_COLOR=1`, `CLICOLOR_FORCE=1`.
- **Measured Properties**:
  1. **Incremental Streaming**: Whether output arrived incrementally across multiple chunks before process exit.
  2. **ANSI Escape Codes**: Whether ANSI color/formatting escape codes (`\x1b[...]`) were retained in stdout.
  3. **Exit Code & Completion**: Process exit code and clean termination status.
  4. **Stdin Dependency / Hang**: Whether process waited indefinitely for stdin input (killed after 12s timeout if hanging).

---

## 3. Measured Results Matrix

| Peer | Condition | (a) Incremental Streaming | (b) ANSI Codes in stdout | (c) Exit Code & Completion | (d) Stdin Dependency / Hang | Elapsed |
| :--- | :--- | :---: | :---: | :---: | :--- | :---: |
| **`cc`** | Condition 1: Plain Popen (default env) | No (buffered to exit) | Stripped (False) | `0` (Clean) | Emits stderr 3s warning, proceeds cleanly without hanging | 8.75s |
| **`cc`** | Condition 2: Mitigation (`PYTHONUNBUFFERED=1`, `FORCE_COLOR=1`) | No (buffered to exit) | Stripped (False) | `0` (Clean) | Emits stderr 3s warning (ANSI yellow), proceeds cleanly | 7.13s |
| **`ag`** | Condition 1: Plain Popen (default env) | No (buffered to exit) | Stripped (False) | `1` (Timeout/Kill)* | Emits stdout `ok\n`, but hangs waiting for stdin EOF | 12.07s |
| **`ag`** | Condition 2: Mitigation (`PYTHONUNBUFFERED=1`, `FORCE_COLOR=1`) | No (buffered to exit) | Stripped (False) | `1` (Timeout/Kill)* | Emits stdout `ok\n`, but hangs waiting for stdin EOF | 12.08s |
| **`cx`** | Condition 1: Plain Popen (default env) | No (0 chunks) | Stripped (False) | `1` (Timeout/Kill)* | Hangs waiting for stdin input (`Reading additional input from stdin...`) | 16.07s |
| **`cx`** | Condition 2: Mitigation (`PYTHONUNBUFFERED=1`, `FORCE_COLOR=1`) | No (0 chunks) | Stripped (False) | `1` (Timeout/Kill)* | Hangs waiting for stdin input (`Reading additional input from stdin...`) | 16.09s |

*\*Note on `ag` and `cx` timeouts*: Under unclosed `stdin=subprocess.PIPE`, both `ag` and `cx` wait for stdin EOF before terminating. In targeted follow-up verification with `stdin=subprocess.DEVNULL` (or explicit `stdin.close()`), `cx` exited cleanly with code `0` in **4.75s** and `ag` exited cleanly with code `0`.

---

## 4. Per-Peer Detailed Analysis

### 4.1 Claude (`cc`)
- **Buffering / Streaming**: Output arrives as a single final chunk (`"ok\n"`) upon process completion. Plain pipes under `-p` / non-interactive mode produce block-buffered stdout.
- **ANSI Escape Codes**: `cc` strips ANSI escape codes from stdout when stdout is not attached to a TTY. Environment variables `FORCE_COLOR=1` colorize stderr warnings (e.g. `\x1b[33mWarning...\x1b[39m`) but keep stdout clean plain text.
- **Stdin Behavior**: `cc` detects unclosed stdin, logs `Warning: no stdin data received in 3s, proceeding without it...` to stderr after 3 seconds, and automatically proceeds to complete the request without hanging.
- **Verdict**: **Plain Pipe Sufficient (`pipe.py`)**. No ConPTY needed.

### 4.2 Antigravity (`ag`)
- **Buffering / Streaming**: `agy.exe` emits stdout (`"ok\n"`) cleanly into the pipe buffer.
- **ANSI Escape Codes**: Output is plain text without ANSI codes when stdout is redirected to a pipe.
- **Stdin Behavior**: `ag` does not time out on open stdin pipes. If `stdin` is left as an open pipe without EOF, `agy.exe` remains alive waiting for further input. When `stdin` is closed immediately or set to `subprocess.DEVNULL`, `ag` exits cleanly with code `0`.
- **Verdict**: **Plain Pipe Sufficient (`pipe.py`) with Stdin EOF Mitigation**. No ConPTY needed.

### 4.3 Codex (`cx`)
- **Buffering / Streaming**: `codex exec` outputs session metadata and response text (`"ok\n"`) to stdout upon completion.
- **ANSI Escape Codes**: Output is plain text without ANSI escape sequences under pipe redirection.
- **Stdin Behavior**: `codex exec` explicitly attempts to read input from stdin (`Reading additional input from stdin...`). If `stdin` is left open without EOF, `cx` hangs waiting for input. When `stdin` is set to `subprocess.DEVNULL` or closed after spawn, `cx` completes cleanly with exit code `0` in **4.75s**.
- **Directory Requirement**: `codex exec` requires execution inside a valid workspace/git repository (or `--skip-git-repo-check`). Inside `peerhub` (`D:\PortableDev (v2.1)\peerhub`), execution succeeds cleanly.
- **Verdict**: **Plain Pipe Sufficient (`pipe.py`) with Stdin EOF Mitigation**. No ConPTY needed.

---

## 5. Implementation Guidance for Phase 2 (`pipe.py`)

1. **`pipe.py` Default Stdin Handling**: `pipe.py` must default `stdin` to `subprocess.DEVNULL` (or immediately close `proc.stdin` after process spawn when no stdin payload is provided). This completely eliminates the stdin-hang behavior observed in `ag` and `cx`.
2. **ConPTY Dependency (`pywinpty`) Deferred**: Because plain `Popen` pipes satisfy all requirements for `cc`, `ag`, and `cx`, adding `pywinpty` or building complex ConPTY bindings is unnecessary for standard non-interactive peer execution.
3. **Lease Heartbeat Entanglement**: As ratified in `SLICE5-KICKOFF-R1.md`, lease heartbeating must run on a dedicated background worker (Option B) since all three peers block-buffer stdout under plain pipes in non-interactive print mode.

---
*End of Empirical PTY / Buffering Probe Report.*
