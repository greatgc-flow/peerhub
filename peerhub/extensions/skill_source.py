"""Read and verify the on-disk source of a catalogued skill."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, cast

from peerhub.extensions.boundary import SecurityBoundaryError
from peerhub.extensions.skill_models import SkillManifestError


def compute_directory_digest(skill_dir: Path) -> tuple[str, int]:
    """Compute deterministic SHA-256 tree digest over sorted relative paths and file contents."""
    hasher = hashlib.sha256()
    file_count = 0
    resolved_dir = skill_dir.resolve()

    all_files: list[tuple[str, Path]] = []
    for p in resolved_dir.rglob("*"):
        if p.is_symlink():
            raise SecurityBoundaryError("Skill source symlinks are not allowed")
        if p.is_file():
            rel_path = p.relative_to(resolved_dir).as_posix()
            if ".git" in p.relative_to(resolved_dir).parts:
                continue
            all_files.append((rel_path, p))

    all_files.sort(key=lambda x: x[0])
    # Length-prefixed framing (8-byte big-endian lengths): no byte sequence inside a path or a file can be mistaken for a
    # boundary, so two different directory trees can never share a digest. Files are streamed, memory stays bounded.
    for rel_path, file_path in all_files:
        name = rel_path.encode("utf-8")
        hasher.update(len(name).to_bytes(8, "big"))
        hasher.update(name)
        with file_path.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size  # the length of THIS open file, not of a path that may change
            hasher.update(size.to_bytes(8, "big"))
            seen = 0
            while chunk := stream.read(65536):
                hasher.update(chunk)
                seen += len(chunk)
            if seen != size:  # appended to / truncated while being hashed: the framed length would not match the bytes
                raise SecurityBoundaryError(f"Skill file {rel_path!r} changed while it was being hashed")
        file_count += 1

    return hasher.hexdigest(), file_count

def parse_yaml_frontmatter(text: str) -> dict[str, Any]:
    """Parse YAML frontmatter using pyyaml if available, or lightweight stdlib parser."""
    try:
        import importlib
        yaml_module = importlib.import_module("yaml")
        safe_load_fn = getattr(yaml_module, "safe_load", None)
        if callable(safe_load_fn):
            try:
                parsed: object = safe_load_fn(text)
                if isinstance(parsed, dict):
                    return cast(dict[str, Any], parsed)
            except Exception as e:
                raise SkillManifestError(f"Failed to parse YAML frontmatter: {e}") from e
    except ImportError:
        pass

    # Standard-library fallback parser for basic YAML frontmatter
    result: dict[str, Any] = {}
    current_key: str | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- "):
            if current_key:
                val = line[2:].strip().strip("\"'")
                if current_key not in result:
                    result[current_key] = []
                if isinstance(result[current_key], list):
                    cast(list[Any], result[current_key]).append(val)
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip()
            v = v.strip()
            current_key = k
            if v.startswith("[") and v.endswith("]"):  # inline list, as PyYAML reads `tags: [a, b]`
                result[k] = [i.strip().strip("\"'") for i in v[1:-1].split(",") if i.strip()]
                continue
            v = v.strip("\"'")
            if v:
                result[k] = v
            else:
                result[k] = []
    return result


def parse_skill_manifest(manifest_path: Path) -> dict[str, Any]:
    """Parse YAML frontmatter from SKILL.md."""
    if not manifest_path.is_file():
        raise SkillManifestError(f"SKILL.md not found in {manifest_path.parent}")

    content = manifest_path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        raise SkillManifestError("SKILL.md must begin with YAML frontmatter delimiter '---'")

    parts = content.split("---", 2)
    if len(parts) < 3:
        raise SkillManifestError("Unclosed YAML frontmatter in SKILL.md")

    frontmatter_text = parts[1]
    data = parse_yaml_frontmatter(frontmatter_text)

    if "name" not in data or not isinstance(data["name"], str) or not data["name"]:
        raise SkillManifestError("SKILL.md frontmatter missing required non-empty 'name' field")

    return data
