"""Grader for profiles-hard-2026-11-10 (tiny integer language interpreter).

Usage: python grade.py <response.md> [-v]

Extracts the first ```python block (else the first fenced block, else the
whole file), runs it in a separate, isolated Python worker process, calls
evaluate() on every hidden case with a per-case time limit, and prints
`SCORE n/N` as the last line. A worker that hangs or crashes is killed and a
fresh one resumes at the next case, so one runaway case cannot poison the
others. The worker blocks network, subprocesses and file writes with an audit
hook. The grader itself writes no files and uses no network. -v lists failures.
"""
import json
import os
import queue
import re
import subprocess
import sys
import threading

CASE_TIMEOUT = 2.0   # seconds per case (task.md promises callers 1 s)
LOAD_TIMEOUT = 10.0  # seconds to exec the response code
E = 10 ** 18
S, Z, U, N, O = "SYNTAX", "DIV_ZERO", "UNDEFINED", "NEG_EXP", "OVERFLOW"
TAB, LF, CR, VT, NBSP = chr(9), chr(10), chr(13), chr(11), chr(0xA0)

CASES = [
    # --- precedence, associativity, unary (12)
    ("1+2*3", 7),
    ("(1+2)*3", 9),
    ("2^3^2", 512),
    ("-2^2", -4),
    ("(-2)^2", 4),
    ("2^--1", 2),
    ("1-2-3", -4),
    ("100/10/5", 2),
    ("2*3%4", 2),
    ("7-+-3", 10),
    ("1++2", 3),
    ("-2^-0", -1),
    # --- truncating division and remainder (7)
    ("-7/2", -3),
    ("7/-2", -3),
    ("-7%2", -1),
    ("7%-2", 1),
    ("-7%-2", -1),
    ("-9/4*4+-9%4", -9),
    ("-1/2", 0),
    # --- statements and variables (7)
    ("x=4; y=x*x; y-x", 12),
    ("x = 3;", 3),
    ("x=1; x=x+1; x", 2),
    ("a_b1 = 5; _ = a_b1 * 2; _", 10),
    ("x=2;x^x^x", 16),
    ("q = 5; q", 5),
    ("q", U),  # variables must not leak between calls
    # --- powers with special bases / big exponents (6)
    ("0^0", 1),
    ("(-1)^999999999999999999", -1),
    ("1^1000000000000000000", 1),
    ("2^59", 576460752303423488),
    ("(-2)^59", -576460752303423488),
    ("10^18", E),
    # --- boundaries (4)
    ("-10^18", -E),
    ("1000000000000000000", E),
    ("10^18/-1", -E),
    ("-0", 0),
    # --- whitespace and size limits (6)
    (" " + TAB + LF + " 1 " + LF + "+" + TAB + "2 " + LF, 3),
    ("(" * 100 + "1+1" + ")" * 100, 2),
    ("-" * 99 + "7", -7),
    ("2" + "^1" * 100, 2),
    ("1+" * 3000 + "1", 3001),
    ("1-" * 999 + "1", -998),
    # --- SYNTAX: structure (16)
    ("", S),
    ("   " + LF, S),
    (";", S),
    ("1;;2", S),
    (";1", S),
    ("1 2", S),
    ("2(3)", S),
    ("(1+2", S),
    ("1+", S),
    ("2^", S),
    ("a = b = 1", S),
    ("(a) = 1", S),
    ("1 = 1", S),
    ("x = (y = 1)", S),
    ("2 ** 3", S),
    ("x == 1", S),
    # --- SYNTAX: lexing (12)
    ("007", S),
    ("1.5", S),
    ("1_000", S),          # int("1_000") would accept it
    ("12abc", S),
    ("X = 1", S),
    (chr(0xFF12), S),      # fullwidth digit two
    (chr(0x663) + "+1", S),  # Arabic-Indic digit three
    ("2" + chr(0xB2), S),  # superscript two (str.isdigit() is True)
    ("1" + CR + "+1", S),
    ("1" + NBSP + "+ 1", S),
    ("1" + VT + "+1", S),
    (chr(0xE9) + " = 1", S),  # e-acute name
    # --- SYNTAX beats runtime errors (3)
    ("1/0; 1+", S),
    ("undefined_var; (", S),
    ("10^19; 1 2", S),
    # --- runtime errors and their order (14)
    ("1/0", Z),
    ("1%0", Z),
    ("x = x + 1", U),
    ("y + 1/0", U),
    ("1/0 + y", Z),
    ("2 ^ -y", U),
    ("(1/0) ^ -1", Z),
    ("2^-(1/0)", Z),
    ("0 * (1/0)", Z),
    ("x = 1; y = 1/0; x", Z),
    ("1/0; 99999999999999999999", Z),
    ("99999999999999999999; 1/0", O),
    ("0^-1", N),
    ("1 ^ -1", N),
    # --- NEG_EXP vs OVERFLOW and overflow detection (13)
    ("2^-1^2", N),
    ("10^19", O),
    ("2^62", O),
    ("(-2)^61", O),
    ("1000000000000000001 - 1", O),
    ("10^18 * 10 / 10", O),
    ("-10^18 - 1", O),
    ("0 * 10^19", O),
    ("-(-1000000000000000001)", O),
    ("9^9^9", O),
    ("2^1000000000000000000", O),
    ("0^1000000000000000000000", O),
    ("1" + "0" * 5000 + " * 0", O),  # int() refuses > 4300 digits
]


def extract(src):
    m = re.search(r"```python[^\n]*\n(.*?)```", src, re.S)
    if not m:
        m = re.search(r"```[^\n]*\n(.*?)```", src, re.S)
    return m.group(1) if m else src


def same(got, want):
    kind, val = got
    if isinstance(want, int):
        return kind == "int" and val == hex(want)
    return kind == "str" and val == want


# ----------------------------------------------------------------- worker
_BLOCKED = ("socket.", "subprocess.", "os.system", "os.exec", "os.spawn",
            "os.posix_spawn", "os.startfile", "os.fork", "os.kill",
            "os.remove", "os.unlink", "os.rename", "os.replace", "os.rmdir",
            "os.mkdir", "os.chmod", "os.truncate", "os.link", "os.symlink",
            "shutil.", "ctypes.", "winreg.", "urllib.", "http.", "ftplib.",
            "smtplib.", "webbrowser.", "_winapi.")


def _guard(event, args):
    if event.startswith(_BLOCKED):
        raise PermissionError("blocked by grader: " + event)
    if event == "open":
        mode, flags = args[1], args[2]
        if isinstance(mode, str):
            if any(c in mode for c in "wax+"):
                raise PermissionError("blocked by grader: write open")
        elif isinstance(flags, int):
            w = (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND
                 | os.O_TRUNC)
            if flags & w:
                raise PermissionError("blocked by grader: write open")


class _Null:
    def write(self, s):
        return len(s)

    def flush(self):
        pass


def worker(path, start):
    out = sys.stdout
    sys.dont_write_bytecode = True

    def emit(*parts):
        out.write("@@ " + json.dumps(parts) + "\n")
        out.flush()

    with open(path, encoding="utf-8") as fh:
        code = extract(fh.read())
    sys.setrecursionlimit(1000)
    sys.addaudithook(_guard)
    sys.stdout = sys.stderr = _Null()
    ns = {"__name__": "solution"}
    try:
        exec(compile(code, "<response>", "exec"), ns)
        fn = ns["evaluate"]
    except BaseException as e:  # noqa: BLE001 - anything means load failure
        emit("loadfail", type(e).__name__ + ": " + str(e)[:200])
        return
    emit("loaded")
    for i in range(start, len(CASES)):
        try:
            r = fn(CASES[i][0])
            if type(r) is int:
                got = ["int", hex(r)]
            elif type(r) is str:
                got = ["str", r[:64]]
            else:
                got = ["other", type(r).__name__]
        except BaseException as e:  # noqa: BLE001 - includes RecursionError
            got = ["exc", type(e).__name__]
        emit("case", i, got)


# ----------------------------------------------------------------- parent
def spawn(path, start):
    cmd = [sys.executable, "-I", "-B", os.path.abspath(__file__),
           "--worker", path, str(start)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, encoding="utf-8",
                         errors="replace", creationflags=flags)
    q = queue.Queue()

    def pump():
        for line in p.stdout:
            if line.startswith("@@ "):
                try:
                    q.put(json.loads(line[3:]))
                except ValueError:
                    pass
        q.put(None)

    threading.Thread(target=pump, daemon=True).start()
    return p, q


def stop(p):
    try:
        p.kill()
        p.wait(5)
    except Exception:  # noqa: BLE001
        pass


def main():
    args = [a for a in sys.argv[1:] if a != "-v"]
    verbose = "-v" in sys.argv[1:]
    if len(args) != 1:
        print("usage: python grade.py <response.md> [-v]")
        print(f"SCORE 0/{len(CASES)}")
        return
    path = os.path.abspath(args[0])
    results = {}
    i = 0
    while i < len(CASES):
        p, q = spawn(path, i)
        try:
            msg = q.get(timeout=LOAD_TIMEOUT)
        except queue.Empty:
            msg = None
        if not msg or msg[0] != "loaded":
            stop(p)
            why = msg[1] if msg and msg[0] == "loadfail" else "no response"
            print("LOAD_FAIL", why)
            break
        while i < len(CASES):
            try:
                msg = q.get(timeout=CASE_TIMEOUT)
            except queue.Empty:
                msg = None
            if not msg or msg[0] != "case" or msg[1] != i:
                results[i] = ["timeout/crash", ""]
                i += 1
                stop(p)
                break
            results[i] = msg[2]
            i += 1
        else:
            stop(p)
    score = 0
    for k, (src, want) in enumerate(CASES):
        got = results.get(k)
        if got and same(got, want):
            score += 1
        elif verbose:
            shown = src if len(src) <= 40 else src[:37] + "..."
            print(f"FAIL #{k} {shown!r}: want {want!r}, got {got}")
    print(f"SCORE {score}/{len(CASES)}")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        worker(sys.argv[2], int(sys.argv[3]))
    else:
        main()
