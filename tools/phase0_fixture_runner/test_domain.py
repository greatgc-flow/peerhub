from __future__ import annotations

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
from runner import run_fixture


class DomainTransportTests(unittest.TestCase):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent
        / "fixtures"
    )
    POSITIVE_IDS = (
        "DT-02",
        "DT-03",
        "DT-04",
        "DT-05",
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

    def _read_record(
        self,
        path: Path,
    ) -> dict[str, Any]:
        return json.loads(
            path.read_text(encoding="utf-8")
        )

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
        path.write_text(
            raw,
            encoding="utf-8",
            newline="\n",
        )

    def test_positive_oracle_adapter_pairs_match(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            context = IsolatedDomainContext(
                root=Path(temporary),
                clock=(1,),
                ids=(
                    "run-test",
                    "event-test",
                ),
            )

            for fixture_id in self.POSITIVE_IDS:
                with self.subTest(
                    fixture_id=fixture_id
                ):
                    script = self._load_script(
                        fixture_id
                    )
                    result = DOMAIN_REGISTRY.verify(
                        script["domain_case"],
                        fixture_id,
                        context,
                    )
                    self.assertTrue(result.passed)
                    self.assertEqual(
                        result.domain_verification[
                            "status"
                        ],
                        "PASS",
                    )

    def test_fault_injected_pairs_are_detected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            context = IsolatedDomainContext(
                root=Path(temporary),
                clock=(1,),
                ids=(
                    "run-test",
                    "event-test",
                ),
            )

            for fixture_id in self.NEGATIVE_IDS:
                with self.subTest(
                    fixture_id=fixture_id
                ):
                    script = self._load_script(
                        fixture_id
                    )
                    result = DOMAIN_REGISTRY.verify(
                        script["domain_case"],
                        fixture_id,
                        context,
                    )
                    self.assertFalse(result.passed)
                    self.assertEqual(
                        result.domain_verification[
                            "status"
                        ],
                        "FAIL",
                    )
                    self.assertNotEqual(
                        result.domain_actual[
                            "output"
                        ],
                        result.domain_expected[
                            "output"
                        ],
                    )

    def test_expected_outputs_cover_invariants(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            context = IsolatedDomainContext(
                root=Path(temporary),
                clock=(1,),
                ids=(
                    "run-test",
                    "event-test",
                ),
            )
            results = {
                fixture_id: (
                    DOMAIN_REGISTRY.verify(
                        self._load_script(
                            fixture_id
                        )["domain_case"],
                        fixture_id,
                        context,
                    ).domain_expected["output"]
                )
                for fixture_id in self.POSITIVE_IDS
            }

        self.assertEqual(
            results["DT-02"]["canonical_text"],
            "€\n",
        )
        self.assertEqual(
            results["DT-02"]["lines"],
            ["€"],
        )

        classifications = {
            row["timeline_id"]: row["terminal"]
            for row in results[
                "DT-03"
            ]["classifications"]
        }
        self.assertEqual(
            classifications["silence"],
            "SILENCE_TIMEOUT",
        )
        self.assertEqual(
            classifications["process"],
            "PROCESS_TIMEOUT",
        )

        self.assertEqual(
            results["DT-04"]["terminal"],
            "PROCESS_TIMEOUT",
        )
        self.assertEqual(
            results["DT-04"]["effect_certainty"],
            "MAY_HAVE_STARTED",
        )
        self.assertEqual(
            results["DT-04"]["steps"],
            [
                "PROCESS_DEADLINE",
                "SOFT_CANCEL",
                "TERMINATE_TREE",
                "KILL_TREE",
                "RECONCILE_TREE",
            ],
        )

        self.assertEqual(
            results["DT-05"][
                "unresolved_tokens"
            ],
            ["pty-DT-05-child"],
        )
        self.assertEqual(
            results["DT-05"][
                "cleanup_classification"
            ],
            "CANCELLATION_CLEANUP_FAILED",
        )

    def test_positive_runner_integration(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)

            for fixture_id in self.POSITIVE_IDS:
                with self.subTest(
                    fixture_id=fixture_id
                ):
                    root = (
                        temporary_path
                        / fixture_id
                    )
                    record_path = run_fixture(
                        self._fixture_path(
                            fixture_id
                        ),
                        fixture_id,
                        root,
                    )
                    record = self._read_record(
                        record_path
                    )

                    self.assertEqual(
                        record["status"],
                        "V1_CAPTURE",
                    )
                    self.assertEqual(
                        record["coverage_scope"],
                        "SPEC_FAITHFUL",
                    )
                    self.assertEqual(
                        record[
                            "domain_verification"
                        ]["status"],
                        "PASS",
                    )

                    for key in (
                        "domain_input",
                        "domain_actual",
                        "domain_expected",
                        "domain_verification",
                    ):
                        relative = record[
                            "artifact_paths"
                        ][key]
                        artifact = root / relative
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

    def test_negative_runner_integration(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)

            for fixture_id in self.NEGATIVE_IDS:
                with self.subTest(
                    fixture_id=fixture_id
                ):
                    record_path = run_fixture(
                        self._fixture_path(
                            fixture_id
                        ),
                        fixture_id,
                        (
                            temporary_path
                            / fixture_id
                        ),
                    )
                    record = self._read_record(
                        record_path
                    )

                    self.assertEqual(
                        record["status"],
                        "DOMAIN_ASSERTION_FAILED",
                    )
                    self.assertEqual(
                        record[
                            "domain_verification"
                        ]["status"],
                        "FAIL",
                    )
                    self.assertNotIn(
                        "coverage_scope",
                        record,
                    )

    def test_missing_domain_case_fails_closed(
        self,
    ) -> None:
        script = {
            "schema_version": 1,
            "clock": [1],
            "ids": [
                "run-missing-domain",
                "event-missing-domain",
            ],
            "events": [
                {
                    "type": "EXIT",
                    "code": 0,
                }
            ],
            "expect": {
                "terminal_classification": (
                    "EXITED"
                )
            },
        }

        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            script_path = (
                temporary_path
                / "script.json"
            )
            self._write_script(
                script_path,
                script,
            )

            record = self._read_record(
                run_fixture(
                    script_path,
                    "DT-02",
                    temporary_path / "run",
                )
            )

        self.assertEqual(
            record["status"],
            "DOMAIN_VERIFICATION_REQUIRED",
        )
        self.assertEqual(
            record["diagnostics"][0]["code"],
            "DOMAIN_VERIFICATION_REQUIRED",
        )

    def test_outcome_input_is_rejected(
        self,
    ) -> None:
        script = self._load_script("DT-02")
        script["domain_case"]["inputs"][
            "expected"
        ] = {
            "status": "OK",
        }

        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            script_path = (
                temporary_path
                / "tainted.json"
            )
            self._write_script(
                script_path,
                script,
            )

            run_root = temporary_path / "run"
            record = self._read_record(
                run_fixture(
                    script_path,
                    "DT-02",
                    run_root,
                )
            )

            self.assertFalse(
                (
                    run_root
                    / "domain-actual.json"
                ).exists()
            )

        self.assertEqual(
            record["status"],
            "CONTRACT_VIOLATION",
        )
        self.assertEqual(
            record["diagnostics"][0]["code"],
            "ORACLE_INPUT_TAINTED",
        )

    def test_schema_v1_non_domain_is_unchanged(
        self,
    ) -> None:
        script = {
            "schema_version": 1,
            "clock": [1],
            "ids": [
                "run-v1",
                "event-v1",
            ],
            "events": [
                {
                    "type": "EXIT",
                    "code": 0,
                }
            ],
            "expect": {
                "terminal_classification": (
                    "EXITED"
                )
            },
        }

        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            script_path = (
                temporary_path
                / "v1.json"
            )
            self._write_script(
                script_path,
                script,
            )

            record = self._read_record(
                run_fixture(
                    script_path,
                    "EXAMPLE-V1",
                    temporary_path / "run",
                )
            )

        self.assertEqual(
            record["status"],
            "V1_CAPTURE",
        )
        self.assertEqual(
            record["schema_version"],
            1,
        )
        self.assertNotIn(
            "domain_verification",
            record,
        )

    def test_domain_outputs_are_deterministic(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            first_root = (
                temporary_path
                / "first"
            )
            second_root = (
                temporary_path
                / "second"
            )

            run_fixture(
                self._fixture_path("DT-02"),
                "DT-02",
                first_root,
            )
            run_fixture(
                self._fixture_path("DT-02"),
                "DT-02",
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
                        first_root
                        / name
                    ).read_bytes(),
                    (
                        second_root
                        / name
                    ).read_bytes(),
                    name,
                )


if __name__ == "__main__":
    unittest.main()