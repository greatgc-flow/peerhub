import json
from typing import Any
from peerhub.core.protocol import canonical_json_bytes

def _json_text(value: object) -> str:  # pyright: ignore[reportUnusedFunction]
    return canonical_json_bytes(value).decode("utf-8")

def _json_value(raw: str) -> Any:
    return json.loads(raw)

def _json_object(raw: str) -> dict[str, Any]:
    value = _json_value(raw)
    if not isinstance(value, dict):
        raise RuntimeError("stored JSON value is not an object")
    return value  # pyright: ignore[reportUnknownVariableType]

def _optional_json_object(raw: str | None) -> dict[str, Any] | None:  # pyright: ignore[reportUnusedFunction]
    if raw is None:
        return None
    return _json_object(raw)

def _string_tuple(raw: str) -> tuple[str, ...]:  # pyright: ignore[reportUnusedFunction]
    value = _json_value(raw)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):  # pyright: ignore[reportUnknownVariableType]
        raise RuntimeError("stored evidence_refs is not a string array")
    return tuple(value)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]

def _stored_revision(raw: str) -> str | int:  # pyright: ignore[reportUnusedFunction]
    value = _json_value(raw)
    if type(value) is int or isinstance(value, str):
        return value
    raise RuntimeError("stored revision is not a string or integer")

def _stored_optional_revision(raw: str) -> str | int | None:  # pyright: ignore[reportUnusedFunction]
    value = _json_value(raw)
    if value is None or type(value) is int or isinstance(value, str):
        return value
    raise RuntimeError("stored expected revision has an invalid type")
