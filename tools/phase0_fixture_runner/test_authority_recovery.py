from __future__ import annotations

import ast
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from domain import (
    DOMAIN_REGISTRY,
    IsolatedDomainContext,
)
from domain import authority_recovery
from runner import run_fixture


class DomainAuthorityRecoveryTests(
    unittest.TestCase
):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent
        / "fixtures"
    )
    POSITIVE_IDS = tuple(
        f"AC-07-{index:02d}"
        for index in range(1, 7)
    )
    NEGATIVE_IDS = tuple(
        f"{fixture_id}-NEG-01"
        for fixture_id in POSITIVE_IDS
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

    def test_module_has_no_real_os_access(
        self,
    ) -> None:
        source = Path(
            authority_recovery.__file__
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
                validated = (
                    authority_recovery
                    .validate_authority_recovery_inputs(
                        fixture_id,
                        raw_inputs,
                    )
                )
                self.assertEqual(
                    validated,
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

    def test_expected_recovery_decisions_are_exact(
        self,
    ) -> None:
        expected = {
            "AC-07-01": (
                "STOPPED_SAFE",
                None,
                "PRE_MARKER_ATTEMPT_PRESERVED",
                "ENGRAM",
                False,
            ),
            "AC-07-02": (
                "RECOVERED",
                None,
                "POST_MARKER_RECEIPT_RESTORE",
                "PEERHUB",
                True,
            ),
            "AC-07-03": (
                "REFUSED",
                "RECEIPT_INTEGRITY_INVALID",
                "RECEIPT_INTEGRITY_INVALID",
                "PEERHUB",
                False,
            ),
            "AC-07-04": (
                "REFUSED",
                "RESTORE_ARTIFACT_DIGEST_MISMATCH",
                "RESTORE_ARTIFACT_DIGEST_MISMATCH",
                "PEERHUB",
                False,
            ),
            "AC-07-05": (
                "ROLLED_BACK",
                None,
                "INVERSE_FENCED_ROLLBACK_COMMITTED",
                "ENGRAM",
                True,
            ),
            "AC-07-06": (
                "REFUSED",
                "PEERHUB_ERA_WRITES_PRESENT",
                "PEERHUB_ERA_WRITES_PRESENT",
                "PEERHUB",
                False,
            ),
        }

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)

            for fixture_id, values in expected.items():
                with self.subTest(
                    fixture_id=fixture_id
                ):
                    root = parent / fixture_id
                    root.mkdir()
                    output = self._verify(
                        fixture_id,
                        root,
                    ).domain_expected["output"]

                    self.assertEqual(
                        (
                            output["decision"],
                            output["error_code"],
                            output["disposition"],
                            output[
                                "authority_after_recovery"
                            ],
                            output["restore_performed"],
                        ),
                        values,
                    )

    def test_pre_marker_failure_preserves_attempt(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self._verify(
                "AC-07-01",
                root,
            )

        output = result.domain_expected["output"]

        self.assertEqual(
            output["authority_after_recovery"],
            "ENGRAM",
        )
        self.assertTrue(
            output["attempt_preserved"]
        )
        self.assertFalse(
            output["transition_receipt_trusted"]
        )
        self.assertFalse(
            output["restore_performed"]
        )
        self.assertEqual(
            output["fresh_transition_replay_count"],
            0,
        )

    def test_post_marker_failure_uses_receipt_artifact(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self._verify(
                "AC-07-02",
                root,
            )

        inputs = result.domain_input["inputs"]
        output = result.domain_expected["output"]

        self.assertTrue(
            output["receipt_integrity_checked"]
        )
        self.assertTrue(
            output["transition_receipt_trusted"]
        )
        self.assertTrue(
            output["backup_digest_checked"]
        )
        self.assertEqual(
            output["restore_artifact_id"],
            inputs["transition_receipt"][
                "restore_artifact_id"
            ],
        )
        self.assertTrue(
            output["restore_performed"]
        )
        self.assertEqual(
            output["fresh_transition_replay_count"],
            0,
        )

    def test_corrupt_receipt_is_not_trusted(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self._verify(
                "AC-07-03",
                root,
            )

        receipt = result.domain_input[
            "inputs"
        ]["transition_receipt"]
        output = result.domain_expected["output"]

        self.assertNotEqual(
            receipt["recorded_receipt_digest"],
            receipt["observed_receipt_digest"],
        )
        self.assertTrue(
            output["receipt_integrity_checked"]
        )
        self.assertFalse(
            output["transition_receipt_trusted"]
        )
        self.assertFalse(
            output["backup_digest_checked"]
        )
        self.assertFalse(
            output["restore_performed"]
        )

    def test_backup_digest_mismatch_refuses_restore(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self._verify(
                "AC-07-04",
                root,
            )

        inputs = result.domain_input["inputs"]
        output = result.domain_expected["output"]

        self.assertNotEqual(
            inputs["transition_receipt"][
                "restore_artifact_digest"
            ],
            inputs["restore_artifact"][
                "observed_digest"
            ],
        )
        self.assertTrue(
            output["transition_receipt_trusted"]
        )
        self.assertTrue(
            output["backup_digest_checked"]
        )
        self.assertFalse(
            output["restore_performed"]
        )
        self.assertIsNone(
            output["restore_artifact_id"]
        )

    def test_rollback_reuses_all_requirements_and_fence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self._verify(
                "AC-07-05",
                root,
            )

        output = result.domain_expected["output"]
        checks = output["rollback_requirement_checks"]

        self.assertTrue(all(checks.values()))
        self.assertTrue(
            output["inverse_fence_checked"]
        )
        self.assertTrue(
            output["inverse_fence_committed"]
        )
        self.assertEqual(
            output["rollback_epoch"],
            43,
        )
        self.assertTrue(
            output["restore_performed"]
        )
        self.assertEqual(
            output["authority_after_recovery"],
            "ENGRAM",
        )

    def test_peerhub_era_writes_are_fully_enumerated(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self._verify(
                "AC-07-06",
                root,
            )

        output = result.domain_expected["output"]

        self.assertEqual(
            output["error_code"],
            "PEERHUB_ERA_WRITES_PRESENT",
        )
        self.assertEqual(
            output["enumerated_peerhub_mutation_ids"],
            [
                "mutation-after-in-scope-01",
                "mutation-after-in-scope-02",
            ],
        )
        self.assertFalse(
            output["inverse_fence_committed"]
        )
        self.assertFalse(
            output["restore_performed"]
        )
        self.assertEqual(
            output["authority_after_recovery"],
            "PEERHUB",
        )

    def test_no_positive_fixture_best_effort_replays(
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
                    output = self._verify(
                        fixture_id,
                        root,
                    ).domain_expected["output"]

                    self.assertEqual(
                        output[
                            "fresh_transition_replay_count"
                        ],
                        0,
                    )

    def test_each_specific_fault_is_detected(
        self,
    ) -> None:
        expected = {
            "AC-07-01-NEG-01": (
                "attempt_preserved",
                False,
                True,
            ),
            "AC-07-02-NEG-01": (
                "fresh_transition_replay_count",
                1,
                0,
            ),
            "AC-07-03-NEG-01": (
                "transition_receipt_trusted",
                True,
                False,
            ),
            "AC-07-04-NEG-01": (
                "restore_performed",
                True,
                False,
            ),
            "AC-07-05-NEG-01": (
                "inverse_fence_committed",
                False,
                True,
            ),
            "AC-07-06-NEG-01": (
                "decision",
                "ROLLED_BACK",
                "REFUSED",
            ),
        }

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)

            for fixture_id, values in expected.items():
                with self.subTest(
                    fixture_id=fixture_id
                ):
                    root = parent / fixture_id
                    root.mkdir()
                    result = self._verify(
                        fixture_id,
                        root,
                    )
                    actual = result.domain_actual["output"]
                    oracle = result.domain_expected["output"]

                    self.assertFalse(result.passed)
                    self.assertEqual(
                        result.domain_verification[
                            "status"
                        ],
                        "FAIL",
                    )
                    self.assertEqual(
                        actual[values[0]],
                        values[1],
                    )
                    self.assertEqual(
                        oracle[values[0]],
                        values[2],
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
            fixture = self._fixture_path(
                "AC-07-06"
            )

            run_fixture(
                fixture,
                "AC-07-06",
                first_root,
            )
            run_fixture(
                fixture,
                "AC-07-06",
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
                    (
                        first_root / name
                    ).read_bytes(),
                    (
                        second_root / name
                    ).read_bytes(),
                    name,
                )


if __name__ == "__main__":
    unittest.main()
