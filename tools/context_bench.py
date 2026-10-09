"""Compare fresh context injection with native resume (direct CLI calls).

Usage: python -m tools.context_bench --peer cc|cx|ag --turns N
       [--gap-seconds S] [--model M] [--effort E]
Each strategy uses an empty temporary directory; resume keeps it across turns.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import tempfile
import time


def workload(turns, item_count=259):
    rng = random.Random(20261009)
    items = [{"code": f"{rng.randrange(100000):05d}",
              "owner": f"user{rng.randrange(100)}",
              "status": rng.choice(("open", "closed")),
              "note": f"word-{rng.randrange(1000):03d}"} for _ in range(item_count)]
    document = "\n".join(f"Item {i}: " + " ".join(f"{k} {v}" for k, v in item.items())
                         for i, item in enumerate(items, 1))
    order = list(range(item_count))
    rng.shuffle(order)
    questions = []
    for t, index in enumerate(order[:turns]):
        field = ("owner", "code", "status", "note")[t % 4]
        questions.append((f"What is the {field} of Item {index + 1}? "
                          "Reply with only the value. Do not use tools.", items[index][field]))
    return document, questions


def invoke(args, prompt, cwd, session):
    if args.peer == "cc":
        cmd = ["claude", "-p", "-", "--model", args.model, "--output-format", "json"]
        if args.effort:
            cmd += ["--effort", args.effort]
        if session:
            cmd += ["--resume", session]
    elif args.peer == "cx":
        cmd = ["codex", "exec"] + (["resume", session] if session else [])
        cmd += ["--json", "-m", args.model, "-c", f"model_reasoning_effort={args.effort or 'low'}",
                "--skip-git-repo-check"]
        # Resume inherits the sandbox of the initial exec; its parser has no -s.
        if not session:
            cmd += ["-s", "read-only"]
        cmd += ["-"]
    else:
        # Inline prompts share Windows' roughly 30,000-character argv budget.
        if len(prompt.encode("utf-8")) > 24_000:
            print("# WARNING: ag prompt exceeds 24000 bytes; Windows argv may exceed its limit.")
        cmd = ["agy.exe", "-p", prompt, "--model", args.model, "--output-format", "json"]
        if args.effort:
            cmd += ["--effort", args.effort]
        if session:
            cmd += ["--conversation", session]
    executable = shutil.which(cmd[0])
    if executable is None:
        raise FileNotFoundError(f"{cmd[0]} is not on PATH")
    proc = subprocess.run([executable, *cmd[1:]], input=None if args.peer == "ag" else prompt, cwd=cwd,
                          capture_output=True, encoding="utf-8", errors="replace")
    if proc.stderr:
        print(proc.stderr, end="")
    try:
        if args.peer == "cc":
            result = json.loads(proc.stdout)
            failed = result.get("is_error", False)
            usage = result.get("usage", {})
            answer = result.get("result", "")
            session = result.get("session_id", session)
            cost = float(result.get("total_cost_usd", 0))
            cached = int(usage.get("cache_read_input_tokens", 0))
            written = int(usage.get("cache_creation_input_tokens", 0))
            total = int(usage.get("input_tokens", 0)) + cached + written
        elif args.peer == "cx":
            events = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
            failed = any(e.get("type") in ("error", "turn.failed") for e in events)
            usage, answers = {}, []
            for event in events:
                if event.get("type") == "thread.started":
                    session = event["thread_id"]
                if event.get("type") == "turn.completed":
                    usage = event.get("usage", {})
                item = event.get("item") or {}
                if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                    answers.append(item.get("text", ""))
            answer, cost, written = "\n".join(answers), 0.0, 0
            total, cached = int(usage.get("input_tokens", 0)), int(usage.get("cached_input_tokens", 0))
        else:
            result = json.loads(proc.stdout)
            failed = result.get("is_error", False) or result.get("error")
            usage = result.get("usage", {})
            answer = result.get("response", "")
            session = result.get("conversation_id", session)
            total = int(usage.get("input_tokens", 0))
            cached = int(usage.get("cache_read_tokens", 0))
            cost, written = 0.0, 0
        if proc.returncode or failed or not usage:
            raise ValueError(f"CLI failed or returned no usage (exit {proc.returncode})")
        return answer, session, [total, cached, written, int(usage.get("output_tokens", 0)), cost]
    except (ValueError, TypeError, KeyError):
        print(proc.stdout, end="")
        raise


def benchmark(args, strategy, document, questions):
    print(f"\n{strategy}")
    columns = ["turn", "input_tokens", "cached", "written", "output"]
    if args.peer == "cc":
        columns.append("cost_usd")
    if args.peer == "cx" and strategy == "RESUME":
        print("# Usage after the first turn is the delta from the previous turn of the same session.")
    if args.peer == "ag":
        columns += ["raw_input_tokens", "raw_cache_read_tokens", "raw_output_tokens"]
        if strategy == "RESUME":
            print("# ag usage rule: use deltas only if turn-2 raw input > 1.6x turn-1 raw input; otherwise per-turn.")
    print("\t".join(columns + ["correct", "seconds"]), flush=True)
    totals, history, session, prior_cost, correct_count = [0, 0, 0, 0, 0.0], [], "", 0.0, 0
    prior_usage = None
    ag_cumulative = False
    seconds = 0.0
    with tempfile.TemporaryDirectory(prefix=f"context_bench_{strategy.lower()}_") as cwd:
        for turn, (question, expected) in enumerate(questions, 1):
            if turn > 1:
                time.sleep(args.gap_seconds)
            prompt = question if strategy == "RESUME" and turn > 1 else "\n\n".join(
                [document, *history, question])
            started = time.perf_counter()
            try:
                answer, new_session, values = invoke(args, prompt, cwd, session)
                raw_usage = values[:4]
                if strategy == "RESUME":
                    if args.peer == "ag":
                        if turn == 2:
                            ag_cumulative = (new_session == session and prior_usage is not None
                                             and raw_usage[0] > 1.6 * prior_usage[0])
                            rule = "cumulative deltas" if ag_cumulative else "per-turn raw counters"
                            print(f"# ag usage rule applied: {rule} (criterion: turn-2 input > 1.6x turn-1 input and same conversation).")
                        if ag_cumulative and prior_usage is not None and new_session == session:
                            values[:4] = [current - previous for current, previous
                                          in zip(raw_usage, prior_usage)]
                        prior_usage = raw_usage
                    if args.peer == "cx":
                        cumulative_usage = values[:4]
                        if prior_usage is not None and new_session == session:
                            values[:4] = [current - previous for current, previous
                                          in zip(cumulative_usage, prior_usage)]
                        prior_usage = cumulative_usage
                    session = new_session
                    cumulative = values[4]
                    values[4] -= prior_cost
                    prior_cost = cumulative
                correct = expected in answer
            except (OSError, ValueError, TypeError, KeyError) as exc:
                print(str(exc))
                break
            elapsed = time.perf_counter() - started
            seconds += elapsed
            correct_count += correct
            totals = [a + b for a, b in zip(totals, values)]
            row = [str(turn), *(str(v) for v in values[:2]),
                   str(values[2]) if args.peer == "cc" else "-", str(values[3])]
            if args.peer == "cc":
                row.append(f"{values[4]:.8f}")
            if args.peer == "ag":
                row += [str(raw_usage[i]) for i in (0, 1, 3)]
            print("\t".join(row + ["yes" if correct else "no", f"{elapsed:.3f}"]), flush=True)
            history += [f"[{2 * turn - 1} q{turn} user question] {question}",
                        f"[{2 * turn} a{turn} {args.peer} answer] {answer}"]
            if strategy == "RESUME" and not session:
                print("CLI returned no native session ID; cannot resume.")
                break
    share = totals[1] / totals[0] if totals[0] else 0
    written = str(totals[2]) if args.peer == "cc" else "-"
    cost = f" cost_usd={totals[4]:.8f}" if args.peer == "cc" else ""
    print(f"TOTAL input_tokens={totals[0]} cached={totals[1]} written={written} output={totals[3]}"
          f"{cost} uncached_input={totals[0] - totals[1]} cached_share={share:.2%}"
          f" correct={correct_count}/{len(questions)} seconds={seconds:.3f}")
    return totals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--peer", choices=("cc", "cx", "ag"), required=True)
    parser.add_argument("--turns", type=int, required=True)
    parser.add_argument("--gap-seconds", type=float, default=0)
    parser.add_argument("--model")
    parser.add_argument("--effort")
    args = parser.parse_args()
    if not 1 <= args.turns <= 259 or not 0 <= args.gap_seconds < float("inf"):
        raise ValueError("turns must be 1..259 and gap-seconds must be finite and nonnegative")
    args.model = args.model or {"cc": "claude-haiku-4-5-20251001", "cx": "gpt-6-luna",
                               "ag": "gemini-3.8-flash-low"}[args.peer]
    # Keep ag's default document near 100 lines; larger turn counts still get unique questions.
    document, questions = workload(args.turns, max(100, args.turns) if args.peer == "ag" else 259)
    fresh = benchmark(args, "FRESH", document, questions)
    resume = benchmark(args, "RESUME", document, questions)
    ratio = lambda a, b: f"{a / b:.6f}" if b else "n/a"
    cost = f" cost={ratio(resume[4], fresh[4])}" if args.peer == "cc" else ""
    print(f"resume/fresh{cost} uncached_input={ratio(resume[0] - resume[1], fresh[0] - fresh[1])}")


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt, SystemExit) as exc:
        if not isinstance(exc, SystemExit):
            print(str(exc))
