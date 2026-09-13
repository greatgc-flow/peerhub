from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import peerhub.cli


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CALL_MAP_PATH = (
    PROJECT_ROOT / "docs" / "design" / "peerhub-production-call-map-R1.json"
)
REQUIRED_PIPELINE_FIELDS = {
    "entrance",
    "resolver",
    "authorizer",
    "handler",
    "transactional_owner",
    "receipt",
    "external_effects",
    "integration_tests",
}


class _ParserCaptured(Exception):
    pass


def _capture_root_parser(monkeypatch: pytest.MonkeyPatch) -> argparse.ArgumentParser:
    captured: list[argparse.ArgumentParser] = []

    def capture(
        parser: argparse.ArgumentParser,
        args: list[str] | None = None,
        namespace: argparse.Namespace | None = None,
    ) -> argparse.Namespace:
        del args, namespace
        captured.append(parser)
        raise _ParserCaptured

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", capture)
    with pytest.raises(_ParserCaptured):
        peerhub.cli.main([])
    assert len(captured) == 1
    return captured[0]


def _subparser_action(
    parser: argparse.ArgumentParser,
) -> argparse._SubParsersAction[argparse.ArgumentParser] | None:  # pyright: ignore[reportPrivateUsage]
    actions = [
        action
        for action in parser._actions  # pyright: ignore[reportPrivateUsage]
        if isinstance(action, argparse._SubParsersAction)  # pyright: ignore[reportPrivateUsage]
    ]
    assert len(actions) <= 1
    return actions[0] if actions else None


def _command_help(
    action: argparse._SubParsersAction[argparse.ArgumentParser],  # pyright: ignore[reportPrivateUsage]
    name: str,
) -> str | None:
    for choice in action._choices_actions:  # pyright: ignore[reportPrivateUsage]
        if choice.dest == name:
            return choice.help
    return None


def _leaf_parsers(
    parser: argparse.ArgumentParser,
    prefix: tuple[str, ...] = (),
    help_parts: tuple[str | None, ...] = (),
) -> Iterator[tuple[str, argparse.ArgumentParser, tuple[str | None, ...]]]:
    action = _subparser_action(parser)
    if action is None:
        yield " ".join(prefix), parser, help_parts
        return
    for name, child in action.choices.items():
        yield from _leaf_parsers(
            child,
            (*prefix, name),
            (*help_parts, _command_help(action, name)),
        )


def _json_value(value: object) -> object:
    if value is argparse.SUPPRESS:
        return "<SUPPRESS>"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return repr(value)


def _type_name(value: Any) -> str | None:
    if value is None:
        return None
    return getattr(value, "__name__", repr(value))


def _parser_contract(
    parser: argparse.ArgumentParser,
    help_parts: tuple[str | None, ...],
) -> dict[str, object]:
    arguments: list[dict[str, object]] = []
    for action in parser._actions:  # pyright: ignore[reportPrivateUsage]
        if action.dest == "help" or isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
            action, argparse._SubParsersAction  # pyright: ignore[reportPrivateUsage]
        ):
            continue
        arguments.append(
            {
                "action": type(action).__name__,
                "choices": (
                    None
                    if action.choices is None
                    else [_json_value(item) for item in action.choices]
                ),
                "default": _json_value(action.default),
                "dest": action.dest,
                "help": action.help,
                "nargs": _json_value(action.nargs),
                "option_strings": list(action.option_strings),
                "required": action.required,
                "type": _type_name(action.type),
            }
        )
    return {"arguments": arguments, "command_help": list(help_parts)}


def _live_contracts(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, dict[str, object]]:
    root = _capture_root_parser(monkeypatch)
    return {
        path: _parser_contract(parser, help_parts)
        for path, parser, help_parts in _leaf_parsers(root)
    }


def test_production_call_map_matches_all_live_cli_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = json.loads(CALL_MAP_PATH.read_text(encoding="utf-8"))
    live = _live_contracts(monkeypatch)
    commands = artifact["commands"]

    assert artifact["schema_version"] == 1
    assert artifact["measurement"]["leaf_command_count"] == 103
    assert len(commands) == 103
    assert len({command["path"] for command in commands}) == 103
    assert [command["path"] for command in commands] == list(live)
    assert {
        command["path"]: command["parser_contract"] for command in commands
    } == live


def test_production_call_map_has_complete_pipeline_contracts() -> None:
    artifact = json.loads(CALL_MAP_PATH.read_text(encoding="utf-8"))
    commands = artifact["commands"]
    allowed_effects = {
        "READ_ONLY",
        "EXTERNAL_OBSERVATION",
        "MUTATING",
        "MUTATING_EXTERNAL",
        "CONDITIONAL_MUTATION",
        "CONDITIONAL_MUTATION_EXTERNAL",
    }

    for command in commands:
        assert REQUIRED_PIPELINE_FIELDS <= command.keys(), command["path"]
        assert command["effect"] in allowed_effects, command["path"]
        assert command["output_contract"], command["path"]
        assert command["exit_contract"], command["path"]
        assert command["integration_tests"], command["path"]
        for reference in command["integration_tests"]:
            if reference.startswith("TEST NEEDED:"):
                continue
            test_file = reference.split("::", maxsplit=1)[0]
            assert (PROJECT_ROOT / test_file).is_file(), reference


def test_map_records_current_gateway_bypasses_without_calling_them_fixed() -> None:
    artifact = json.loads(CALL_MAP_PATH.read_text(encoding="utf-8"))
    commands = artifact["commands"]
    mutators = [
        command
        for command in commands
        if command["effect"] not in {"READ_ONLY", "EXTERNAL_OBSERVATION"}
    ]

    assert artifact["measurement"]["mutating_or_conditional_count"] == len(mutators)
    assert any(
        command["gateway"] == "DIRECT_CLI_BYPASS" for command in mutators
    )
    assert all(command["gateway"] for command in commands)
    assert all(
        "security fix" not in command["authorizer"].lower()
        for command in mutators
    )
