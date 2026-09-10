"""``peerhub config validate`` (item 13, dotdir consolidation, ratified
2026-09-09).

Exercises every config family's real loader across every applicable
layer and reports OK/ERROR per family -- reusing each family's own
production loader rather than a separate parallel validator, so
"validates" means exactly "would load successfully for a real dispatch",
never a looser or stricter check. Where a family exposes item 9's
winning-layer diagnostic (``arbiter.json``/``proposals.json``), this is
its actual consumer.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from peerhub.application.arbiter_review import (
    describe_final_arbiter_policy_source,
    load_final_arbiter_policy,
)
from peerhub.application.ask_config import AskConfigError, load_ask_config
from peerhub.application.config_layers import LayeredConfigError
from peerhub.application.config_paths import resolve_config_paths
from peerhub.application.proposals import (
    describe_proposal_voters_source,
    load_proposal_voters,
)

# Every loader below raises one of these for a malformed layer; nothing
# else is expected to escape a config-loading call, so nothing broader
# (bare Exception) is caught.
_CONFIG_ERRORS = (ValueError, AskConfigError, LayeredConfigError)


@dataclass(frozen=True, slots=True)
class ConfigFamilyReport:
    """The validation outcome for one config family."""

    name: str
    ok: bool
    detail: str
    winning_layer: dict[str, str]

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "winning_layer": self.winning_layer,
        }


def validate_workspace_config(workspace_root: Path) -> tuple[ConfigFamilyReport, ...]:
    """Validate every config family for ``workspace_root``, without
    raising: every outcome (including a malformed layer) is reported."""

    reports: list[ConfigFamilyReport] = []
    resolved = resolve_config_paths(workspace_root=workspace_root)

    # models.toml: packaged + global only -- there is no workspace tier
    # (ratified release gate: workspace model bindings remain the sole
    # workspace model authority). A structural TOML-parse check only;
    # per-profile resolution needs runtime peer/node context this command
    # does not have.
    models_path = resolved.models_toml.path
    if not models_path.is_file():
        reports.append(
            ConfigFamilyReport("models.toml", True, "absent (packaged default applies)", {})
        )
    else:
        try:
            with models_path.open("rb") as handle:
                tomllib.load(handle)  # tomllib.load() always returns dict[str, Any]; a parse failure is the only structural check available here.
            reports.append(
                ConfigFamilyReport("models.toml", True, f"parses OK ({models_path})", {})
            )
        except tomllib.TOMLDecodeError as exc:
            # TOMLDecodeError is a ValueError subclass, already inside
            # _CONFIG_ERRORS -- this branch exists to report a clearer
            # detail message than the generic handler below.
            reports.append(ConfigFamilyReport("models.toml", False, str(exc), {}))
        except OSError as exc:
            reports.append(ConfigFamilyReport("models.toml", False, str(exc), {}))

    try:
        load_ask_config(workspace_root)
        reports.append(ConfigFamilyReport("ask.toml", True, "resolves OK", {}))
    except _CONFIG_ERRORS as exc:
        reports.append(ConfigFamilyReport("ask.toml", False, str(exc), {}))

    try:
        load_final_arbiter_policy(workspace_root)
        reports.append(
            ConfigFamilyReport(
                "arbiter.json",
                True,
                "resolves OK",
                describe_final_arbiter_policy_source(workspace_root),
            )
        )
    except _CONFIG_ERRORS as exc:
        reports.append(ConfigFamilyReport("arbiter.json", False, str(exc), {}))

    try:
        load_proposal_voters(workspace_root)
        reports.append(
            ConfigFamilyReport(
                "proposals.json",
                True,
                "resolves OK",
                describe_proposal_voters_source(workspace_root),
            )
        )
    except _CONFIG_ERRORS as exc:
        reports.append(ConfigFamilyReport("proposals.json", False, str(exc), {}))

    return tuple(reports)
