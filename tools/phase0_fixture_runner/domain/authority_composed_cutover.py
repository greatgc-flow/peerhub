from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .authority_drain import (
    AuthorityDrainOracle,
    AuthorityDrainSubjectAdapter,
    validate_authority_drain_inputs,
)
from .authority_fence import (
    AuthorityFenceOracle,
    AuthorityFenceSubjectAdapter,
    validate_authority_fence_inputs,
)
from .authority_identity import (
    AuthorityIdentityOracle,
    AuthorityIdentitySubjectAdapter,
    validate_authority_identity_inputs,
)
from .authority_json_custody import (
    AuthorityJsonCustodyOracle,
    AuthorityJsonCustodySubjectAdapter,
    validate_authority_json_custody_inputs,
)
from .contract import (
    DomainContractError,
    DomainRegistration,
    IsolatedDomainContext,
    require_bool,
    require_exact_fields,
    require_mapping,
    require_nonnegative_int,
    require_string,
)

_BASE_FIXTURES = (
    "AC-COMPOSED-01",
    "AC-COMPOSED-02",
    "AC-COMPOSED-03",
    "AC-COMPOSED-04",
)
_NEGATIVE_SUFFIX = "-NEG-01"
_NEGATIVE_FIXTURE = "AC-COMPOSED-02-NEG-01"

_ORACLE_IDS = {
    "AC-COMPOSED-01": (
        "authority_composed_cutover.accomposed01."
        "golden_ordered_cutover"
    ),
    "AC-COMPOSED-02": (
        "authority_composed_cutover.accomposed02."
        "identity_gate"
    ),
    "AC-COMPOSED-03": (
        "authority_composed_cutover.accomposed03."
        "drain_gate"
    ),
    "AC-COMPOSED-04": (
        "authority_composed_cutover.accomposed04."
        "custody_gate"
    ),
}

_STAGE_NAMES = frozenset(
    {
        "IDENTITY",
        "DRAIN",
        "CUSTODY",
        "MARKER",
    }
)
_PIPELINE_DECISIONS = frozenset(
    {
        "COMMITTED",
        "HALTED",
    }
)


def _base_fixture_id(fixture_id: str) -> str:
    if fixture_id == _NEGATIVE_FIXTURE:
        return "AC-COMPOSED-02"
    if fixture_id in _BASE_FIXTURES:
        return fixture_id
    raise DomainContractError(
        "DOMAIN_FIXTURE_UNSUPPORTED",
        f"fixture_id={fixture_id}",
    )


def _require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} must be an array",
        )
    return value


def _optional_string(
    value: Any,
    path: str,
    *,
    output: bool = False,
) -> str | None:
    if value is None:
        return None
    try:
        return require_string(value, path)
    except DomainContractError as error:
        raise DomainContractError(
            (
                "DOMAIN_OUTPUT_INVALID"
                if output
                else "DOMAIN_INPUT_INVALID"
            ),
            error.detail,
        ) from error


def _validate_component_case(
    value: Any,
    path: str,
    *,
    allowed_fixture_ids: frozenset[str],
    validator: Any,
) -> dict[str, Any]:
    case = require_mapping(value, path)
    require_exact_fields(
        case,
        {"fixture_id", "inputs"},
        path=path,
    )
    fixture_id = require_string(
        case["fixture_id"],
        f"{path}.fixture_id",
    )
    if fixture_id not in allowed_fixture_ids:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path}.fixture_id unsupported={fixture_id}",
        )

    inputs = require_mapping(
        case["inputs"],
        f"{path}.inputs",
    )
    return {
        "fixture_id": fixture_id,
        "inputs": validator(fixture_id, inputs),
    }


def validate_authority_composed_cutover_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the composed scenario and delegate every stage schema."""

    _base_fixture_id(fixture_id)
    inputs = require_mapping(raw_inputs, "inputs")
    require_exact_fields(
        inputs,
        {
            "identity",
            "drain",
            "custody",
            "marker",
        },
        path="inputs",
    )

    identity = _validate_component_case(
        inputs["identity"],
        "inputs.identity",
        allowed_fixture_ids=frozenset(
            {
                "AC-02-01",
                "AC-02-02",
            }
        ),
        validator=validate_authority_identity_inputs,
    )
    drain = _validate_component_case(
        inputs["drain"],
        "inputs.drain",
        allowed_fixture_ids=frozenset(
            {
                "AC-08-01",
                "AC-08-06",
            }
        ),
        validator=validate_authority_drain_inputs,
    )
    custody = _validate_component_case(
        inputs["custody"],
        "inputs.custody",
        allowed_fixture_ids=frozenset(
            {
                "AC-05-01",
                "AC-05-02",
            }
        ),
        validator=validate_authority_json_custody_inputs,
    )
    marker = _validate_component_case(
        inputs["marker"],
        "inputs.marker",
        allowed_fixture_ids=frozenset({"AC-04-04"}),
        validator=validate_authority_fence_inputs,
    )

    return {
        "identity": identity,
        "drain": drain,
        "custody": custody,
        "marker": marker,
    }


def validate_authority_composed_cutover_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the observable pipeline sequence and marker evidence."""

    _base_fixture_id(fixture_id)
    output = require_mapping(raw_output, "output")
    require_exact_fields(
        output,
        {
            "pipeline_decision",
            "halt_stage",
            "executed_stages",
            "identity_disposition",
            "drain_disposition",
            "custody_disposition",
            "marker_attempted",
            "marker_disposition",
            "marker_count",
            "committed_epoch",
        },
        path="output",
    )

    pipeline_decision = require_string(
        output["pipeline_decision"],
        "output.pipeline_decision",
    )
    if pipeline_decision not in _PIPELINE_DECISIONS:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "output.pipeline_decision "
                f"unsupported={pipeline_decision}"
            ),
        )

    halt_stage = _optional_string(
        output["halt_stage"],
        "output.halt_stage",
        output=True,
    )
    if halt_stage is not None and halt_stage not in _STAGE_NAMES:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            f"output.halt_stage unsupported={halt_stage}",
        )

    stages = [
        require_string(
            value,
            f"output.executed_stages[{index}]",
        )
        for index, value in enumerate(
            _require_list(
                output["executed_stages"],
                "output.executed_stages",
            )
        )
    ]
    if (
        not stages
        or any(stage not in _STAGE_NAMES for stage in stages)
        or len(stages) != len(set(stages))
    ):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            "output.executed_stages invalid",
        )

    marker_attempted = require_bool(
        output["marker_attempted"],
        "output.marker_attempted",
    )
    marker_count = require_nonnegative_int(
        output["marker_count"],
        "output.marker_count",
    )
    marker_disposition = _optional_string(
        output["marker_disposition"],
        "output.marker_disposition",
        output=True,
    )

    if (
        pipeline_decision == "COMMITTED"
        and (
            halt_stage is not None
            or stages != [
                "IDENTITY",
                "DRAIN",
                "CUSTODY",
                "MARKER",
            ]
            or not marker_attempted
            or marker_count != 1
            or marker_disposition != "MARKER_COMMITTED"
        )
    ):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            "committed pipeline invariant violated",
        )

    if (
        pipeline_decision == "HALTED"
        and (
            halt_stage is None
            or marker_attempted
            or marker_count != 0
            or marker_disposition is not None
        )
    ):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            "halted pipeline invariant violated",
        )

    return {
        "pipeline_decision": pipeline_decision,
        "halt_stage": halt_stage,
        "executed_stages": stages,
        "identity_disposition": _optional_string(
            output["identity_disposition"],
            "output.identity_disposition",
            output=True,
        ),
        "drain_disposition": _optional_string(
            output["drain_disposition"],
            "output.drain_disposition",
            output=True,
        ),
        "custody_disposition": _optional_string(
            output["custody_disposition"],
            "output.custody_disposition",
            output=True,
        ),
        "marker_attempted": marker_attempted,
        "marker_disposition": marker_disposition,
        "marker_count": marker_count,
        "committed_epoch": require_nonnegative_int(
            output["committed_epoch"],
            "output.committed_epoch",
        ),
    }


def _halted(
    *,
    stage: str,
    executed_stages: list[str],
    identity_disposition: str | None,
    drain_disposition: str | None,
    custody_disposition: str | None,
    marker_epoch: int,
) -> dict[str, Any]:
    return {
        "pipeline_decision": "HALTED",
        "halt_stage": stage,
        "executed_stages": executed_stages,
        "identity_disposition": identity_disposition,
        "drain_disposition": drain_disposition,
        "custody_disposition": custody_disposition,
        "marker_attempted": False,
        "marker_disposition": None,
        "marker_count": 0,
        "committed_epoch": marker_epoch,
    }


def _committed(
    *,
    identity_disposition: str,
    drain_disposition: str,
    custody_disposition: str,
    marker_output: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "pipeline_decision": "COMMITTED",
        "halt_stage": None,
        "executed_stages": [
            "IDENTITY",
            "DRAIN",
            "CUSTODY",
            "MARKER",
        ],
        "identity_disposition": identity_disposition,
        "drain_disposition": drain_disposition,
        "custody_disposition": custody_disposition,
        "marker_attempted": True,
        "marker_disposition": marker_output["disposition"],
        "marker_count": marker_output["marker_count"],
        "committed_epoch": marker_output["committed_epoch"],
    }


class _ComposedPipeline:
    """Calls the existing authority stage implementations in order."""

    def _identity_oracle(
        self,
        case: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        fixture_id = case["fixture_id"]
        return AuthorityIdentityOracle(
            fixture_id
        ).compute_expected(
            fixture_id,
            case["inputs"],
        )

    def _identity_adapter(
        self,
        case: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> Mapping[str, Any]:
        fixture_id = case["fixture_id"]
        return AuthorityIdentitySubjectAdapter(
            fixture_id
        ).execute(
            fixture_id,
            case["inputs"],
            context,
        )

    def _drain_oracle(
        self,
        case: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        fixture_id = case["fixture_id"]
        return AuthorityDrainOracle(fixture_id).compute_expected(
            fixture_id,
            case["inputs"],
        )

    def _drain_adapter(
        self,
        case: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> Mapping[str, Any]:
        fixture_id = case["fixture_id"]
        return AuthorityDrainSubjectAdapter(
            fixture_id
        ).execute(
            fixture_id,
            case["inputs"],
            context,
        )

    def _custody_oracle(
        self,
        case: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        fixture_id = case["fixture_id"]
        return AuthorityJsonCustodyOracle(
            fixture_id
        ).compute_expected(
            fixture_id,
            case["inputs"],
        )

    def _custody_adapter(
        self,
        case: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> Mapping[str, Any]:
        fixture_id = case["fixture_id"]
        return AuthorityJsonCustodySubjectAdapter(
            fixture_id
        ).execute(
            fixture_id,
            case["inputs"],
            context,
        )

    def _marker_oracle(
        self,
        case: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        fixture_id = case["fixture_id"]
        return AuthorityFenceOracle(fixture_id).compute_expected(
            fixture_id,
            case["inputs"],
        )

    def _marker_adapter(
        self,
        case: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> Mapping[str, Any]:
        fixture_id = case["fixture_id"]
        return AuthorityFenceSubjectAdapter(
            fixture_id
        ).execute(
            fixture_id,
            case["inputs"],
            context,
        )

    def _run(
        self,
        raw_inputs: Mapping[str, Any],
        *,
        adapter: bool,
        context: IsolatedDomainContext | None,
    ) -> Mapping[str, Any]:
        marker_epoch = raw_inputs["marker"]["inputs"][
            "admission_epoch"
        ]

        identity = (
            self._identity_adapter(
                raw_inputs["identity"],
                context,
            )
            if adapter
            else self._identity_oracle(raw_inputs["identity"])
        )
        if identity["decision"] != "ACCEPTED":
            return _halted(
                stage="IDENTITY",
                executed_stages=["IDENTITY"],
                identity_disposition=identity["disposition"],
                drain_disposition=None,
                custody_disposition=None,
                marker_epoch=marker_epoch,
            )

        drain = (
            self._drain_adapter(
                raw_inputs["drain"],
                context,
            )
            if adapter
            else self._drain_oracle(raw_inputs["drain"])
        )
        if (
            drain["decision"] != "PROCEED_TO_PRECOMMIT"
            or not drain["marker_eligible"]
        ):
            return _halted(
                stage="DRAIN",
                executed_stages=["IDENTITY", "DRAIN"],
                identity_disposition=identity["disposition"],
                drain_disposition=drain["disposition"],
                custody_disposition=None,
                marker_epoch=marker_epoch,
            )

        custody = (
            self._custody_adapter(
                raw_inputs["custody"],
                context,
            )
            if adapter
            else self._custody_oracle(raw_inputs["custody"])
        )
        if (
            custody["decision"] != "PROCEED"
            or not custody["marker_eligible"]
        ):
            return _halted(
                stage="CUSTODY",
                executed_stages=[
                    "IDENTITY",
                    "DRAIN",
                    "CUSTODY",
                ],
                identity_disposition=identity["disposition"],
                drain_disposition=drain["disposition"],
                custody_disposition=custody["disposition"],
                marker_epoch=marker_epoch,
            )

        marker = (
            self._marker_adapter(
                raw_inputs["marker"],
                context,
            )
            if adapter
            else self._marker_oracle(raw_inputs["marker"])
        )
        if (
            marker["decision"] != "ACCEPTED"
            or marker["marker_count"] != 1
            or marker["disposition"] != "MARKER_COMMITTED"
        ):
            raise DomainContractError(
                "DOMAIN_ADAPTER_INVALID",
                "AC-04 marker reference did not commit once",
            )

        return _committed(
            identity_disposition=identity["disposition"],
            drain_disposition=drain["disposition"],
            custody_disposition=custody["disposition"],
            marker_output=marker,
        )


class AuthorityComposedCutoverOracle(_ComposedPipeline):
    """Pure expected pipeline using the real stage oracle classes."""

    oracle_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        self._base = base_fixture_id
        self.oracle_id = _ORACLE_IDS[base_fixture_id]
        self.fixture_ids = frozenset(
            {
                base_fixture_id,
                (
                    _NEGATIVE_FIXTURE
                    if base_fixture_id == "AC-COMPOSED-02"
                    else base_fixture_id
                ),
            }
        )

    def compute_expected(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if _base_fixture_id(fixture_id) != self._base:
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                f"oracle={self.oracle_id};fixture_id={fixture_id}",
            )
        return self._run(
            raw_inputs,
            adapter=False,
            context=None,
        )


class AuthorityComposedCutoverSubjectAdapter(
    _ComposedPipeline
):
    """Reference composition using real reference adapters."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        self._base = base_fixture_id
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = (
            f"authority_composed_cutover.{label}.reference"
        )
        self.fixture_ids = frozenset({base_fixture_id})

    def execute(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> Mapping[str, Any]:
        if fixture_id != self._base:
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                f"adapter={self.adapter_id};fixture_id={fixture_id}",
            )
        return self._run(
            raw_inputs,
            adapter=True,
            context=context,
        )


class FaultInjectedAuthorityComposedCutoverAdapter(
    _ComposedPipeline
):
    """A real composition defect: marker runs after identity rejection."""

    adapter_version = 1

    def __init__(self) -> None:
        self.adapter_id = (
            "authority_composed_cutover.accomposed02."
            "fault_skip_identity_gate"
        )
        self.fixture_ids = frozenset({_NEGATIVE_FIXTURE})

    def execute(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> Mapping[str, Any]:
        if fixture_id != _NEGATIVE_FIXTURE:
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                f"adapter={self.adapter_id};fixture_id={fixture_id}",
            )

        identity = self._identity_adapter(
            raw_inputs["identity"],
            context,
        )
        marker = self._marker_adapter(
            raw_inputs["marker"],
            context,
        )

        return {
            "pipeline_decision": "COMMITTED",
            "halt_stage": None,
            "executed_stages": [
                "IDENTITY",
                "DRAIN",
                "CUSTODY",
                "MARKER",
            ],
            "identity_disposition": identity["disposition"],
            "drain_disposition": "GATE_SKIPPED",
            "custody_disposition": "GATE_SKIPPED",
            "marker_attempted": True,
            "marker_disposition": marker["disposition"],
            "marker_count": marker["marker_count"],
            "committed_epoch": marker["committed_epoch"],
        }


def authority_composed_cutover_registrations(
) -> tuple[DomainRegistration, ...]:
    """Return the ratified cross-area authority pipeline rows."""

    registrations: list[DomainRegistration] = []

    for base_fixture_id in _BASE_FIXTURES:
        oracle = AuthorityComposedCutoverOracle(
            base_fixture_id
        )
        adapter = AuthorityComposedCutoverSubjectAdapter(
            base_fixture_id
        )
        registrations.append(
            DomainRegistration(
                fixture_id=base_fixture_id,
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=adapter,
                input_validator=(
                    validate_authority_composed_cutover_inputs
                ),
                output_validator=(
                    validate_authority_composed_cutover_output
                ),
            )
        )

    negative_oracle = AuthorityComposedCutoverOracle(
        "AC-COMPOSED-02"
    )
    registrations.append(
        DomainRegistration(
            fixture_id=_NEGATIVE_FIXTURE,
            oracle_id=negative_oracle.oracle_id,
            oracle_version=negative_oracle.oracle_version,
            oracle=negative_oracle,
            adapter=(
                FaultInjectedAuthorityComposedCutoverAdapter()
            ),
            input_validator=(
                validate_authority_composed_cutover_inputs
            ),
            output_validator=(
                validate_authority_composed_cutover_output
            ),
        )
    )

    return tuple(registrations)