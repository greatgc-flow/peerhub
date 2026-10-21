"""Fake `peerhub ask --json` worker for batch tests: argv = <query_file> <outdir> <task_id>; the prompt's first line is `mode:<m>`."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

query_file, outdir, task_id = sys.argv[1:4]
mode = Path(query_file).read_text(encoding="utf-8").splitlines()[0].split(":", 1)[1].strip()
out = Path(outdir)
with open(out / f"events-{task_id}.log", "a", encoding="utf-8") as f:
    f.write(f"S {task_id} {time.time():.4f}\n")


def finish(code: int, payload: object | None) -> None:
    with open(out / f"events-{task_id}.log", "a", encoding="utf-8") as f:
        f.write(f"E {task_id} {time.time():.4f}\n")
    if payload is not None:
        print(json.dumps(payload) if not isinstance(payload, str) else payload)
    sys.exit(code)


def delivered(response: str = "pong") -> dict:
    return {"status": "delivered", "certainty": "TERMINAL", "response": response, "error": None,
            "session": {"handle": "sess_x", "mode": "fresh", "generation": 1}}


if mode == "ok":
    time.sleep(float(os.environ.get("FAKE_WORK_SECONDS", "0.05")))
    finish(0, delivered())
elif mode == "slow":
    time.sleep(0.4)
    finish(0, delivered())
elif mode == "empty":
    finish(0, delivered(""))
elif mode == "notstarted":
    finish(1, {"status": "failed_not_started", "certainty": "NOT_STARTED", "response": None,
               "error": {"code": "delivery_failed", "message": "x"}})
elif mode == "refused":
    finish(1, {"status": "not_executed", "certainty": "STARTED", "task_outcome": "NOT_EXECUTED", "reason": "quota_exhausted",
               "response": None, "error": {"code": "delivery_failed", "message": "x"}})
elif mode == "uncertain":
    finish(1, {"status": "uncertain", "certainty": "STARTED", "response": None,
               "error": {"code": "delivery_failed", "message": "Execution ended as uncertain."}})
elif mode == "claimheld":
    finish(1, {"status": "error", "response": None, "error": {"code": "delivery_failed", "message": "claim held"},
               "guidance": {"code": "ask_claim_held"}})
elif mode == "valueerror":  # whole-command ValueError handling: NOT evidence of NOT_STARTED
    finish(2, {"status": "error", "response": None, "error": {"code": "invalid_arguments", "message": "x"}})
elif mode == "crash":
    sys.exit(3)
elif mode == "garbage":
    finish(0, "\x00 not json {{{")
elif mode == "huge":
    sys.stdout.write("x" * 6_000_000)
    sys.stdout.flush()
    finish(0, None)
elif mode in ("hang", "grandchild"):
    if mode == "grandchild":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        (out / f"gc-{task_id}.pid").write_text(str(child.pid), encoding="utf-8")
    (out / f"w-{task_id}.pid").write_text(str(os.getpid()), encoding="utf-8")
    time.sleep(600)
else:
    sys.exit(99)
