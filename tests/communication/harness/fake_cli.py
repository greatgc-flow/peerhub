"""Tiny fake vendor CLI (run as `python fake_cli.py <args>`). Mode via env FAKE_MODE; log via FAKE_LOG; kind via FAKE_KIND.
Modes: ok nonzero hang huge garbage secret_echo secret_fail vendor_error incomplete error_marked noread replay.
Probes (--version/--help) log to FAKE_HELP_LOG; every other launch logs to FAKE_LAUNCH_LOG before reading stdin.
ag stream mode (--input-format in argv): reads stdin to EOF, logs each message to FAKE_LOG, then logs eof_seen.
replay: reads stdin to EOF, discards it, prints FAKE_REPLAY with FAKE_STDERR_FILE on stderr and exit FAKE_EXIT."""
import json
import os
import sys
import time

mode = os.environ.get("FAKE_MODE", "ok")
kind = os.environ.get("FAKE_KIND", "cc")
args = sys.argv[1:]


def append(var, entry):
    path = os.environ.get(var)
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


if args[:1] in (["--version"], ["--help"]):
    append("FAKE_HELP_LOG", {"probe": args[0]})
    if os.environ.get("FAKE_PROBE_HANG") == args[0]:
        time.sleep(600)
if args[:1] == ["--version"]:
    print(os.environ.get("FAKE_VERSION", "9.8.7 (fake)"))
    sys.exit(0)
if args[:1] == ["--help"]:
    print("usage: fake [--resume ID] [--conversation ID] resume" if os.environ.get("FAKE_HELP_RESUME", "1") == "1" else "usage: fake")
    if os.environ.get("FAKE_HELP_STREAM") == "1":
        print("  --input-format string   input format (text, stream-json); stream-json needs --output-format stream-json")
    sys.exit(int(os.environ.get("FAKE_HELP_EXIT", "0")))
append("FAKE_LAUNCH_LOG", {"argv": args})
stream = "--input-format" in args
if mode == "noread":
    print("ready", flush=True)  # after the launch log, so a test can wait for it before killing
    time.sleep(600)
if mode == "replay":
    sys.stdin.buffer.read()
    with open(os.environ["FAKE_REPLAY"], "rb") as replay:
        sys.stdout.buffer.write(replay.read())
    sys.stdout.flush()
    stderr_file = os.environ.get("FAKE_STDERR_FILE")
    if stderr_file:
        with open(stderr_file, "rb") as err:
            sys.stderr.buffer.write(err.read())
        sys.stderr.flush()
    sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
if stream:
    raw = sys.stdin.buffer.read()
    messages = [json.loads(line) for line in raw.decode("utf-8").split("\n") if line.strip()]
    for message in messages:
        append("FAKE_LOG", {"argv": args, "message": message, "raw_newlines": raw.count(b"\n"), "raw_ascii": raw.isascii()})
    append("FAKE_LOG", {"eof_seen": True})
    prompt = messages[0]["message"]["content"] if messages else ""
else:
    prompt = sys.stdin.read() if kind in ("cc", "cx") else args[args.index("-p") + 1]
    log = os.environ.get("FAKE_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"argv": args, "prompt_len": len(prompt), "cwd": os.getcwd(), "prompt": prompt}) + "\n")
secret = os.environ.get("FAKE_SECRET", "")


def emit(text):
    bad = mode in ("vendor_error", "error_marked")
    if kind == "cc":
        print(json.dumps({"type": "system", "subtype": "init", "session_id": "s1"}))
        if mode != "incomplete":  # incomplete: the stream ends without the terminal result event
            print(json.dumps({"type": "result", "subtype": "success", "is_error": bad, "result": text, "session_id": "s1"}))
    elif kind == "cx":
        print(json.dumps({"type": "thread.started", "thread_id": "t1"}))
        print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}))
        if mode == "error_marked":
            print(json.dumps({"type": "turn.failed", "error": {"message": "partial"}}))
        elif mode != "incomplete":
            print(json.dumps({"type": "turn.completed", "usage": {}}))
    elif stream:  # ag stream-json: init, then one terminal result (incomplete: no result)
        print(json.dumps({"event": "init", "conversation_id": "c1"}))
        if mode != "incomplete":
            status = "ERROR" if mode == "error_marked" else "SUCCESS"
            print(json.dumps({"event": "result", "result": {"conversation_id": "c1", "status": status, "response": text,
                                                            "usage": {"input_tokens": 3, "output_tokens": 1}}}))
    else:  # ag has no streamed terminal event: "incomplete" = a truncated JSON document
        doc = json.dumps({"response": text, "conversation_id": "c1", **({"is_error": True} if mode == "error_marked" else {})})
        print(doc[:-1] if mode == "incomplete" else doc)


if mode == "hang":
    time.sleep(600)
elif mode == "huge":
    sys.stdout.write("x" * 5_000_000)
    sys.stdout.flush()
elif mode == "garbage":
    print("\x00\x01 not json at all {{{")
elif mode == "nonzero":
    sys.stderr.write("boom: failure while handling: " + prompt + "\n")
    sys.exit(3)
elif mode == "secret_fail":
    sys.stderr.write(f"auth error token={secret} Bearer abcdefghijkl1234\n")
    sys.exit(2)
elif mode == "secret_echo":
    emit(f"here is {secret}")
elif mode == "vendor_error":
    emit("quota exceeded")
elif mode in ("incomplete", "error_marked"):
    emit("PARTIAL")
else:
    emit("OK")
