from __future__ import annotations

import ast
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import domain.governance_broker as governance_broker
from domain import (
    DOMAIN_REGISTRY,
    IsolatedDomainContext,
)
from runner import run_fixture


class GovernanceBrokerDomainTests(unittest.TestCase):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent
        / "fixtures"
    )
    POSITIVE_IDS = (
        "GB-02",
        "GB-06",
    )
    NEGATIVE_IDS = tuple(
        f"{fixture_id}-NEG-01"
        for fixture_id in POSITIVE_IDS
    )
    SCOPE_BOUNDARY = (
        "INJECTED_CAS_STALENESS_ONLY__"
        "NORMALIZE_INSIDE_DRAIN_ORDERING_UNPROVEN"
    )

    def _fixture_path(
        self,
        fixture_id: str,
    ) -> Path:
        return (
            self.FIXTURE_DIRECTORY
            / f"{fixture_id}.json"
        )

    def _load_script(
        self,
        fixture_id: str,
    ) -> dict[str, Any]:
        return json.loads(
            self._fixture_path(
                fixture_id
            ).read_text(encoding="utf-8")
        )

    def _context(
        self,
        root: Path,
    ) -> IsolatedDomainContext:
        return IsolatedDomainContext(
            root=root,
            clock=(1,),
            ids=(
                "run-test",
                "event-test",
            ),
        )

    def _verify(
        self,
        fixture_id: str,
        root: Path,
    ):
        return DOMAIN_REGISTRY.verify(
            self._load_script(
                fixture_id
            )["domain_case"],
            fixture_id,
            self._context(root),
        )

    def test_module_has_no_real_os_access(self) -> None:
        source = Path(
            governance_broker.__file__
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

    def test_fixture_inputs_satisfy_closed_vectors(
        self,
    ) -> None:
        for fixture_id in (
            self.POSITIVE_IDS
            + self.NEGATIVE_IDS
        ):
            with self.subTest(
                fixture_id=fixture_id
            ):
                raw_inputs = self._load_script(
                    fixture_id
                )["domain_case"]["inputs"]
                self.assertEqual(
                    (
                        governance_broker
                        .validate_governance_broker_inputs(
                            fixture_id,
                            raw_inputs,
                        )
                    ),
                    raw_inputs,
                )

    def test_positive_oracle_adapter_pairs_match(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)

            for fixture_id in self.POSITIVE_IDS:
                with self.subTest(
                    fixture_id=fixture_id
                ):
                    root = parent / fixture_id
                    root.mkdir()
                    result = self._verify(
                        fixture_id,
                        root,
                    )
                    self.assertTrue(result.passed)
                    self.assertEqual(
                        result.domain_verification[
                            "status"
                        ],
                        "PASS",
                    )

    def test_gb02_candidate_scope_and_cas_rejection(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self._verify(
                "GB-02",
                Path(temporary),
            ).domain_expected["output"]

        self.assertEqual(
            output["rule_tier"],
            "CANDIDATE",
        )
        self.assertEqual(
            output["decision"],
            "CAS_STALE_REJECTED",
        )

        details = output["details"]
        self.assertEqual(
            details["scope_boundary"],
            self.SCOPE_BOUNDARY,
        )
        self.assertEqual(
            details["drain_summary"],
            {
                "processed": 2,
                "committed": 1,
                "failed": 1,
            },
        )
        self.assertEqual(
            [
                record["disposition"]
                for record in details["requests"]
            ],
            [
                "COMMITTED",
                "REJECTED_STALE_CAS",
            ],
        )
        self.assertEqual(
            details["requests"][1]["error_type"],
            "RuntimeError",
        )
        self.assertEqual(
            details["requests"][1][
                "archive_location"
            ],
            "broker/error",
        )
        self.assertFalse(
            details["requests"][1][
                "mutation_applied"
            ]
        )
        self.assertFalse(
            details["stale_request_mutation_applied"]
        )
        self.assertEqual(
            details["final_revision"],
            18,
        )
        self.assertEqual(
            details["final_value"],
            "gb02-commit",
        )

    def test_gb06_contention_never_transfers_owner(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self._verify(
                "GB-06",
                Path(temporary),
            ).domain_expected["output"]

        self.assertEqual(
            output["rule_tier"],
            "OBS",
        )
        self.assertEqual(
            output["decision"],
            (
                "LOCK_CONTENTION_"
                "REJECTED_AND_RELEASED"
            ),
        )

        details = output["details"]
        self.assertEqual(
            [
                record["exit_code"]
                for record in details["action_records"]
            ],
            [0, 1, 0],
        )
        self.assertEqual(
            [
                record["disposition"]
                for record in details["action_records"]
            ],
            [
                "ACQUIRED",
                "REJECTED_LOCK_HELD",
                "RELEASED",
            ],
        )
        self.assertEqual(
            details["action_records"][1][
                "authoritative_owner_after"
            ],
            "ag",
        )
        self.assertFalse(
            details["action_records"][1][
                "mutation_applied"
            ]
        )
        self.assertEqual(
            details["contending_owner"],
            "cx",
        )
        self.assertEqual(
            details["rejected_because"],
            "locked by ag",
        )
        self.assertEqual(
            details["ownership_sequence"],
            ["ag"],
        )
        self.assertFalse(
            details["terminal_lock_present"]
        )
        self.assertIsNone(
            details["terminal_owner"]
        )

    def test_gb02_stale_commit_fault_is_detected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._verify(
                "GB-02-NEG-01",
                Path(temporary),
            )

        self.assertFalse(result.passed)
        actual = result.domain_actual["output"]
        expected = result.domain_expected["output"]

        self.assertEqual(
            actual["decision"],
            "STALE_CAS_ACCEPTED",
        )
        self.assertEqual(
            actual["details"]["drain_summary"],
            {
                "processed": 2,
                "committed": 2,
                "failed": 0,
            },
        )
        self.assertTrue(
            actual["details"][
                "stale_request_mutation_applied"
            ]
        )
        self.assertEqual(
            actual["details"]["requests"][1][
                "disposition"
            ],
            "COMMITTED",
        )
        self.assertEqual(
            actual["details"]["final_value"],
            "gb02-stale",
        )

        self.assertEqual(
            expected["decision"],
            "CAS_STALE_REJECTED",
        )
        self.assertFalse(
            expected["details"][
                "stale_request_mutation_applied"
            ]
        )

    def test_gb06_transfer_fault_is_detected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._verify(
                "GB-06-NEG-01",
                Path(temporary),
            )

        self.assertFalse(result.passed)
        actual = result.domain_actual["output"]
        expected = result.domain_expected["output"]

        self.assertEqual(
            actual["decision"],
            "LOCK_SILENTLY_TRANSFERRED",
        )
        self.assertEqual(
            actual["details"]["action_records"][1][
                "disposition"
            ],
            "TRANSFERRED",
        )
        self.assertTrue(
            actual["details"]["action_records"][1][
                "mutation_applied"
            ]
        )
        self.assertEqual(
            actual["details"]["ownership_sequence"],
            ["ag", "cx"],
        )
        self.assertTrue(
            actual["details"]["terminal_lock_present"]
        )
        self.assertEqual(
            actual["details"]["terminal_owner"],
            "cx",
        )
        self.assertEqual(
            actual["details"]["action_records"][2][
                "disposition"
            ],
            "REJECTED_NOT_OWNER",
        )

        self.assertEqual(
            expected["details"]["ownership_sequence"],
            ["ag"],
        )
        self.assertFalse(
            expected["details"]["terminal_lock_present"]
        )

    def test_runner_integration(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)

            for fixture_id in (
                self.POSITIVE_IDS
                + self.NEGATIVE_IDS
            ):
                with self.subTest(
                    fixture_id=fixture_id
                ):
                    root = parent / fixture_id
                    record_path = run_fixture(
                        self._fixture_path(
                            fixture_id
                        ),
                        fixture_id,
                        root,
                    )
                    record = json.loads(
                        record_path.read_text(
                            encoding="utf-8"
                        )
                    )
                    positive = (
                        fixture_id
                        in self.POSITIVE_IDS
                    )

                    self.assertEqual(
                        record["status"],
                        (
                            "V1_CAPTURE"
                            if positive
                            else (
                                "DOMAIN_"
                                "ASSERTION_FAILED"
                            )
                        ),
                    )
                    self.assertEqual(
                        record[
                            "domain_verification"
                        ]["status"],
                        (
                            "PASS"
                            if positive
                            else "FAIL"
                        ),
                    )

                    if positive:
                        self.assertEqual(
                            record["coverage_scope"],
                            "SPEC_FAITHFUL",
                        )
                    else:
                        self.assertNotIn(
                            "coverage_scope",
                            record,
                        )

                    for key in (
                        "domain_input",
                        "domain_actual",
                        "domain_expected",
                        "domain_verification",
                    ):
                        artifact = (
                            root
                            / record[
                                "artifact_paths"
                            ][key]
                        )
                        self.assertTrue(
                            artifact.is_file()
                        )
                        self.assertEqual(
                            hashlib.sha256(
                                artifact.read_bytes()
                            ).hexdigest(),
                            record["digests"][
                                f"{key}_raw_sha256"
                            ],
                        )

    def test_domain_outputs_are_deterministic(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            first_root = parent / "first"
            second_root = parent / "second"

            run_fixture(
                self._fixture_path("GB-02"),
                "GB-02",
                first_root,
            )
            run_fixture(
                self._fixture_path("GB-02"),
                "GB-02",
                second_root,
            )

            for name in (
                "domain-input.json",
                "domain-actual.json",
                "domain-expected.json",
                "domain-verification.json",
                "fixture-record.json",
            ):
                self.assertEqual(
                    (first_root / name).read_bytes(),
                    (second_root / name).read_bytes(),
                    name,
                )


if __name__ == "__main__":
    unittest.main()
