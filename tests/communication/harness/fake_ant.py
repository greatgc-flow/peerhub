"""Tiny fake `ant` CLI (run as `python fake_ant.py messages create --format json`), for the ca adapter tests (#378).

Reads the request body from stdin, logs one JSON line to FAKE_ANT_LOG (argv, the parsed body, the names of the ANTHROPIC_*
variables it can see, cwd), sleeps FAKE_ANT_SLEEP seconds, waits for the file FAKE_ANT_UNTIL when given, then prints
FAKE_ANT_STDOUT (default: a complete message whose text blocks join to "OK") and FAKE_ANT_STDERR and exits with
FAKE_ANT_EXIT."""
import json
import os
import sys
import time

raw = sys.stdin.buffer.read()
try:
    body = json.loads(raw.decode("utf-8"))
except ValueError:
    body = None
log = os.environ.get("FAKE_ANT_LOG")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"argv": sys.argv[1:], "body": body, "cwd": os.getcwd(),
                                 "anthropic_env": sorted(k for k in os.environ if k.upper().startswith("ANTHROPIC_"))}) + "\n")
time.sleep(float(os.environ.get("FAKE_ANT_SLEEP", "0")))
until = os.environ.get("FAKE_ANT_UNTIL")  # wait until the test creates this file (a bounded safety wait of 120 s)
waited = time.monotonic() + 120
while until and not os.path.exists(until) and time.monotonic() < waited:
    time.sleep(0.05)
default = json.dumps({"type": "message", "role": "assistant", "stop_reason": "end_turn",
                      "content": [{"type": "text", "text": "O"}, {"type": "thinking", "thinking": "hidden"},
                                  {"type": "text", "text": "K"}],
                      "usage": {"input_tokens": 12, "output_tokens": 3}})
sys.stdout.write(os.environ.get("FAKE_ANT_STDOUT", default))
sys.stdout.flush()
sys.stderr.write(os.environ.get("FAKE_ANT_STDERR", ""))
sys.exit(int(os.environ.get("FAKE_ANT_EXIT", "0")))
