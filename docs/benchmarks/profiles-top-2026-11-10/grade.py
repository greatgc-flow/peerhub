"""Grader for profiles-top-2026-11-10 (backtracking regex dialect).

Usage: python grade.py <response.md> [-v]

Extracts the first ```python block (else the first fenced block, else the
whole file), runs it in a separate, isolated Python worker process, calls
search(pattern, text) on every hidden case with a per-case time limit, and
prints `SCORE n/N` as the last line. A worker that hangs or crashes is killed
and a fresh one resumes at the next case, so one runaway case cannot poison
the others. The worker blocks network, subprocesses and file writes with an
audit hook. The grader itself writes no files and uses no network. -v lists
failures.
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
ERR = "ERROR"
LF, CR, VT = chr(10), chr(13), chr(11)
AB = "ab" * 499 + "c"

CASES = [
    # --- basics (7)
    ("abc", "xxabcx", ((2, 5),)),
    ("", "abc", ((0, 0),)),
    ("x*", "abc", ((0, 0),)),
    ("a.c", "a" + LF + "c abc", ((4, 7),)),
    ("a$", "a" + LF, None),
    ("(a)(b)?", "ac", ((0, 1), (0, 1), None)),
    ("(a)?c|b", "ab", ((1, 2), None)),          # nothing leaks across starts
    # --- priority: alternation order, greedy vs lazy (12)
    ("(a|ab)(c|bcd)(d*)", "abcd", ((0, 4), (0, 1), (1, 4), (4, 4))),
    ("(a*)(a*)", "aaa", ((0, 3), (0, 3), (3, 3))),
    ("(a*?)(a*)", "aaa", ((0, 3), (0, 0), (0, 3))),
    ("(a+?)(a*)", "aaa", ((0, 3), (0, 1), (1, 3))),
    ("(a{2,3}?)(a*)", "aaaa", ((0, 4), (0, 2), (2, 4))),
    ("a??b", "ab", ((0, 2),)),
    ("(a??)(a)", "aa", ((0, 1), (0, 0), (0, 1))),
    ("(x|xy)z|(xyz)", "xyz", ((0, 3), (0, 2), None)),
    ("<(.+)>", "<a><b>", ((0, 6), (1, 5))),
    ("<(.+?)>", "<a><b>", ((0, 3), (1, 2))),
    ("(a|b)*?c", "abac", ((0, 4), (2, 3))),
    ("(ab|a)(bc|c)?", "abc", ((0, 3), (0, 2), (2, 3))),
    # --- captures across iterations: reset and restore (15)
    ("(a|b)*", "abab", ((0, 4), (3, 4))),
    ("(?:(a)|b)+", "ab", ((0, 2), None)),
    ("(?:(a)|(b))+", "ab", ((0, 2), None, (1, 2))),
    ("(?:(a)|(b))+", "ba", ((0, 2), (1, 2), None)),
    ("((a)|b)+", "ab", ((0, 2), (1, 2), None)),
    ("(?:(a)b|ac)+", "abac", ((0, 4), None)),
    ("(a(b)?)+", "aba", ((0, 3), (2, 3), None)),
    ("(?:(x)|y){2}", "xy", ((0, 2), None)),
    ("(?:(a)|b)*?b", "ab", ((0, 2), (0, 1))),
    ("(?:(a)|b)*?c", "abc", ((0, 3), None)),
    ("((a)|(b))*", "ab", ((0, 2), (1, 2), None, (1, 2))),
    ("(a)*ab", "aab", ((0, 3), (0, 1))),
    ("(?:(a)b)*", "aba", ((0, 2), (0, 1))),
    ("(?:(a)|b)*\\1", "aba", ((0, 2), None)),
    ("(?:(a)(?:(b)|c)*)+", "abca", ((0, 4), (3, 4), None)),
    # --- empty iterations (15)
    ("(a*)*", "b", ((0, 0), None)),
    ("(a*)+", "b", ((0, 0), (0, 0))),
    ("(a*)*", "aa", ((0, 2), (0, 2))),
    ("(a*)+", "aa", ((0, 2), (0, 2))),
    ("(a|)*", "aab", ((0, 2), (1, 2))),
    ("(|a)*", "aa", ((0, 2), (1, 2))),
    ("(|a)+", "aa", ((0, 2), (1, 2))),
    ("(|a){2,}", "a", ((0, 1), (0, 1))),
    ("(a?)*?b", "aab", ((0, 3), (1, 2))),
    ("(?:a*)*b", "aac", None),
    ("(a*?)*", "aa", ((0, 2), (1, 2))),
    ("(?:()|a)*", "a", ((0, 1), None)),
    ("(()|a)+", "a", ((0, 1), (0, 1), None)),
    ("(a?)+?b", "b", ((0, 1), (0, 0))),
    ("(a?){2}b", "ab", ((0, 2), (1, 1))),
    # --- backreferences (13)
    ("(a+)\\1", "aaaa", ((0, 4), (0, 2))),
    ("(a)|\\1b", "b", ((0, 1), None)),
    ("\\1(a)", "aa", ((0, 1), (0, 1))),
    ("(a)\\10", "aa0", ((0, 3), (0, 1))),
    ("(a)\\2", "a", ERR),
    ("(?:(a)|b\\1)+", "aba", ((0, 3), (2, 3))),
    ("(b)?a\\1c", "ac", ((0, 2), None)),
    ("(\\w+)\\s+\\1", "hello world world", ((6, 17), (6, 11))),
    ("(a*)b\\1+", "aabaaaa", ((0, 7), (0, 2))),
    ("(a*)b\\1*", "baa", ((0, 1), (0, 0))),
    ("([ab])\\1", "abba", ((1, 3), (1, 2))),
    ("(a\\1)", "aa", ((0, 1), (0, 1))),
    ("(\\w)+-\\1", "abc-c", ((0, 5), (2, 3))),
    # --- character classes and class escapes (19)
    ("[]", "a]", None),
    ("[]a]", "]a", ERR),
    ("[^]", LF, ((0, 1),)),
    ("[a-]+", "-a-b", ((0, 3),)),
    ("[-a]+", "a-x", ((0, 2),)),
    ("[a-c-e]+", "b-ed", ((0, 3),)),
    ("[--/]+", "-./0", ((0, 3),)),
    ("[z-a]", "a", ERR),
    ("[\\d-z]", "-", ERR),
    ("[a-\\d]", "a", ERR),
    ("[\\1]", "1", ERR),
    ("[\\w.]+", "a.b,c", ((0, 3),)),
    ("[a^]+", "^a^", ((0, 3),)),
    ("[\\t-\\n]+", "a" + LF + "\t" + VT, ((1, 3),)),  # escaped endpoints
    ("\\d+", chr(0x663) + chr(0x664) + " 12", ((3, 5),)),
    ("\\w+", chr(0xE9) + "_a1", ((1, 4),)),
    ("\\s+", "a" + CR + VT + "\t " + LF + "b", ((3, 6),)),
    ("\\D\\W\\S", LF + LF + "a", ((0, 3),)),
    (".", CR, ((0, 1),)),
    # --- anchors (6)
    ("x^|b", "ab", ((1, 2),)),
    ("(^a)*b", "aab", ((2, 3), None)),
    ("$a?", "a", ((1, 1),)),
    ("(?:$|a)+", "aa", ((0, 2),)),
    ("a^b|$", "ab", ((2, 2),)),
    ("^*", "", ERR),
    # --- counted quantifiers (9)
    ("a{3}", "aaaa", ((0, 3),)),
    ("a{2,}", "a aaa", ((2, 5),)),
    ("a{0,2}?b", "aab", ((0, 3),)),
    ("(?:a|ab){2,3}c", "aababc", ((0, 6),)),
    ("(a){0}b", "ab", ((1, 2), None)),
    ("a{02}", "aa", ((0, 2),)),
    ("a{2}?", "aaa", ((0, 2),)),
    ("a{1001}", "a", ERR),
    ("a{2,1}", "aa", ERR),
    # --- other syntax (15)
    ("|", "x", ((0, 0),)),
    ("()", "", ((0, 0), (0, 0))),
    ("\\-", "-", ((0, 1),)),
    ("a**", "a", ERR),
    ("a{2}{3}", "aaaaaa", ERR),
    ("a*??", "a", ERR),
    ("a{", "a{", ERR),
    ("a{,3}", "a", ERR),
    ("a}", "a}", ERR),
    ("]", "]", ERR),
    ("(?=a)", "a", ERR),
    ("\\b", "a b", ERR),
    ("a\\", "a", ERR),
    ("a)", "a", ERR),
    ("a|*", "a", ERR),
    # --- long texts (4)
    ("a{1000}", "a" * 1000, ((0, 1000),)),
    (".*", "x" * 1000, ((0, 1000),)),
    ("(a|b)*c", AB, ((0, 999), (997, 998))),
    ("(?:a|b)*?$", "ab" * 500, ((0, 1000),)),
]


def extract(src):
    m = re.search(r"```python[^\n]*\n(.*?)```", src, re.S)
    if not m:
        m = re.search(r"```[^\n]*\n(.*?)```", src, re.S)
    return m.group(1) if m else src


def norm(r):
    """Map a result to a JSON-able, comparable form (lists = tuples)."""
    if r is None:
        return ["none"]
    if type(r) is str:
        return ["str", r[:64]]
    if isinstance(r, (tuple, list)):
        out = []
        for g in r:
            if g is None:
                out.append(None)
            elif (isinstance(g, (tuple, list)) and len(g) == 2
                  and all(type(x) is int for x in g)):
                out.append([g[0], g[1]])
            else:
                return ["other", "bad group item"]
        return ["spans", out]
    return ["other", type(r).__name__]


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
        fn = ns["search"]
    except BaseException as e:  # noqa: BLE001 - anything means load failure
        emit("loadfail", type(e).__name__ + ": " + str(e)[:200])
        return
    emit("loaded")
    for i in range(start, len(CASES)):
        try:
            got = norm(fn(CASES[i][0], CASES[i][1]))
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
    for k, (pat, text, want) in enumerate(CASES):
        got = results.get(k)
        if got == norm(want):
            score += 1
        elif verbose:
            shown = text if len(text) <= 30 else text[:27] + "..."
            print(f"FAIL #{k} {pat!r} on {shown!r}: want {want!r}, got {got}")
    print(f"SCORE {score}/{len(CASES)}")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        worker(sys.argv[2], int(sys.argv[3]))
    else:
        main()
