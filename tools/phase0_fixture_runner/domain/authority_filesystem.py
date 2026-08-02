from __future__ import annotations

from collections.abc import Mapping
from typing import Any

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

_BASE_FIXTURES = tuple(
    f"AC-01-{index:02d}"
    for index in range(1, 9)
)
_NEGATIVE_SUFFIX = "-NEG-01"

_ORACLE_IDS = {
    "AC-01-01": (
        "authority_filesystem.ac0101.local_ntfs"
    ),
    "AC-01-02": (
        "authority_filesystem.ac0102.exfat_rejection"
    ),
    "AC-01-03": (
        "authority_filesystem.ac0103.fat_rejection"
    ),
    "AC-01-04": (
        "authority_filesystem.ac0104.network_rejection"
    ),
    "AC-01-05": (
        "authority_filesystem.ac0105.wal_rejection"
    ),
    "AC-01-06": (
        "authority_filesystem.ac0106.custody_rejection"
    ),
    "AC-01-07": (
        "authority_filesystem.ac0107.resolved_target_trust"
    ),
    "AC-01-08": (
        "authority_filesystem.ac0108.alias_lock_identity"
    ),
}

_FILESYSTEMS = frozenset(
    {
        "NTFS",
        "EXFAT",
        "FAT",
        "SMB",
        "UNKNOWN",
    }
)
_STORAGE_SCOPES = frozenset(
    {
        "LOCAL",
        "NETWORK",
    }
)
_NODE_KINDS = frozenset(
    {
        "ALIAS",
        "PHYSICAL",
    }
)
_DECISIONS = frozenset(
    {
        "ACCEPTED",
        "REJECTED",
    }
)
_PROBE_STAGES = frozenset(
    {
        "FILESYSTEM",
        "WAL_SHARED_MEMORY",
        "LOCK_RENAME_CUSTODY",
        "COMPLETE",
    }
)
_KEY_SOURCES = frozenset(
    {
        "RESOLVED_IDENTITY",
        "PRESENTED_PATH",
    }
)
_LOCK_DISPOSITIONS = frozenset(
    {
        "ACQUIRED",
        "CONTENDED",
    }
)


def _base_fixture_id(fixture_id: str) -> str:
    for base in _BASE_FIXTURES:
        if fixture_id in {
            base,
            f"{base}{_NEGATIVE_SUFFIX}",
        }:
            return base

    raise DomainContractError(
        "DOMAIN_FIXTURE_UNSUPPORTED",
        f"fixture_id={fixture_id}",
    )


def _require_enum(
    value: Any,
    allowed: frozenset[str],
    path: str,
    *,
    output: bool = False,
) -> str:
    actual = require_string(value, path)
    if actual not in allowed:
        raise DomainContractError(
            (
                "DOMAIN_OUTPUT_INVALID"
                if output
                else "DOMAIN_INPUT_INVALID"
            ),
            f"{path} unsupported={actual}",
        )
    return actual


def _require_optional_string(
    value: Any,
    path: str,
) -> str | None:
    if value is None:
        return None
    return require_string(value, path)


def _require_list(
    value: Any,
    path: str,
) -> list[Any]:
    if not isinstance(value, list):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} must be an array",
        )
    return value


def _validate_entry(
    value: Any,
    path: str,
) -> dict[str, Any]:
    entry = require_mapping(value, path)
    require_exact_fields(
        entry,
        {
            "presented_path",
            "entry_node_id",
        },
        path=path,
    )
    return {
        "presented_path": require_string(
            entry["presented_path"],
            f"{path}.presented_path",
        ),
        "entry_node_id": require_string(
            entry["entry_node_id"],
            f"{path}.entry_node_id",
        ),
    }


def _validate_node(
    value: Any,
    path: str,
) -> dict[str, Any]:
    node = require_mapping(value, path)
    kind = _require_enum(
        node.get("node_kind"),
        _NODE_KINDS,
        f"{path}.node_kind",
    )

    if kind == "ALIAS":
        require_exact_fields(
            node,
            {
                "node_id",
                "node_kind",
                "reported_filesystem",
                "target_node_id",
            },
            path=path,
        )
        return {
            "node_id": require_string(
                node["node_id"],
                f"{path}.node_id",
            ),
            "node_kind": kind,
            "reported_filesystem": _require_enum(
                node["reported_filesystem"],
                _FILESYSTEMS,
                f"{path}.reported_filesystem",
            ),
            "target_node_id": require_string(
                node["target_node_id"],
                f"{path}.target_node_id",
            ),
        }

    require_exact_fields(
        node,
        {
            "node_id",
            "node_kind",
            "reported_filesystem",
            "resolved_filesystem",
            "storage_scope",
            "redirected",
            "virtualized",
            "volume_guid",
            "file_id",
        },
        path=path,
    )
    return {
        "node_id": require_string(
            node["node_id"],
            f"{path}.node_id",
        ),
        "node_kind": kind,
        "reported_filesystem": _require_enum(
            node["reported_filesystem"],
            _FILESYSTEMS,
            f"{path}.reported_filesystem",
        ),
        "resolved_filesystem": _require_enum(
            node["resolved_filesystem"],
            _FILESYSTEMS,
            f"{path}.resolved_filesystem",
        ),
        "storage_scope": _require_enum(
            node["storage_scope"],
            _STORAGE_SCOPES,
            f"{path}.storage_scope",
        ),
        "redirected": require_bool(
            node["redirected"],
            f"{path}.redirected",
        ),
        "virtualized": require_bool(
            node["virtualized"],
            f"{path}.virtualized",
        ),
        "volume_guid": _require_optional_string(
            node["volume_guid"],
            f"{path}.volume_guid",
        ),
        "file_id": _require_optional_string(
            node["file_id"],
            f"{path}.file_id",
        ),
    }


def _validate_capabilities(
    value: Any,
    path: str,
) -> dict[str, Any]:
    capabilities = require_mapping(value, path)
    fields = {
        "stable_identity_available",
        "wal_mode_available",
        "shared_memory_available",
        "exclusive_lock_available",
        "atomic_rename_available",
        "namespace_custody_available",
    }
    require_exact_fields(
        capabilities,
        fields,
        path=path,
    )
    return {
        field: require_bool(
            capabilities[field],
            f"{path}.{field}",
        )
        for field in sorted(fields)
    }


def _validate_lock_attempt(
    value: Any,
    path: str,
) -> dict[str, Any]:
    attempt = require_mapping(value, path)
    require_exact_fields(
        attempt,
        {
            "owner_id",
            "presented_path",
        },
        path=path,
    )
    return {
        "owner_id": require_string(
            attempt["owner_id"],
            f"{path}.owner_id",
        ),
        "presented_path": require_string(
            attempt["presented_path"],
            f"{path}.presented_path",
        ),
    }


def _validate_graph_topology(
    entries: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
) -> None:
    node_table = {
        node["node_id"]: node
        for node in nodes
    }

    for entry in entries:
        current_id = entry["entry_node_id"]
        visited: set[str] = set()

        while True:
            if current_id in visited:
                raise DomainContractError(
                    "DOMAIN_INPUT_INVALID",
                    (
                        "inputs.resolution_graph "
                        f"contains_cycle_at={current_id}"
                    ),
                )
            visited.add(current_id)

            node = node_table[current_id]
            if node["node_kind"] == "PHYSICAL":
                break

            current_id = node["target_node_id"]


def validate_authority_filesystem_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a closed, abstract AC-01 observation schema."""

    base = _base_fixture_id(fixture_id)
    inputs = require_mapping(raw_inputs, "inputs")
    require_exact_fields(
        inputs,
        {
            "resolution_graph",
            "capability_observations",
            "lock_attempts",
        },
        path="inputs",
    )

    graph = require_mapping(
        inputs["resolution_graph"],
        "inputs.resolution_graph",
    )
    require_exact_fields(
        graph,
        {
            "entries",
            "nodes",
        },
        path="inputs.resolution_graph",
    )

    raw_entries = _require_list(
        graph["entries"],
        "inputs.resolution_graph.entries",
    )
    raw_nodes = _require_list(
        graph["nodes"],
        "inputs.resolution_graph.nodes",
    )
    if not raw_entries or not raw_nodes:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "inputs.resolution_graph entries and "
                "nodes must be non-empty"
            ),
        )

    entries = [
        _validate_entry(
            value,
            (
                "inputs.resolution_graph."
                f"entries[{index}]"
            ),
        )
        for index, value in enumerate(raw_entries)
    ]
    nodes = [
        _validate_node(
            value,
            (
                "inputs.resolution_graph."
                f"nodes[{index}]"
            ),
        )
        for index, value in enumerate(raw_nodes)
    ]

    node_ids = [
        node["node_id"]
        for node in nodes
    ]
    if len(set(node_ids)) != len(node_ids):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "inputs.resolution_graph has "
                "duplicate node_id"
            ),
        )
    node_id_set = set(node_ids)

    presented_paths = [
        entry["presented_path"]
        for entry in entries
    ]
    if len(set(presented_paths)) != len(
        presented_paths
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "inputs.resolution_graph has "
                "duplicate presented_path"
            ),
        )

    for entry in entries:
        if entry["entry_node_id"] not in node_id_set:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "inputs.resolution_graph missing "
                    f"entry_node_id="
                    f"{entry['entry_node_id']}"
                ),
            )

    for node in nodes:
        if (
            node["node_kind"] == "ALIAS"
            and node["target_node_id"]
            not in node_id_set
        ):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "inputs.resolution_graph missing "
                    f"target_node_id="
                    f"{node['target_node_id']}"
                ),
            )

    _validate_graph_topology(entries, nodes)

    capabilities = _validate_capabilities(
        inputs["capability_observations"],
        "inputs.capability_observations",
    )

    raw_attempts = _require_list(
        inputs["lock_attempts"],
        "inputs.lock_attempts",
    )
    attempts = [
        _validate_lock_attempt(
            value,
            f"inputs.lock_attempts[{index}]",
        )
        for index, value in enumerate(raw_attempts)
    ]

    known_paths = set(presented_paths)
    owner_ids: set[str] = set()

    for attempt in attempts:
        if attempt["presented_path"] not in (
            known_paths
        ):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "inputs.lock_attempts references "
                    "unknown presented_path="
                    f"{attempt['presented_path']}"
                ),
            )

        if attempt["owner_id"] in owner_ids:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "inputs.lock_attempts has duplicate "
                    f"owner_id={attempt['owner_id']}"
                ),
            )
        owner_ids.add(attempt["owner_id"])

    if base == "AC-01-08":
        if (
            len(entries) != 2
            or len(attempts) != 2
        ):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "AC-01-08 requires exactly two "
                    "entries and two lock attempts"
                ),
            )
    elif len(entries) != 1 or attempts:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                f"{base} requires exactly one entry "
                "and zero lock attempts"
            ),
        )

    return {
        "resolution_graph": {
            "entries": entries,
            "nodes": nodes,
        },
        "capability_observations": capabilities,
        "lock_attempts": attempts,
    }


def _validate_identity(
    value: Any,
    path: str,
) -> dict[str, str]:
    identity = require_mapping(value, path)
    require_exact_fields(
        identity,
        {
            "volume_guid",
            "file_id",
        },
        path=path,
    )
    return {
        "volume_guid": require_string(
            identity["volume_guid"],
            f"{path}.volume_guid",
        ),
        "file_id": require_string(
            identity["file_id"],
            f"{path}.file_id",
        ),
    }


def _validate_lock_key(
    value: Any,
    path: str,
) -> dict[str, Any]:
    key = require_mapping(value, path)
    require_exact_fields(
        key,
        {
            "key_source",
            "volume_guid",
            "file_id",
            "presented_path",
        },
        path=path,
    )

    source = _require_enum(
        key["key_source"],
        _KEY_SOURCES,
        f"{path}.key_source",
        output=True,
    )
    volume_guid = _require_optional_string(
        key["volume_guid"],
        f"{path}.volume_guid",
    )
    file_id = _require_optional_string(
        key["file_id"],
        f"{path}.file_id",
    )
    presented_path = _require_optional_string(
        key["presented_path"],
        f"{path}.presented_path",
    )

    if source == "RESOLVED_IDENTITY":
        if (
            volume_guid is None
            or file_id is None
            or presented_path is not None
        ):
            raise DomainContractError(
                "DOMAIN_OUTPUT_INVALID",
                (
                    f"{path} resolved key requires "
                    "volume/file and forbids "
                    "presented_path"
                ),
            )
    elif (
        presented_path is None
        or volume_guid is not None
        or file_id is not None
    ):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                f"{path} path key requires "
                "presented_path and forbids "
                "volume/file"
            ),
        )

    return {
        "key_source": source,
        "volume_guid": volume_guid,
        "file_id": file_id,
        "presented_path": presented_path,
    }


def _validate_acquisition(
    value: Any,
    path: str,
    key_count: int,
) -> dict[str, Any]:
    acquisition = require_mapping(value, path)
    require_exact_fields(
        acquisition,
        {
            "owner_id",
            "disposition",
            "lock_key_index",
        },
        path=path,
    )

    key_index = require_nonnegative_int(
        acquisition["lock_key_index"],
        f"{path}.lock_key_index",
    )
    if key_index >= key_count:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                f"{path}.lock_key_index "
                f"out_of_range={key_index}"
            ),
        )

    return {
        "owner_id": require_string(
            acquisition["owner_id"],
            f"{path}.owner_id",
        ),
        "disposition": _require_enum(
            acquisition["disposition"],
            _LOCK_DISPOSITIONS,
            f"{path}.disposition",
            output=True,
        ),
        "lock_key_index": key_index,
    }


def validate_authority_filesystem_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate oracle and adapter AC-01 output."""

    _base_fixture_id(fixture_id)
    output = require_mapping(
        raw_output,
        "output",
    )
    require_exact_fields(
        output,
        {
            "decision",
            "error_code",
            "probe_stage",
            "workspace_identities",
            "migration_lock_keys",
            "lock_acquisitions",
            "zero_database_mutations",
            "zero_marker_mutations",
            "zero_legacy_mutations",
            "zero_provider_calls",
        },
        path="output",
    )

    error_code = _require_optional_string(
        output["error_code"],
        "output.error_code",
    )
    if error_code not in {
        None,
        "FILESYSTEM_UNSUPPORTED",
    }:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "output.error_code "
                f"unsupported={error_code}"
            ),
        )

    raw_identities = output[
        "workspace_identities"
    ]
    raw_keys = output["migration_lock_keys"]
    raw_acquisitions = output[
        "lock_acquisitions"
    ]

    if not isinstance(raw_identities, list):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "output.workspace_identities "
                "must be an array"
            ),
        )
    if not isinstance(raw_keys, list):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "output.migration_lock_keys "
                "must be an array"
            ),
        )
    if not isinstance(raw_acquisitions, list):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "output.lock_acquisitions "
                "must be an array"
            ),
        )

    identities = [
        _validate_identity(
            value,
            (
                "output.workspace_identities"
                f"[{index}]"
            ),
        )
        for index, value in enumerate(
            raw_identities
        )
    ]
    keys = [
        _validate_lock_key(
            value,
            (
                "output.migration_lock_keys"
                f"[{index}]"
            ),
        )
        for index, value in enumerate(raw_keys)
    ]
    acquisitions = [
        _validate_acquisition(
            value,
            (
                "output.lock_acquisitions"
                f"[{index}]"
            ),
            len(keys),
        )
        for index, value in enumerate(
            raw_acquisitions
        )
    ]

    return {
        "decision": _require_enum(
            output["decision"],
            _DECISIONS,
            "output.decision",
            output=True,
        ),
        "error_code": error_code,
        "probe_stage": _require_enum(
            output["probe_stage"],
            _PROBE_STAGES,
            "output.probe_stage",
            output=True,
        ),
        "workspace_identities": identities,
        "migration_lock_keys": keys,
        "lock_acquisitions": acquisitions,
        "zero_database_mutations": require_bool(
            output["zero_database_mutations"],
            "output.zero_database_mutations",
        ),
        "zero_marker_mutations": require_bool(
            output["zero_marker_mutations"],
            "output.zero_marker_mutations",
        ),
        "zero_legacy_mutations": require_bool(
            output["zero_legacy_mutations"],
            "output.zero_legacy_mutations",
        ),
        "zero_provider_calls": require_bool(
            output["zero_provider_calls"],
            "output.zero_provider_calls",
        ),
    }


class AuthorityFilesystemOracle:
    """Pure AC-01 oracle over injected observations."""

    oracle_version = 1

    def __init__(
        self,
        base_fixture_id: str,
    ) -> None:
        self._base = base_fixture_id
        self.oracle_id = _ORACLE_IDS[
            base_fixture_id
        ]
        self.fixture_ids = frozenset(
            {
                base_fixture_id,
                (
                    f"{base_fixture_id}"
                    f"{_NEGATIVE_SUFFIX}"
                ),
            }
        )

    def _resolve(
        self,
        inputs: Mapping[str, Any],
    ) -> list[
        tuple[str, Mapping[str, Any]]
    ]:
        graph = inputs["resolution_graph"]
        nodes = {
            node["node_id"]: node
            for node in graph["nodes"]
        }
        resolved: list[
            tuple[str, Mapping[str, Any]]
        ] = []

        for entry in graph["entries"]:
            node = nodes[entry["entry_node_id"]]
            while node["node_kind"] == "ALIAS":
                node = nodes[
                    node["target_node_id"]
                ]
            resolved.append(
                (
                    entry["presented_path"],
                    node,
                )
            )

        return resolved

    def _failure_stage(
        self,
        resolved: list[
            tuple[str, Mapping[str, Any]]
        ],
        capabilities: Mapping[str, Any],
    ) -> str | None:
        for _, node in resolved:
            if (
                node["resolved_filesystem"]
                != "NTFS"
                or node["storage_scope"]
                != "LOCAL"
                or node["redirected"]
                or node["virtualized"]
                or not capabilities[
                    "stable_identity_available"
                ]
                or node["volume_guid"] is None
                or node["file_id"] is None
            ):
                return "FILESYSTEM"

        if not (
            capabilities["wal_mode_available"]
            and capabilities[
                "shared_memory_available"
            ]
        ):
            return "WAL_SHARED_MEMORY"

        if not (
            capabilities[
                "exclusive_lock_available"
            ]
            and capabilities[
                "atomic_rename_available"
            ]
            and capabilities[
                "namespace_custody_available"
            ]
        ):
            return "LOCK_RENAME_CUSTODY"

        return None

    def _accepted(
        self,
        inputs: Mapping[str, Any],
        resolved: list[
            tuple[str, Mapping[str, Any]]
        ],
    ) -> dict[str, Any]:
        identities: list[dict[str, str]] = []
        identity_indexes: dict[
            tuple[str, str],
            int,
        ] = {}
        path_to_identity: dict[
            str,
            tuple[str, str],
        ] = {}

        for presented_path, node in resolved:
            identity = (
                str(node["volume_guid"]),
                str(node["file_id"]),
            )
            path_to_identity[
                presented_path
            ] = identity

            if identity not in identity_indexes:
                identity_indexes[identity] = len(
                    identities
                )
                identities.append(
                    {
                        "volume_guid": identity[0],
                        "file_id": identity[1],
                    }
                )

        lock_keys = [
            {
                "key_source": (
                    "RESOLVED_IDENTITY"
                ),
                "volume_guid": identity[
                    "volume_guid"
                ],
                "file_id": identity["file_id"],
                "presented_path": None,
            }
            for identity in identities
        ]

        held: set[int] = set()
        acquisitions: list[
            dict[str, Any]
        ] = []

        for attempt in inputs["lock_attempts"]:
            identity = path_to_identity[
                attempt["presented_path"]
            ]
            key_index = identity_indexes[
                identity
            ]
            disposition = (
                "CONTENDED"
                if key_index in held
                else "ACQUIRED"
            )
            held.add(key_index)

            acquisitions.append(
                {
                    "owner_id": attempt[
                        "owner_id"
                    ],
                    "disposition": disposition,
                    "lock_key_index": key_index,
                }
            )

        return {
            "decision": "ACCEPTED",
            "error_code": None,
            "probe_stage": "COMPLETE",
            "workspace_identities": identities,
            "migration_lock_keys": lock_keys,
            "lock_acquisitions": acquisitions,
            "zero_database_mutations": True,
            "zero_marker_mutations": True,
            "zero_legacy_mutations": True,
            "zero_provider_calls": True,
        }

    def compute_expected(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if (
            _base_fixture_id(fixture_id)
            != self._base
        ):
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                (
                    f"oracle_id={self.oracle_id};"
                    f"fixture_id={fixture_id}"
                ),
            )

        resolved = self._resolve(raw_inputs)
        failure_stage = self._failure_stage(
            resolved,
            raw_inputs[
                "capability_observations"
            ],
        )

        if failure_stage is not None:
            return {
                "decision": "REJECTED",
                "error_code": (
                    "FILESYSTEM_UNSUPPORTED"
                ),
                "probe_stage": failure_stage,
                "workspace_identities": [],
                "migration_lock_keys": [],
                "lock_acquisitions": [],
                "zero_database_mutations": True,
                "zero_marker_mutations": True,
                "zero_legacy_mutations": True,
                "zero_provider_calls": True,
            }

        return self._accepted(
            raw_inputs,
            resolved,
        )


class AuthorityFilesystemSubjectAdapter:
    """Pure reference AC-01 adapter with no I/O."""

    adapter_version = 1

    def __init__(
        self,
        base_fixture_id: str,
    ) -> None:
        self._base = base_fixture_id
        label = (
            base_fixture_id
            .lower()
            .replace("-", "")
        )
        self.adapter_id = (
            "authority_filesystem."
            f"{label}.reference"
        )
        self.fixture_ids = frozenset(
            {base_fixture_id}
        )

    def _trace_targets(
        self,
        inputs: Mapping[str, Any],
    ) -> list[
        tuple[str, Mapping[str, Any]]
    ]:
        graph = inputs["resolution_graph"]
        by_id: dict[
            str,
            Mapping[str, Any],
        ] = {}

        for candidate in graph["nodes"]:
            by_id[
                candidate["node_id"]
            ] = candidate

        targets: list[
            tuple[str, Mapping[str, Any]]
        ] = []

        for root in graph["entries"]:
            cursor_id = root["entry_node_id"]
            cursor = by_id[cursor_id]

            while (
                cursor["node_kind"]
                != "PHYSICAL"
            ):
                cursor_id = cursor[
                    "target_node_id"
                ]
                cursor = by_id[cursor_id]

            targets.append(
                (
                    root["presented_path"],
                    cursor,
                )
            )

        return targets

    def _reference_failure_stage(
        self,
        targets: list[
            tuple[str, Mapping[str, Any]]
        ],
        observations: Mapping[str, Any],
    ) -> str | None:
        physical_ok = all(
            (
                target["resolved_filesystem"]
                == "NTFS"
                and target["storage_scope"]
                == "LOCAL"
                and target["redirected"] is False
                and target["virtualized"] is False
                and target["volume_guid"]
                is not None
                and target["file_id"]
                is not None
            )
            for _, target in targets
        )

        if not (
            physical_ok
            and observations[
                "stable_identity_available"
            ]
        ):
            return "FILESYSTEM"

        wal_ok = (
            observations["wal_mode_available"]
            and observations[
                "shared_memory_available"
            ]
        )
        if not wal_ok:
            return "WAL_SHARED_MEMORY"

        custody_ok = all(
            (
                observations[
                    "exclusive_lock_available"
                ],
                observations[
                    "atomic_rename_available"
                ],
                observations[
                    "namespace_custody_available"
                ],
            )
        )
        if not custody_ok:
            return "LOCK_RENAME_CUSTODY"

        return None

    def _reference_accept(
        self,
        inputs: Mapping[str, Any],
        targets: list[
            tuple[str, Mapping[str, Any]]
        ],
    ) -> dict[str, Any]:
        unique_physical: list[
            tuple[str, str]
        ] = []
        path_binding: dict[
            str,
            tuple[str, str],
        ] = {}

        for path, target in targets:
            physical = (
                str(target["volume_guid"]),
                str(target["file_id"]),
            )
            path_binding[path] = physical

            if physical not in unique_physical:
                unique_physical.append(physical)

        identities = [
            {
                "volume_guid": physical[0],
                "file_id": physical[1],
            }
            for physical in unique_physical
        ]
        keys = [
            {
                "key_source": (
                    "RESOLVED_IDENTITY"
                ),
                "volume_guid": physical[0],
                "file_id": physical[1],
                "presented_path": None,
            }
            for physical in unique_physical
        ]

        owners_by_key: dict[int, str] = {}
        acquisitions: list[
            dict[str, Any]
        ] = []

        for attempt in inputs["lock_attempts"]:
            key_index = unique_physical.index(
                path_binding[
                    attempt["presented_path"]
                ]
            )

            if key_index in owners_by_key:
                disposition = "CONTENDED"
            else:
                disposition = "ACQUIRED"
                owners_by_key[key_index] = (
                    attempt["owner_id"]
                )

            acquisitions.append(
                {
                    "owner_id": attempt[
                        "owner_id"
                    ],
                    "disposition": disposition,
                    "lock_key_index": key_index,
                }
            )

        return {
            "decision": "ACCEPTED",
            "error_code": None,
            "probe_stage": "COMPLETE",
            "workspace_identities": identities,
            "migration_lock_keys": keys,
            "lock_acquisitions": acquisitions,
            "zero_database_mutations": True,
            "zero_marker_mutations": True,
            "zero_legacy_mutations": True,
            "zero_provider_calls": True,
        }

    def _evaluate(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        targets = self._trace_targets(inputs)
        stage = self._reference_failure_stage(
            targets,
            inputs[
                "capability_observations"
            ],
        )

        if stage is not None:
            return {
                "decision": "REJECTED",
                "error_code": (
                    "FILESYSTEM_UNSUPPORTED"
                ),
                "probe_stage": stage,
                "workspace_identities": [],
                "migration_lock_keys": [],
                "lock_acquisitions": [],
                "zero_database_mutations": True,
                "zero_marker_mutations": True,
                "zero_legacy_mutations": True,
                "zero_provider_calls": True,
            }

        return self._reference_accept(
            inputs,
            targets,
        )

    def execute(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> Mapping[str, Any]:
        del context

        if fixture_id != self._base:
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                (
                    f"adapter_id={self.adapter_id};"
                    f"fixture_id={fixture_id}"
                ),
            )

        return self._evaluate(raw_inputs)


class FaultInjectedAuthorityFilesystemAdapter(
    AuthorityFilesystemSubjectAdapter
):
    """Eight specific faulty AC-01 adapters."""

    adapter_version = 1

    def __init__(
        self,
        base_fixture_id: str,
    ) -> None:
        super().__init__(base_fixture_id)
        label = (
            base_fixture_id
            .lower()
            .replace("-", "")
        )
        self.adapter_id = (
            "authority_filesystem."
            f"{label}.fault_injected"
        )
        self.fixture_ids = frozenset(
            {
                (
                    f"{base_fixture_id}"
                    f"{_NEGATIVE_SUFFIX}"
                )
            }
        )

    def _accept_despite_skipped_check(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        targets = self._trace_targets(inputs)
        return self._reference_accept(
            inputs,
            targets,
        )

    def _path_keyed_alias_result(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        targets = self._trace_targets(inputs)
        first_target = targets[0][1]

        identities = [
            {
                "volume_guid": str(
                    first_target["volume_guid"]
                ),
                "file_id": str(
                    first_target["file_id"]
                ),
            }
        ]
        keys = [
            {
                "key_source": "PRESENTED_PATH",
                "volume_guid": None,
                "file_id": None,
                "presented_path": attempt[
                    "presented_path"
                ],
            }
            for attempt in inputs["lock_attempts"]
        ]
        acquisitions = [
            {
                "owner_id": attempt["owner_id"],
                "disposition": "ACQUIRED",
                "lock_key_index": index,
            }
            for index, attempt in enumerate(
                inputs["lock_attempts"]
            )
        ]

        return {
            "decision": "ACCEPTED",
            "error_code": None,
            "probe_stage": "COMPLETE",
            "workspace_identities": identities,
            "migration_lock_keys": keys,
            "lock_acquisitions": acquisitions,
            "zero_database_mutations": True,
            "zero_marker_mutations": True,
            "zero_legacy_mutations": True,
            "zero_provider_calls": True,
        }

    def execute(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> Mapping[str, Any]:
        del context

        expected_fixture_id = (
            f"{self._base}{_NEGATIVE_SUFFIX}"
        )
        if fixture_id != expected_fixture_id:
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                (
                    f"adapter_id={self.adapter_id};"
                    f"fixture_id={fixture_id}"
                ),
            )

        if self._base == "AC-01-01":
            output = self._evaluate(raw_inputs)
            output[
                "zero_database_mutations"
            ] = False
            return output

        if self._base == "AC-01-02":
            targets = self._trace_targets(
                raw_inputs
            )
            if all(
                (
                    target[
                        "resolved_filesystem"
                    ]
                    != "FAT"
                )
                for _, target in targets
            ):
                return (
                    self
                    ._accept_despite_skipped_check(
                        raw_inputs
                    )
                )
            return self._evaluate(raw_inputs)

        if self._base == "AC-01-03":
            targets = self._trace_targets(
                raw_inputs
            )
            if all(
                (
                    target[
                        "resolved_filesystem"
                    ]
                    != "EXFAT"
                )
                for _, target in targets
            ):
                return (
                    self
                    ._accept_despite_skipped_check(
                        raw_inputs
                    )
                )
            return self._evaluate(raw_inputs)

        if self._base == "AC-01-04":
            targets = self._trace_targets(
                raw_inputs
            )
            if all(
                (
                    target[
                        "resolved_filesystem"
                    ]
                    == "NTFS"
                )
                for _, target in targets
            ):
                return (
                    self
                    ._accept_despite_skipped_check(
                        raw_inputs
                    )
                )
            return self._evaluate(raw_inputs)

        if self._base == "AC-01-05":
            return (
                self
                ._accept_despite_skipped_check(
                    raw_inputs
                )
            )

        if self._base == "AC-01-06":
            return (
                self
                ._accept_despite_skipped_check(
                    raw_inputs
                )
            )

        if self._base == "AC-01-07":
            graph = raw_inputs[
                "resolution_graph"
            ]
            node_table = {
                node["node_id"]: node
                for node in graph["nodes"]
            }
            trusts_presented_labels = all(
                (
                    node_table[
                        entry["entry_node_id"]
                    ]["reported_filesystem"]
                    == "NTFS"
                )
                for entry in graph["entries"]
            )

            if trusts_presented_labels:
                return (
                    self
                    ._accept_despite_skipped_check(
                        raw_inputs
                    )
                )
            return self._evaluate(raw_inputs)

        return self._path_keyed_alias_result(
            raw_inputs
        )


def authority_filesystem_registrations(
) -> tuple[DomainRegistration, ...]:
    """Return immutable built-in AC-01 rows."""

    registrations: list[
        DomainRegistration
    ] = []

    for base_fixture_id in _BASE_FIXTURES:
        oracle = AuthorityFilesystemOracle(
            base_fixture_id
        )
        positive_adapter = (
            AuthorityFilesystemSubjectAdapter(
                base_fixture_id
            )
        )
        negative_adapter = (
            FaultInjectedAuthorityFilesystemAdapter(
                base_fixture_id
            )
        )

        registrations.append(
            DomainRegistration(
                fixture_id=base_fixture_id,
                oracle_id=oracle.oracle_id,
                oracle_version=(
                    oracle.oracle_version
                ),
                oracle=oracle,
                adapter=positive_adapter,
                input_validator=(
                    validate_authority_filesystem_inputs
                ),
                output_validator=(
                    validate_authority_filesystem_output
                ),
            )
        )
        registrations.append(
            DomainRegistration(
                fixture_id=(
                    f"{base_fixture_id}"
                    f"{_NEGATIVE_SUFFIX}"
                ),
                oracle_id=oracle.oracle_id,
                oracle_version=(
                    oracle.oracle_version
                ),
                oracle=oracle,
                adapter=negative_adapter,
                input_validator=(
                    validate_authority_filesystem_inputs
                ),
                output_validator=(
                    validate_authority_filesystem_output
                ),
            )
        )

    return tuple(registrations)