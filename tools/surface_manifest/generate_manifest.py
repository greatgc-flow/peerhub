"""
generate_manifest.py - Legacy Hub Surface Manifest Generator (Stage 0 Artifact)

Machine-generates legacy-hub-surface-current.json by statically analyzing P:\\_sys\\core\\hub.py,
its argparse setup, action dispatch table, helper call graphs, state interactions, and runtime --help receipt.

Usage:
    python tools/surface_manifest/generate_manifest.py [--output OUT_PATH]
"""
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Default canonical locations
SCRIPT_DIR = Path(__file__).resolve().parent
PEERHUB_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_SYS_DIR = Path("P:/_sys")
DEFAULT_OUTPUT_PATH = PEERHUB_ROOT / "docs" / "design" / "phase0" / "legacy-hub-surface-current.json"


def calculate_file_hash(path: Path) -> Tuple[Optional[str], int, int]:
    """Return (sha256, size_bytes, line_count) for a file if it exists."""
    if not path.exists():
        return None, 0, 0
    raw = path.read_bytes()
    sha256 = hashlib.sha256(raw).hexdigest()
    lines = len(raw.decode("utf-8", errors="ignore").splitlines())
    return sha256, len(raw), lines


def get_git_receipt(repo_dir: Path) -> Dict[str, Any]:
    """Collect git commit and dirty state receipt for a repository directory."""
    if not repo_dir.exists():
        return {"error": f"Path {repo_dir} does not exist"}
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_dir, text=True, stderr=subprocess.DEVNULL
        ).strip()
        status_out = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=repo_dir, text=True, stderr=subprocess.DEVNULL
        ).strip()
        uncommitted = [line[3:].strip() for line in status_out.splitlines() if line.strip()]
        return {
            "commit": commit,
            "is_dirty": len(uncommitted) > 0,
            "uncommitted_files": uncommitted,
        }
    except Exception as e:
        return {"error": str(e)}


class HubParserExtractor(ast.NodeVisitor):
    """AST visitor to extract parser setup and arguments from hub.py's main()."""

    def __init__(self) -> None:
        self.arguments: List[Dict[str, Any]] = []
        self.prog: Optional[str] = None
        self.description: Optional[str] = None
        self.action_choices: List[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        # Check parser = argparse.ArgumentParser(...)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "ArgumentParser":
            for kw in node.keywords:
                if kw.arg == "prog" and isinstance(kw.value, ast.Constant):
                    self.prog = str(kw.value.value)
                elif kw.arg == "description" and isinstance(kw.value, ast.Constant):
                    self.description = str(kw.value.value)

        # Check parser.add_argument(...)
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
            arg_data: Dict[str, Any] = {
                "name_or_flags": [],
                "dest": None,
                "action": "store",
                "type": None,
                "default": None,
                "choices": None,
                "help": None,
                "required": False,
            }
            # Positional / flag names
            for arg in node.args:
                if isinstance(arg, ast.Constant):
                    arg_data["name_or_flags"].append(str(arg.value))

            if arg_data["name_or_flags"]:
                first_flag = arg_data["name_or_flags"][0]
                if not first_flag.startswith("-"):
                    arg_data["dest"] = first_flag
                else:
                    arg_data["dest"] = first_flag.lstrip("-").replace("-", "_")

            # Keyword options
            for kw in node.keywords:
                val = kw.value
                if kw.arg == "dest" and isinstance(val, ast.Constant):
                    arg_data["dest"] = str(val.value)
                elif kw.arg == "action" and isinstance(val, ast.Constant):
                    arg_data["action"] = str(val.value)
                elif kw.arg == "type":
                    if isinstance(val, ast.Name):
                        arg_data["type"] = val.id
                    elif isinstance(val, ast.Attribute):
                        arg_data["type"] = val.attr
                elif kw.arg == "default":
                    if isinstance(val, ast.Constant):
                        arg_data["default"] = val.value
                    elif isinstance(val, ast.NameConstant):  # for older python ast
                        arg_data["default"] = val.value
                    elif isinstance(val, ast.Name):
                        arg_data["default"] = val.id
                elif kw.arg == "choices" and isinstance(val, ast.List):
                    choices_list = [
                        elt.value for elt in val.elts if isinstance(elt, ast.Constant)
                    ]
                    arg_data["choices"] = choices_list
                    if arg_data["dest"] == "action":
                        self.action_choices = choices_list
                elif kw.arg == "help" and isinstance(val, ast.Constant):
                    arg_data["help"] = str(val.value)
                elif kw.arg == "required" and isinstance(val, ast.Constant):
                    arg_data["required"] = bool(val.value)

            self.arguments.append(arg_data)
        self.generic_visit(node)


def extract_dispatch_table(hub_tree: ast.AST) -> Dict[str, str]:
    """Extract action -> handler function mapping from main() in hub.py."""
    main_fn = next(
        (n for n in ast.walk(hub_tree) if isinstance(n, ast.FunctionDef) and n.name == "main"),
        None,
    )
    if not main_fn:
        return {}

    dispatch: Dict[str, str] = {}

    def extract_handler_from_body(body: List[ast.stmt]) -> str:
        calls: List[str] = []
        for stmt in body:
            for node in ast.walk(stmt):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    name = node.func.id
                    if name not in (
                        "print", "sys", "exit", "_read_json", "_write_json",
                        "_load_orchestration", "find_ai_root", "ensure_ai_dir",
                        "_load_protocol_cfg", "getattr", "set", "list", "int",
                        "bool", "str", "len", "_guard_action",
                    ):
                        calls.append(name)
        for c in calls:
            if c.startswith("action_") or c.startswith("run_"):
                return c
        return calls[0] if calls else "unknown"

    def process_if(if_node: ast.If) -> None:
        cond = if_node.test
        act_name = None
        if isinstance(cond, ast.Compare):
            if (
                isinstance(cond.left, ast.Name)
                and cond.left.id == "act"
                and len(cond.comparators) == 1
                and isinstance(cond.comparators[0], ast.Constant)
            ):
                act_name = str(cond.comparators[0].value)
            elif (
                isinstance(cond.left, ast.Attribute)
                and cond.left.attr == "action"
                and len(cond.comparators) == 1
                and isinstance(cond.comparators[0], ast.Constant)
            ):
                act_name = str(cond.comparators[0].value)

        if act_name:
            dispatch[act_name] = extract_handler_from_body(if_node.body)

        for orel in if_node.orelse:
            if isinstance(orel, ast.If):
                process_if(orel)

    for stmt in main_fn.body:
        if isinstance(stmt, ast.If):
            process_if(stmt)

    return dispatch


class HandlerAnalyzer:
    """Analyzes function ASTs for call graphs, state interactions, and external effects."""

    def __init__(self, func_nodes: Dict[str, ast.FunctionDef]) -> None:
        self.func_nodes = func_nodes

    def get_direct_calls(self, fn_name: str) -> List[str]:
        if fn_name not in self.func_nodes:
            return []
        node = self.func_nodes[fn_name]
        calls: Set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                if isinstance(child.func, ast.Name):
                    calls.add(child.func.id)
                elif isinstance(child.func, ast.Attribute) and isinstance(child.func.value, ast.Name):
                    calls.add(f"{child.func.value.id}.{child.func.attr}")
        return sorted(calls)

    def get_transitive_internal_helpers(self, fn_name: str, max_depth: int = 6) -> List[str]:
        visited: Set[str] = set()
        queue: List[Tuple[str, int]] = [(fn_name, 0)]
        helpers: Set[str] = set()

        while queue:
            curr, depth = queue.pop(0)
            if curr in visited or depth > max_depth or curr not in self.func_nodes:
                continue
            visited.add(curr)
            if curr != fn_name:
                helpers.add(curr)

            node = self.func_nodes[curr]
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                    callee = child.func.id
                    if callee in self.func_nodes:
                        queue.append((callee, depth + 1))
        return sorted(helpers)

    def analyze_state_and_effects(self, handler_name: str) -> Dict[str, Any]:
        """Statically inspect AST of handler + called helpers for state reads/writes & external effects."""
        helpers = [handler_name] + [
            h for h in self.get_transitive_internal_helpers(handler_name, max_depth=3)
            if h in self.func_nodes
        ]

        state_reads: Set[str] = set()
        state_writes: Set[str] = set()
        external_effects: Set[str] = set()

        for fn in helpers:
            node = self.func_nodes[fn]
            fn_code_str = ast.unparse(node) if hasattr(ast, "unparse") else ""

            # Check calls
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    func_name = ""
                    if isinstance(child.func, ast.Name):
                        func_name = child.func.id
                    elif isinstance(child.func, ast.Attribute):
                        func_name = child.func.attr

                    # State reads
                    if func_name in ("_read_json", "_load_orchestration", "_load_protocol_cfg", "_load_lifecycle_policy", "read_text", "read_bytes"):
                        state_reads.add(f"call:{func_name}")
                    # State writes
                    elif func_name in ("_write_json", "_append_jsonl", "_log_p2p", "write_text", "write_bytes", "remove", "rmtree", "unlink", "mkdir"):
                        state_writes.add(f"call:{func_name}")
                    # External effects
                    elif func_name in ("run", "Popen", "call", "check_output", "system", "exec", "_ask_with_pty"):
                        external_effects.add(f"process:{func_name}")
                    elif func_name in ("exit", "_exit"):
                        external_effects.add("process:exit")

            # Check string literals for paths/config/state references
            if "orchestration.json" in fn_code_str:
                state_reads.add("config:orchestration.json")
            if "lifecycle_policy.json" in fn_code_str:
                state_reads.add("config:lifecycle_policy.json")
            if "environment.json" in fn_code_str:
                state_reads.add("config:environment.json")
            if "state.json" in fn_code_str:
                state_reads.add("state:state.json")
            if "consensus" in fn_code_str:
                state_reads.add("state:consensus_dir")
            if "sessions" in fn_code_str:
                state_reads.add("state:sessions_dir")
            if "sqlite" in fn_code_str or "sqlite3" in fn_code_str:
                state_reads.add("db:sqlite")
                state_writes.add("db:sqlite")
            if "os.environ" in fn_code_str:
                external_effects.add("env:os.environ")

        return {
            "state_reads": sorted(state_reads),
            "state_writes": sorted(state_writes),
            "external_effects": sorted(external_effects),
        }


def get_normalized_help_receipt(hub_py: Path) -> Dict[str, str]:
    """Execute hub.py --help and return normalized text receipt and its SHA-256."""
    try:
        raw_output = subprocess.check_output(
            [sys.executable, str(hub_py), "--help"], text=True, stderr=subprocess.STDOUT
        )
        # Normalize CRLF to LF and trim trailing whitespace per line
        lines = [line.rstrip() for line in raw_output.splitlines()]
        normalized_text = "\n".join(lines) + "\n"
        digest = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
        return {
            "normalized_help_text": normalized_text,
            "help_text_sha256": digest,
        }
    except Exception as e:
        return {
            "normalized_help_text": f"ERROR executing hub --help: {e}",
            "help_text_sha256": "",
        }


def generate_manifest(sys_dir: Path = DEFAULT_SYS_DIR, output_path: Path = DEFAULT_OUTPUT_PATH) -> Dict[str, Any]:
    """Main generator function for legacy-hub-surface-current.json."""
    hub_py = sys_dir / "core" / "hub.py"
    hub_peer_py = sys_dir / "core" / "hub_peer.py"

    if not hub_py.exists():
        raise FileNotFoundError(f"Legacy hub.py not found at {hub_py}")

    hub_code = hub_py.read_text(encoding="utf-8", errors="ignore")
    hub_tree = ast.parse(hub_code)

    # 1. Source Hashes & Git Receipts
    hub_sha, hub_size, hub_lines = calculate_file_hash(hub_py)
    hub_peer_sha, hub_peer_size, hub_peer_lines = calculate_file_hash(hub_peer_py)

    config_files_info = {}
    config_paths = [
        sys_dir / "core" / "hub_config.json",
        sys_dir / "core" / "config.py",
        sys_dir / "ai" / "config" / "environment.json",
        sys_dir / "ai" / "config" / "orchestration.json",
        sys_dir / "ai" / "config" / "lifecycle_policy.json",
    ]
    for cp in config_paths:
        if cp.exists():
            c_sha, c_size, c_lines = calculate_file_hash(cp)
            rel_name = cp.relative_to(sys_dir).as_posix()
            config_files_info[rel_name] = {
                "sha256": c_sha,
                "size_bytes": c_size,
                "line_count": c_lines,
            }

    git_receipts = {
        "peerhub_repo": get_git_receipt(PEERHUB_ROOT),
        "legacy_hub_repo": get_git_receipt(sys_dir),
    }

    # 2. Argparse Surface & Action Vector
    extractor = HubParserExtractor()
    extractor.visit(hub_tree)

    action_vector = extractor.action_choices
    action_vector_digest = hashlib.sha256(
        json.dumps(action_vector, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    # 3. Dispatch Table
    dispatch_table = extract_dispatch_table(hub_tree)

    # 4. Action Details (AST Helper Dependencies, State & Effects)
    func_nodes = {
        n.name: n for n in ast.walk(hub_tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    analyzer = HandlerAnalyzer(func_nodes)

    action_details = {}
    for act in action_vector:
        handler_name = dispatch_table.get(act, "unknown")
        direct_calls = analyzer.get_direct_calls(handler_name)
        internal_helpers = [c for c in direct_calls if c in func_nodes]
        transitive_internal = analyzer.get_transitive_internal_helpers(handler_name)
        state_effects = analyzer.analyze_state_and_effects(handler_name)

        action_details[act] = {
            "action": act,
            "handler": handler_name,
            "helper_dependencies": {
                "direct_calls": direct_calls,
                "internal_helpers": sorted(internal_helpers),
                "transitive_internal_helpers": transitive_internal,
            },
            "declared_state_reads": state_effects["state_reads"],
            "declared_state_writes": state_effects["state_writes"],
            "declared_external_effects": state_effects["external_effects"],
        }

    # 5. Help Receipt
    help_receipt = get_normalized_help_receipt(hub_py)

    manifest = {
        "meta": {
            "schema_version": "1.0",
            "generator_name": "tools/surface_manifest/generate_manifest.py",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "target_system": "legacy_hub_surface",
        },
        "measurement_methodology": {
            "directly_measured": [
                "Git commit hashes & dirty receipts for peerhub & legacy hub repositories",
                "Raw SHA-256 hashes, file sizes, and line counts of hub.py, hub_peer.py, and core configs",
                "Action vector (90 actions), canonical order, and SHA-256 digest",
                "Argparse arguments, flags, types, defaults, choices, and help strings",
                "Action-to-handler dispatch mapping extracted from hub.py main() AST",
                "Direct AST function calls per handler function",
                "Normalized hub --help stdout receipt and SHA-256 digest",
            ],
            "heuristic_best_effort": [
                "Transitive helper dependencies (ast reachability tree without dynamic branch evaluation)",
                "Declared state reads, state writes, and external effects (ast pattern matching on file ops, json ops, subprocess, os.environ)",
            ],
        },
        "git_receipts": git_receipts,
        "source_files": {
            "hub_py": {
                "path": str(hub_py),
                "sha256": hub_sha,
                "size_bytes": hub_size,
                "line_count": hub_lines,
            },
            "hub_peer_py": {
                "path": str(hub_peer_py) if hub_peer_py.exists() else None,
                "sha256": hub_peer_sha,
                "size_bytes": hub_peer_size,
                "line_count": hub_peer_lines,
            },
            "config_files": config_files_info,
        },
        "action_vector": {
            "action_count": len(action_vector),
            "actions": action_vector,
            "action_vector_digest": action_vector_digest,
        },
        "argparse_surface": {
            "prog": extractor.prog,
            "description": extractor.description,
            "argument_count": len(extractor.arguments),
            "arguments": extractor.arguments,
        },
        "dispatch_table": dispatch_table,
        "action_details": action_details,
        "help_receipt": help_receipt,
    }

    # Save to file if output_path is provided
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    return manifest


if __name__ == "__main__":
    out_file = DEFAULT_OUTPUT_PATH
    if len(sys.argv) > 1 and sys.argv[1] == "--output" and len(sys.argv) > 2:
        out_file = Path(sys.argv[2])

    print(f"Generating legacy hub surface manifest from {DEFAULT_SYS_DIR / 'core' / 'hub.py'}...")
    manifest_data = generate_manifest(output_path=out_file)

    print("\n--- Manifest Generation Headline Summary ---")
    print(f"Output Path:          {out_file}")
    print(f"Action Count:         {manifest_data['action_vector']['action_count']}")
    print(f"Action Vector SHA:    {manifest_data['action_vector']['action_vector_digest']}")
    print(f"hub.py SHA-256:       {manifest_data['source_files']['hub_py']['sha256']}")
    print(f"hub.py Line Count:    {manifest_data['source_files']['hub_py']['line_count']}")
    print(f"hub_peer.py SHA-256:  {manifest_data['source_files']['hub_peer_py']['sha256']}")
    print(f"Help Receipt SHA:     {manifest_data['help_receipt']['help_text_sha256']}")
    print(f"Peerhub Commit:       {manifest_data['git_receipts']['peerhub_repo'].get('commit')}")
    print(f"Legacy Hub Commit:    {manifest_data['git_receipts']['legacy_hub_repo'].get('commit')}")
