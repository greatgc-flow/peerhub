from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from runner import run_fixture


class ControlledFakeRunnerTests(unittest.TestCase):
    def _write_script(
        self,
        path: Path,
        document: dict[str, Any],
    ) -> None:
        raw = (
            json.dumps(
                document,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        path.write_text(raw, encoding="utf-8", newline="\n")

    def _read_record(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def test_success_is_byte_deterministic(self) -> None:
        script = {
            "schema_version": 1,
            "clock": [100, 101],
            "ids": [
                "run-deterministic-001",
                "event-deterministic-001",
                "event-deterministic-002",
            ],
            "events": [
                {
                    "type": "SPAWNED",
                    "identity": {
                        "token": "process-deterministic-001",
                        "pid": 4242,
                    },
                },
                {
                    "type": "EXIT",
                    "code": 0,
                },
            ],
            "expect": {
                "terminal_classification": "EXITED",
                "exit_code": 0,
                "ordered_event_types": [
                    "SPAWNED",
                    "EXIT",
                ],
                "cleanup_error_count": 0,
                "effect_certainty": "STARTED",
                "execution_outcome": "SUCCEEDED",
                "process_tokens": [
                    "process-deterministic-001"
                ],
            },
        }

        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            script_path = temporary_path / "script.json"
            self._write_script(script_path, script)

            first_root = temporary_path / "run-a"
            second_root = temporary_path / "run-b"

            first_record_path = run_fixture(
                script_path,
                "EXAMPLE-01",
                first_root,
            )
            second_record_path = run_fixture(
                script_path,
                "EXAMPLE-01",
                second_root,
            )

            first_record = self._read_record(
                first_record_path
            )
            second_record = self._read_record(
                second_record_path
            )

            self.assertEqual(
                first_record["status"],
                "V1_CAPTURE",
            )
            self.assertEqual(
                second_record["status"],
                "V1_CAPTURE",
            )
            self.assertEqual(
                (first_root / "transcript.json").read_bytes(),
                (second_root / "transcript.json").read_bytes(),
            )
            self.assertEqual(
                first_record["digests"],
                second_record["digests"],
            )
            self.assertEqual(
                first_record_path.read_bytes(),
                second_record_path.read_bytes(),
            )

    def test_interruption_is_uncertain_and_not_replayed(
        self,
    ) -> None:
        script = {
            "schema_version": 2,
            "clock": [10],
            "ids": [
                "run-interruption-001",
                "event-interruption-001",
            ],
            "events": [
                {
                    "type": "INTENT_PERSISTED",
                }
            ],
            "interrupt_after_append": 0,
            "expect": {
                "terminal_classification": (
                    "START_UNCERTAIN"
                ),
                "effect_certainty": "MAY_HAVE_STARTED",
                "execution_outcome": "UNKNOWN",
                "ordered_event_types": [
                    "INTENT_PERSISTED"
                ],
                "reducer_replay_idempotent": True,
            },
            "domain_case": {
                "contract_version": 1,
                "fixture_id": "DP-06",
                "oracle_id": (
                    "dispatch_pipe_recovery.dp06."
                    "dispatch_intent_boundary"
                ),
                "oracle_version": 1,
                "inputs": {
                    "injected_command_id": (
                        "dispatch-intent-dp06"
                    ),
                    "append_completed": True,
                    "later_terminal_evidence_present": (
                        False
                    ),
                },
            },
        }

        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            script_path = temporary_path / "script.json"
            self._write_script(script_path, script)

            record_path = run_fixture(
                script_path,
                "DP-06",
                temporary_path / "run",
            )
            record = self._read_record(record_path)

            self.assertEqual(
                record["status"],
                "V1_CAPTURE",
            )
            self.assertTrue(
                record["recovery"]["confirmation_required"]
            )
            self.assertFalse(
                record["recovery"][
                    "external_dispatch_replayed"
                ]
            )
            self.assertEqual(
                record["journaled_event_count"],
                1,
            )
            self.assertEqual(
                record["applied_event_count"],
                1,
            )

    def test_cleanup_error_does_not_replace_timeout(
        self,
    ) -> None:
        script = {
            "schema_version": 1,
            "clock": [20, 21],
            "ids": [
                "run-cleanup-001",
                "event-cleanup-001",
                "event-cleanup-002",
            ],
            "events": [
                {
                    "type": "PROCESS_DEADLINE",
                    "t": 20,
                },
                {
                    "type": "CLEANUP_ERROR",
                    "error": {
                        "code": "TREE_CLEANUP_FAILED"
                    },
                },
            ],
            "expect": {
                "terminal_classification": (
                    "PROCESS_TIMEOUT"
                ),
                "cleanup_error_count": 1,
                "ordered_event_types": [
                    "PROCESS_DEADLINE",
                    "CLEANUP_ERROR",
                ],
            },
        }

        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            script_path = temporary_path / "script.json"
            self._write_script(script_path, script)

            record_path = run_fixture(
                script_path,
                "GENERIC-CLEANUP-PROCESS-TIMEOUT-01",
                temporary_path / "run",
            )
            record = self._read_record(record_path)

            self.assertEqual(
                record["status"],
                "V1_CAPTURE",
            )
            self.assertEqual(
                record["terminal_classification"],
                "PROCESS_TIMEOUT",
            )
            self.assertEqual(
                record["cleanup_error_count"],
                1,
            )

    def test_idempotency_mismatch_uses_frozen_name(
        self,
    ) -> None:
        script = {
            "schema_version": 1,
            "clock": [30, 31],
            "ids": [
                "run-idempotency-001",
                "event-idempotency-001",
                "event-idempotency-002",
            ],
            "events": [
                {
                    "type": "INTENT_PERSISTED",
                    "client_id": "client-001",
                    "command_type": "dispatch",
                    "idempotency_key": "key-001",
                    "payload": {
                        "value": 1
                    },
                },
                {
                    "type": "INTENT_PERSISTED",
                    "client_id": "client-001",
                    "command_type": "dispatch",
                    "idempotency_key": "key-001",
                    "payload": {
                        "value": 2
                    },
                },
            ],
            "expect": {
                "terminal_classification": (
                    "IDEMPOTENCY_PAYLOAD_MISMATCH"
                ),
                "effect_certainty": "NOT_STARTED",
                "execution_outcome": "REJECTED",
            },
        }

        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            script_path = temporary_path / "script.json"
            self._write_script(script_path, script)

            record_path = run_fixture(
                script_path,
                "GENERIC-IDEMPOTENCY-01",
                temporary_path / "run",
            )
            record = self._read_record(record_path)

            self.assertEqual(
                record["status"],
                "V1_CAPTURE",
            )
            self.assertEqual(
                record["terminal_classification"],
                "IDEMPOTENCY_PAYLOAD_MISMATCH",
            )

    def test_unsupported_event_never_claims_capture(
        self,
    ) -> None:
        script = {
            "schema_version": 1,
            "clock": [40],
            "ids": [
                "run-unsupported-001",
                "event-unsupported-001",
            ],
            "events": [
                {
                    "type": "HARD_DEADLINE",
                    "t": 40,
                }
            ],
        }

        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            script_path = temporary_path / "script.json"
            self._write_script(script_path, script)

            record_path = run_fixture(
                script_path,
                "NEGATIVE-01",
                temporary_path / "run",
            )
            record = self._read_record(record_path)

            self.assertEqual(
                record["status"],
                "UNSUPPORTED_EVENT",
            )
            self.assertNotEqual(
                record["status"],
                "V1_CAPTURE",
            )
            self.assertEqual(
                record["journaled_event_count"],
                1,
            )
            self.assertEqual(
                record["applied_event_count"],
                0,
            )

    def test_json_error_precedes_schema_negotiation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            script_path = temporary_path / "script.json"
            script_path.write_bytes(
                b'{"schema_version":999,"events":['
            )

            record_path = run_fixture(
                script_path,
                "NEGATIVE-02",
                temporary_path / "run",
            )
            record = self._read_record(record_path)

            self.assertEqual(
                record["status"],
                "CONTRACT_VIOLATION",
            )
            self.assertEqual(
                record["diagnostics"][0]["code"],
                "JSON_PARSE_ERROR",
            )
            self.assertNotEqual(
                record["diagnostics"][0]["code"],
                "SCRIPT_VERSION_UNSUPPORTED",
            )


if __name__ == "__main__":
    unittest.main()
