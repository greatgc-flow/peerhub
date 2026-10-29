"""One-shot cx model benchmark: deterministic tasks, direct `codex exec` (read-only), 2 reps per cell."""
import json, re, shutil, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CONFIGS = [("gpt-6-luna", "low"), ("gpt-6.1-terra", "low"), ("gpt-6.1-terra", "medium"), ("gpt-6.1-sol", "medium")]
REPS = 2

def primes(n):
    out, k = [], 2
    while len(out) < n:
        if all(k % p for p in out): out.append(k)
        k += 1
    return out

N2 = sum(1 for n in range(1, 10001) if (n % 3 == 0 or n % 5 == 0) and n % 15 != 0)
A3, B3, C3 = sum(primes(10)), "peerhub"[::-1], sum(ch in "aeiou" for ch in "hierarchical")

def check_code(text, tests):
    m = re.findall(r"```(?:python)?\n(.*?)```", text, re.S)
    if not m: return False
    ns = {}
    try:
        exec(m[-1], ns); exec(tests, ns); return True
    except BaseException:
        return False

T1_TESTS = """
assert moving_avg([1,2,3,4], 2) == [1.5,2.5,3.5]
assert moving_avg([5], 1) == [5.0]
assert moving_avg([1,2], 3) == []
assert moving_avg([], 1) == []
"""
T4_TESTS = """
assert merge([[1,3],[2,6],[8,10],[15,18]]) == [[1,6],[8,10],[15,18]]
assert merge([[1,4],[4,5]]) == [[1,5]]
assert merge([[5,6],[1,2],[2,3]]) == [[1,3],[5,6]]
assert merge([[1,10],[2,3],[4,5]]) == [[1,10]]
assert merge([]) == []
assert merge([[3,3]]) == [[3,3]]
"""
TASKS = {
 "bugfix": ("This function should return the moving average of window k (list of floats, empty if len(xs)<k). It has bugs. Keep the function name moving_avg. Return the corrected full function in one ```python block, nothing else.\n```python\ndef moving_avg(xs, k):\n    out = []\n    for i in range(len(xs) - k):\n        out.append(sum(xs[i:i+k]) / k)\n    return out\n```",
            lambda t: check_code(t, T1_TESTS)),
 "count": (f"How many integers n with 1 <= n <= 10000 are divisible by 3 or by 5 but NOT by 15? Reply with only the number.",
           lambda t: re.sub(r"\D", "", t.strip().splitlines()[-1] if t.strip() else "") == str(N2)),
 "json": ("Reply with only a JSON object (no code fence) with integer a = sum of the first 10 primes, string b = the word 'peerhub' reversed, integer c = number of vowels (a,e,i,o,u) in 'hierarchical'.",
          lambda t: _json_ok(t)),
 "merge": ("Write Python `def merge(intervals)` (exactly that name) that merges overlapping or touching closed intervals (input may be unsorted, may be empty) and returns a sorted list of [start, end] lists. Return only one ```python block.",
           lambda t: check_code(t, T4_TESTS)),
}
def _json_ok(t):
    try:
        d = json.loads(t.strip())
        return d == {"a": A3, "b": B3, "c": C3}
    except Exception:
        return False

def run(model, effort, task, rep):
    prompt, ok = TASKS[task]
    argv = [shutil.which("codex"), "exec", "--json", "-m", model, "--skip-git-repo-check", "-s", "read-only", "-c", f"model_reasoning_effort={effort}", "-"]
    t0 = time.time()
    with tempfile.TemporaryDirectory() as d:
        p = subprocess.run(argv, cwd=d, input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=600)
    dt = time.time() - t0
    reply, usage = "", {}
    for line in p.stdout.splitlines():
        try: e = json.loads(line)
        except ValueError: continue
        if (e.get("item") or {}).get("type") == "agent_message": reply = e["item"].get("text", "")
        if e.get("type") == "turn.completed": usage = e.get("usage", {})
    return dict(model=model, effort=effort, task=task, rep=rep, ok=bool(ok(reply)), secs=round(dt, 1), usage=usage, reply=reply[:300])

if __name__ == "__main__":
    jobs = [(m, e, t, r) for m, e in CONFIGS for t in TASKS for r in range(REPS)]
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(lambda j: run(*j), jobs))
    Path(sys.argv[1]).write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(f"expected: count={N2} json=({A3},{B3},{C3})")
    for m, e in CONFIGS:
        rows = [r for r in res if (r["model"], r["effort"]) == (m, e)]
        tok = sum((r["usage"].get("input_tokens", 0) + r["usage"].get("output_tokens", 0)) for r in rows)
        out = sum(r["usage"].get("output_tokens", 0) for r in rows)
        per = {t: sum(r["ok"] for r in rows if r["task"] == t) for t in TASKS}
        print(f"{m}/{e}: pass {sum(r['ok'] for r in rows)}/{len(rows)} {per} avg {sum(r['secs'] for r in rows)/len(rows):.1f}s out_tok {out} total_tok {tok}")
