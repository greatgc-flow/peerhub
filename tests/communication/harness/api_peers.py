"""Shared helpers of the API peer tests (#378): what the real `ant` prints on an API error, and an evidence reader.

Measured with `ant` 2.9.0 (`ant messages create --format json`) on 2026-11-16: the error object goes to stdout,
pretty-printed over several lines; stderr is one line with the HTTP status; the exit code is 1."""
import json
import sqlite3
from pathlib import Path


def ant_error(status: int, phrase: str, kind: str, message: str, code: int = 1) -> dict:
    """Keyword arguments for the fake `ant` (stdout, stderr, code) in the shape the real `ant` prints."""
    body = {"type": "error", "error": {"type": kind, "message": message}, "request_id": "req_011CVfake0000000000000000"}
    return {"stdout": json.dumps(body, indent=2) + "\n",
            "stderr": f'POST "https://api.anthropic.com/v1/messages": {status} {phrase}\n', "code": code}


ANT_NOT_SIGNED_IN = ant_error(401, "Unauthorized", "authentication_error", "x-api-key header is required")
ANT_UNKNOWN_MODEL = ant_error(404, "Not Found", "not_found_error", "model: claude-nope-0")
ANT_NO_CREDIT = ant_error(400, "Bad Request", "invalid_request_error",
                          "Your credit balance is too low to access the Anthropic API. "
                          "Please go to Plans & Billing to upgrade or purchase credits.")


def evidence_kinds(db: Path) -> list[str]:
    with sqlite3.connect(db) as conn:
        return [row[0] for row in conn.execute("SELECT kind FROM bridge_evidence ORDER BY seq")]
