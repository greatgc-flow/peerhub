"""Compare fresh context injection with native resume (direct CLI calls).

Usage: python -m tools.context_bench --peer cc|cx --turns N
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


def workload(turns):
    rng = random.Random(20261009)
    items = [{"code": f"{rng.randrange(100000):05d}",
              "owner": f"user{rng.randrange(100)}",
              "status": rng.choice(("open", "closed")),
              "note": f"word-{rng.randrange(1000):03d}"} for _ in range(259)]
    document = "\n".join(f"Item {i}: " + " ".join(f"{k} {v}" for k, v in item.items())
                         for i, item in enumerate(items, 1))
    order = list(range(259))
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
    else:
        cmd = ["codex", "exec"] + (["resume", session] if session else [])
        cmd += ["--json", "-m", args.model, "-c", f"model_reasoning_effort={args.effort or 'low'}",
                "--skip-git-repo-check"]
        # Resume inherits the sandbox of the initial exec; its parser has no -s.
        if not session:
            cmd += ["-s", "read-only"]
        cmd += ["-"]
    executable = shutil.which(cmd[0])
    if executable is None:
        raise FileNotFoundError(f"{cmd[0]} is not on PATH")
    proc = subprocess.run([executable, *cmd[1:]], input=prompt, cwd=cwd,
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
        else:
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
    print("\t".join(columns + ["correct", "seconds"]), flush=True)
    totals, history, session, prior_cost, correct_count = [0, 0, 0, 0, 0.0], [], "", 0.0, 0
    prior_usage = None
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
                if strategy == "RESUME":
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
    parser.add_argument("--peer", choices=("cc", "cx"), required=True)
    parser.add_argument("--turns", type=int, required=True)
    parser.add_argument("--gap-seconds", type=float, default=0)
    parser.add_argument("--model")
    parser.add_argument("--effort")
    args = parser.parse_args()
    if not 1 <= args.turns <= 259 or not 0 <= args.gap_seconds < float("inf"):
        raise ValueError("turns must be 1..259 and gap-seconds must be finite and nonnegative")
    args.model = args.model or ("claude-haiku-4-5-20251001" if args.peer == "cc" else "gpt-6-luna")
    document, questions = workload(args.turns)
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
