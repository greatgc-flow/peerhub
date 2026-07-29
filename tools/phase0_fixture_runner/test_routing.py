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


class DomainRoutingTests(unittest.TestCase):
    FIXTURE_DIRECTORY = (
        Path(__file__).resolve().parent
        / "fixtures"
    )
    POSITIVE_IDS = (
        "RT-04",
        "RT-05",
        "RT-06",
    )
    NEGATIVE_IDS = tuple(
        f"{fixture_id}-NEG-01"
        for fixture_id in POSITIVE_IDS
    )
    ORACLE_IDS = {
        "RT-04": (
            "routing.rt04.exclusion"
        ),
        "RT-05": (
            "routing.rt05."
            "deterministic_tie_selection"
        ),
        "RT-06": (
            "routing.rt06."
            "pre_dispatch_drift"
        ),
    }

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

    def _case(
        self,
        fixture_id: str,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "contract_version": 1,
            "fixture_id": fixture_id,
            "oracle_id": self.ORACLE_IDS[
                fixture_id
            ],
            "oracle_version": 1,
            "inputs": inputs,
        }

    def test_positive_oracle_adapter_pairs_match(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            context = self._context(
                Path(temporary)
            )

            for fixture_id in self.POSITIVE_IDS:
                with self.subTest(
                    fixture_id=fixture_id
                ):
                    script = self._load_script(
                        fixture_id
                    )
                    result = (
                        DOMAIN_REGISTRY.verify(
                            script["domain_case"],
                            fixture_id,
                            context,
                        )
                    )

                    self.assertTrue(
                        result.passed
                    )
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
            context = self._context(
                Path(temporary)
            )

            for fixture_id in self.NEGATIVE_IDS:
                with self.subTest(
                    fixture_id=fixture_id
                ):
                    script = self._load_script(
                        fixture_id
                    )
                    result = (
                        DOMAIN_REGISTRY.verify(
                            script["domain_case"],
                            fixture_id,
                            context,
                        )
                    )

                    self.assertFalse(
                        result.passed
                    )
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

    def test_expected_outputs_cover_routing_invariants(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            context = self._context(
                Path(temporary)
            )
            outputs = {
                fixture_id: (
                    DOMAIN_REGISTRY.verify(
                        self._load_script(
                            fixture_id
                        )["domain_case"],
                        fixture_id,
                        context,
                    ).domain_expected["output"]
                )
                for fixture_id
                in self.POSITIVE_IDS
            }

        rt04 = {
            row["candidate_id"]: row
            for row in outputs["RT-04"][
                "candidates"
            ]
        }
        self.assertEqual(
            rt04["candidate-eligible"][
                "weight"
            ],
            1,
        )
        self.assertIsNone(
            rt04["candidate-eligible"][
                "exclusion_reason"
            ]
        )
        self.assertEqual(
            rt04["candidate-excluded"][
                "weight"
            ],
            0,
        )
        self.assertEqual(
            rt04["candidate-excluded"][
                "exclusion_reason"
            ],
            "EXCLUDED",
        )
        self.assertEqual(
            rt04["candidate-terminal"][
                "weight"
            ],
            0,
        )
        self.assertEqual(
            rt04["candidate-terminal"][
                "exclusion_reason"
            ],
            "TERMINAL_TIER",
        )
        self.assertEqual(
            outputs["RT-04"][
                "selectable_candidates"
            ],
            [
                "candidate-eligible",
            ],
        )

        self.assertEqual(
            outputs["RT-05"][
                "ordered_candidates"
            ],
            [
                "candidate-a",
                "candidate-b",
            ],
        )
        self.assertEqual(
            outputs["RT-05"][
                "selected_candidate"
            ],
            "candidate-a",
        )

        self.assertEqual(
            outputs["RT-06"]["result"],
            "CONFIGURATION_STALE",
        )
        self.assertEqual(
            outputs["RT-06"][
                "dispatch_count"
            ],
            0,
        )
        self.assertEqual(
            outputs["RT-06"][
                "replanning_input_revision"
            ],
            11,
        )

    def test_rt05_formula_is_independently_pinned(
        self,
    ) -> None:
        script = self._load_script("RT-05")
        inputs = script[
            "domain_case"
        ]["inputs"]

        formula_document = {
            "request_id": inputs["request_id"],
            "snapshot_digest": (
                inputs["snapshot_digest"]
            ),
        }
        formula_bytes = json.dumps(
            formula_document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        expected_seed = hashlib.sha256(
            formula_bytes
        ).digest()
        expected_candidates = sorted(
            inputs["candidate_set"]
        )
        expected_index = (
            int.from_bytes(
                expected_seed[:8],
                byteorder="big",
                signed=False,
            )
            % len(expected_candidates)
        )
        expected_selected = (
            expected_candidates[
                expected_index
            ]
        )

        self.assertEqual(
            expected_seed.hex(),
            (
                "786f77a42116fda27d29a3f12"
                "bc8e854e405845f66818cd0f7"
                "4104e381e500b7"
            ),
        )

        with tempfile.TemporaryDirectory() as temporary:
            output = (
                DOMAIN_REGISTRY.verify(
                    script["domain_case"],
                    "RT-05",
                    self._context(
                        Path(temporary)
                    ),
                ).domain_expected["output"]
            )

        self.assertEqual(
            output["audit_seed"],
            expected_seed.hex(),
        )
        self.assertEqual(
            output["selection_index"],
            expected_index,
        )
        self.assertEqual(
            output["ordered_candidates"],
            expected_candidates,
        )
        self.assertEqual(
            output["selected_candidate"],
            expected_selected,
        )

    def test_rt05_selection_is_input_order_invariant(
        self,
    ) -> None:
        script = self._load_script("RT-05")
        original_inputs = script[
            "domain_case"
        ]["inputs"]
        permuted_inputs = {
            **original_inputs,
            "candidate_set": list(
                reversed(
                    original_inputs[
                        "candidate_set"
                    ]
                )
            ),
        }

        with tempfile.TemporaryDirectory() as temporary:
            context = self._context(
                Path(temporary)
            )
            original = (
                DOMAIN_REGISTRY.verify(
                    script["domain_case"],
                    "RT-05",
                    context,
                ).domain_expected["output"]
            )
            permuted = (
                DOMAIN_REGISTRY.verify(
                    self._case(
                        "RT-05",
                        permuted_inputs,
                    ),
                    "RT-05",
                    context,
                ).domain_expected["output"]
            )

        self.assertEqual(
            original,
            permuted,
        )

    def test_rt06_matching_revision_admits_one_dispatch(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = DOMAIN_REGISTRY.verify(
                self._case(
                    "RT-06",
                    {
                        (
                            "frozen_configuration_"
                            "revision"
                        ): 11,
                        (
                            "current_configuration_"
                            "revision"
                        ): 11,
                    },
                ),
                "RT-06",
                self._context(
                    Path(temporary)
                ),
            )

        output = result.domain_expected[
            "output"
        ]
        self.assertTrue(result.passed)
        self.assertEqual(
            output["result"],
            "DISPATCH_ADMITTED",
        )
        self.assertEqual(
            output["dispatch_count"],
            1,
        )
        self.assertIsNone(
            output[
                "replanning_input_revision"
            ]
        )

    def test_runner_integration_positive_and_negative(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)

            for fixture_id in (
                self.POSITIVE_IDS
                + self.NEGATIVE_IDS
            ):
                with self.subTest(
                    fixture_id=fixture_id
                ):
                    root = (
                        temporary_path
                        / fixture_id
                    )
                    record = self._read_record(
                        run_fixture(
                            self._fixture_path(
                                fixture_id
                            ),
                            fixture_id,
                            root,
                        )
                    )

                    expected_status = (
                        "V1_CAPTURE"
                        if fixture_id
                        in self.POSITIVE_IDS
                        else (
                            "DOMAIN_"
                            "ASSERTION_FAILED"
                        )
                    )
                    expected_domain_status = (
                        "PASS"
                        if fixture_id
                        in self.POSITIVE_IDS
                        else "FAIL"
                    )

                    self.assertEqual(
                        record["status"],
                        expected_status,
                    )
                    self.assertEqual(
                        record[
                            "domain_verification"
                        ]["status"],
                        expected_domain_status,
                    )

                    if (
                        fixture_id
                        in self.POSITIVE_IDS
                    ):
                        self.assertEqual(
                            record[
                                "coverage_scope"
                            ],
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
                                (
                                    f"{key}_"
                                    "raw_sha256"
                                )
                            ],
                        )

    def test_domain_outputs_are_deterministic(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            first_root = (
                temporary_path / "first"
            )
            second_root = (
                temporary_path / "second"
            )

            run_fixture(
                self._fixture_path("RT-05"),
                "RT-05",
                first_root,
            )
            run_fixture(
                self._fixture_path("RT-05"),
                "RT-05",
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