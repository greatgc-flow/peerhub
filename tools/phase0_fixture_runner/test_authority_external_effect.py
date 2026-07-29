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
from domain import authority_external_effect
from runner import run_fixture


class DomainAuthorityExternalEffectTests(
    unittest.TestCase
):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent
        / "fixtures"
    )
    POSITIVE_IDS = (
        "AC-06-01",
        "AC-06-02",
        "AC-06-03",
        "AC-06-04",
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
            authority_external_effect.__file__
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
                    authority_external_effect
                    .validate_authority_external_effect_inputs(
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

    def test_expected_classifications_are_exact(
        self,
    ) -> None:
        expected = {
            "AC-06-01": (
                "COMPLETED",
                "STORED_TERMINAL_RECEIPT",
                False,
                True,
                False,
            ),
            "AC-06-02": (
                "INCOMPLETE_SAFE",
                "ABSENT_TERMINAL_RECEIPT",
                True,
                False,
                True,
            ),
            "AC-06-03": (
                "INCOMPLETE_SAFE",
                "AMBIGUOUS_PROVIDER_OBSERVATION",
                True,
                False,
                True,
            ),
            "AC-06-04": (
                "INCOMPLETE_SAFE",
                "STORED_INCOMPLETE_SAFE",
                False,
                True,
                True,
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
                            output["disposition"],
                            output["evidence_basis"],
                            output[
                                "provider_evidence_evaluated"
                            ],
                            output["stored_record_reused"],
                            output[
                                "reconciliation_required"
                            ],
                        ),
                        values,
                    )

    def test_stored_receipt_is_returned_unchanged(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self._verify(
                "AC-06-01",
                root,
            )

        stored_receipt = result.domain_input[
            "inputs"
        ]["stored_terminal_receipt"]
        output = result.domain_expected["output"]

        self.assertEqual(
            output["disposition"],
            "COMPLETED",
        )
        self.assertEqual(
            output["returned_terminal_receipt"],
            stored_receipt,
        )
        self.assertEqual(
            output["effect_invocation_count"],
            0,
        )
        self.assertTrue(
            output["replay_blocked"]
        )
        self.assertTrue(
            output["stored_record_reused"]
        )
        self.assertFalse(
            output["provider_evidence_evaluated"]
        )

    def test_absent_receipt_and_zero_provider_evidence_block(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self._verify(
                "AC-06-02",
                root,
            )

        inputs = result.domain_input["inputs"]
        output = result.domain_expected["output"]

        self.assertIsNone(
            inputs["stored_terminal_receipt"]
        )
        self.assertEqual(
            inputs["provider_observations"],
            [],
        )
        self.assertEqual(
            output["provider_observation_count"],
            0,
        )
        self.assertEqual(
            output["disposition"],
            "INCOMPLETE_SAFE",
        )
        self.assertTrue(
            output["provider_evidence_evaluated"]
        )
        self.assertTrue(
            output["reconciliation_required"]
        )
        self.assertEqual(
            output["effect_invocation_count"],
            0,
        )
        self.assertTrue(
            output["replay_blocked"]
        )

    def test_ambiguous_provider_candidates_do_not_complete(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self._verify(
                "AC-06-03",
                root,
            )

        observations = result.domain_input[
            "inputs"
        ]["provider_observations"]
        output = result.domain_expected["output"]

        self.assertEqual(len(observations), 2)
        self.assertTrue(
            all(
                observation["binding_kind"]
                == "CANDIDATE_MATCH"
                for observation in observations
            )
        )
        self.assertEqual(
            output["provider_observation_count"],
            2,
        )
        self.assertEqual(
            output["disposition"],
            "INCOMPLETE_SAFE",
        )
        self.assertIsNone(
            output["returned_terminal_receipt"]
        )
        self.assertTrue(
            output["reconciliation_required"]
        )
        self.assertEqual(
            output["effect_invocation_count"],
            0,
        )

    def test_no_positive_fixture_blindly_replays(
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
                        output["effect_invocation_count"],
                        0,
                    )
                    self.assertTrue(
                        output["replay_blocked"]
                    )

    def test_stored_incomplete_safe_is_reused_without_replay(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self._verify(
                "AC-06-04",
                root,
            )

        output = result.domain_expected["output"]

        self.assertEqual(
            output["disposition"],
            "INCOMPLETE_SAFE",
        )
        self.assertEqual(
            output["evidence_basis"],
            "STORED_INCOMPLETE_SAFE",
        )
        self.assertTrue(
            output["stored_record_reused"]
        )
        self.assertFalse(
            output["provider_evidence_evaluated"]
        )
        self.assertTrue(
            output["reconciliation_required"]
        )
        self.assertEqual(
            output["effect_invocation_count"],
            0,
        )
        self.assertTrue(
            output["replay_blocked"]
        )

    def test_each_specific_fault_is_detected(
        self,
    ) -> None:
        expected = {
            "AC-06-01-NEG-01": (
                "COMPLETED",
                "COMPLETED",
                1,
                0,
            ),
            "AC-06-02-NEG-01": (
                "COMPLETED",
                "INCOMPLETE_SAFE",
                1,
                0,
            ),
            "AC-06-03-NEG-01": (
                "COMPLETED",
                "INCOMPLETE_SAFE",
                0,
                0,
            ),
            "AC-06-04-NEG-01": (
                "INCOMPLETE_SAFE",
                "INCOMPLETE_SAFE",
                1,
                0,
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
                        (
                            actual["disposition"],
                            oracle["disposition"],
                            actual[
                                "effect_invocation_count"
                            ],
                            oracle[
                                "effect_invocation_count"
                            ],
                        ),
                        values,
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
                "AC-06-03"
            )

            run_fixture(
                fixture,
                "AC-06-03",
                first_root,
            )
            run_fixture(
                fixture,
                "AC-06-03",
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
