from __future__ import annotations

import ast
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import domain.coordination_room as coordination_room
from domain import DOMAIN_REGISTRY, IsolatedDomainContext
from runner import run_fixture


class CoordinationRoomDomainTests(unittest.TestCase):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent / "fixtures"
    )
    POSITIVE_IDS = (
        "CR-01",
        "CR-02",
        "CR-03",
        "CR-04",
        "CR-05",
        "CR-06",
    )
    NEGATIVE_IDS = tuple(
        f"{fixture_id}-NEG-01"
        for fixture_id in POSITIVE_IDS
    )

    def _fixture_path(self, fixture_id: str) -> Path:
        return self.FIXTURE_DIRECTORY / f"{fixture_id}.json"

    def _script(self, fixture_id: str) -> dict[str, Any]:
        return json.loads(
            self._fixture_path(fixture_id).read_text(
                encoding="utf-8"
            )
        )

    def _context(self, root: Path) -> IsolatedDomainContext:
        return IsolatedDomainContext(
            root=root,
            clock=(1,),
            ids=("run-test", "event-test"),
        )

    def _verify(self, fixture_id: str, root: Path):
        return DOMAIN_REGISTRY.verify(
            self._script(fixture_id)["domain_case"],
            fixture_id,
            self._context(root),
        )

    def test_module_has_no_real_os_access(self) -> None:
        source = Path(
            coordination_room.__file__
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden_imports = {
            "ctypes",
            "http",
            "multiprocessing",
            "os",
            "pathlib",
            "psutil",
            "requests",
            "shutil",
            "signal",
            "socket",
            "sqlite3",
            "subprocess",
            "urllib",
            "win32",
            "win32api",
            "win32con",
            "win32file",
            "win32process",
        }
        forbidden_calls = {
            "__import__",
            "compile",
            "eval",
            "exec",
            "open",
        }
        imported_roots: set[str] = set()
        called_names: set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".", 1)[0]
                    for alias in node.names
                )
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
            ):
                imported_roots.add(
                    node.module.split(".", 1)[0]
                )
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
            ):
                called_names.add(node.func.id)

        self.assertEqual(
            imported_roots & forbidden_imports,
            set(),
        )
        self.assertEqual(
            called_names & forbidden_calls,
            set(),
        )

    def test_fixture_inputs_satisfy_closed_vectors(self) -> None:
        for fixture_id in self.POSITIVE_IDS + self.NEGATIVE_IDS:
            with self.subTest(fixture_id=fixture_id):
                raw_inputs = self._script(fixture_id)[
                    "domain_case"
                ]["inputs"]
                self.assertEqual(
                    coordination_room.validate_coordination_room_inputs(
                        fixture_id,
                        raw_inputs,
                    ),
                    raw_inputs,
                )

    def test_positive_oracle_adapter_pairs_match(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            for fixture_id in self.POSITIVE_IDS:
                with self.subTest(fixture_id=fixture_id):
                    root = parent / fixture_id
                    root.mkdir()
                    result = self._verify(fixture_id, root)
                    self.assertTrue(result.passed)
                    self.assertEqual(
                        result.domain_verification["status"],
                        "PASS",
                    )

    def test_expected_tiers_and_decisions_are_exact(self) -> None:
        expected = {
            "CR-01": ("OBS", "SESSION_CLOSED"),
            "CR-02": ("OBS", "MESSAGE_READ"),
            "CR-03": ("OBS", "BROADCAST_DELIVERED"),
            "CR-04": ("CANDIDATE", "CHECKPOINT_RECORDED"),
            "CR-05": ("OBS", "HEARTBEAT_ACCEPTED"),
            "CR-06": ("OBS", "SERIALIZED_TRANSITION"),
        }

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            for fixture_id, values in expected.items():
                with self.subTest(fixture_id=fixture_id):
                    root = parent / fixture_id
                    root.mkdir()
                    output = self._verify(
                        fixture_id,
                        root,
                    ).domain_expected["output"]
                    self.assertEqual(
                        (output["rule_tier"], output["decision"]),
                        values,
                    )

    def test_observed_room_and_message_properties(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)

            close_root = parent / "close"
            close_root.mkdir()
            close = self._verify(
                "CR-01",
                close_root,
            ).domain_expected["output"]["details"]

            read_root = parent / "read"
            read_root.mkdir()
            read = self._verify(
                "CR-02",
                read_root,
            ).domain_expected["output"]["details"]

            broadcast_root = parent / "broadcast"
            broadcast_root.mkdir()
            broadcast = self._verify(
                "CR-03",
                broadcast_root,
            ).domain_expected["output"]["details"]

        self.assertTrue(close["state_transition_applied"])
        self.assertEqual(close["close_exit_code"], 0)
        self.assertEqual(close["members"], ["ag", "cx", "cc"])
        self.assertEqual(read["message_id"], 4)
        self.assertEqual(read["thread_id"], "t-c950")
        self.assertEqual(read["terminal_status"], "READ")
        self.assertEqual(read["read_transition_count"], 1)
        self.assertEqual(broadcast["recipient_order"], ["cx", "cc"])
        self.assertEqual(broadcast["message_ids"], [5, 6])

    def test_candidate_checkpoint_records_source_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self._verify(
                "CR-04",
                Path(temporary),
            ).domain_expected["output"]

        details = output["details"]
        self.assertEqual(output["rule_tier"], "CANDIDATE")
        self.assertTrue(details["checkpoint_persisted"])
        self.assertTrue(
            details["immutable_source_reference_recorded"]
        )
        self.assertEqual(
            details["source_reference"]["reference_type"],
            "SOURCE_DIGEST",
        )

    def test_owner_and_single_winner_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)

            heartbeat_root = parent / "heartbeat"
            heartbeat_root.mkdir()
            heartbeat = self._verify(
                "CR-05",
                heartbeat_root,
            ).domain_expected["output"]["details"]

            transition_root = parent / "transition"
            transition_root.mkdir()
            transition = self._verify(
                "CR-06",
                transition_root,
            ).domain_expected["output"]["details"]

        self.assertEqual(
            heartbeat["heartbeat_peer"],
            heartbeat["assigned_peer"],
        )
        self.assertTrue(heartbeat["owner_heartbeat_accepted"])
        self.assertTrue(transition["serialized_transition"])
        self.assertEqual(
            transition["retired_history_ids"],
            ["handoff-retired-01"],
        )
        self.assertEqual(
            transition["active_ids"],
            ["handoff-active-a"],
        )

    def test_each_specific_fault_is_detected(self) -> None:
        expected = {
            "CR-01-NEG-01": (
                "state_transition_applied",
                False,
                True,
            ),
            "CR-02-NEG-01": (
                "read_transition_count",
                2,
                1,
            ),
            "CR-03-NEG-01": (
                "message_ids",
                [6, 5],
                [5, 6],
            ),
            "CR-04-NEG-01": (
                "source_reference",
                None,
                {
                    "reference_type": "SOURCE_DIGEST",
                    "reference_value": "sha256:cr04-frozen-source",
                },
            ),
            "CR-05-NEG-01": (
                "owner_heartbeat_accepted",
                True,
                False,
            ),
            "CR-06-NEG-01": (
                "active_ids",
                ["handoff-active-a", "handoff-active-b"],
                ["handoff-active-a"],
            ),
        }

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            for fixture_id, values in expected.items():
                with self.subTest(fixture_id=fixture_id):
                    root = parent / fixture_id
                    root.mkdir()
                    result = self._verify(fixture_id, root)
                    actual = result.domain_actual["output"]
                    oracle = result.domain_expected["output"]

                    self.assertFalse(result.passed)
                    self.assertEqual(
                        actual["details"][values[0]],
                        values[1],
                    )
                    self.assertEqual(
                        oracle["details"][values[0]],
                        values[2],
                    )

    def test_runner_integration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)

            for fixture_id in self.POSITIVE_IDS + self.NEGATIVE_IDS:
                with self.subTest(fixture_id=fixture_id):
                    root = parent / fixture_id
                    record_path = run_fixture(
                        self._fixture_path(fixture_id),
                        fixture_id,
                        root,
                    )
                    record = json.loads(
                        record_path.read_text(encoding="utf-8")
                    )
                    positive = fixture_id in self.POSITIVE_IDS

                    self.assertEqual(
                        record["status"],
                        (
                            "V1_CAPTURE"
                            if positive
                            else "DOMAIN_ASSERTION_FAILED"
                        ),
                    )
                    self.assertEqual(
                        record["domain_verification"]["status"],
                        "PASS" if positive else "FAIL",
                    )

                    if positive:
                        self.assertEqual(
                            record["coverage_scope"],
                            "SPEC_FAITHFUL",
                        )

                    for key in (
                        "domain_input",
                        "domain_actual",
                        "domain_expected",
                        "domain_verification",
                    ):
                        artifact = root / record["artifact_paths"][key]
                        self.assertTrue(artifact.is_file())
                        self.assertEqual(
                            hashlib.sha256(
                                artifact.read_bytes()
                            ).hexdigest(),
                            record["digests"][f"{key}_raw_sha256"],
                        )


if __name__ == "__main__":
    unittest.main()
