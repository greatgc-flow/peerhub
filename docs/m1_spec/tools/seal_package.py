"""Rebuild MANIFEST.json and SHA256SUMS.txt after any edit under docs/m1_spec, then validate: python docs/m1_spec/tools/seal_package.py"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
SKIP = {"MANIFEST.json", "SHA256SUMS.txt", "PACKAGE_VALIDATION.txt"}


def files(skip):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p.name not in skip and "__pycache__" not in p.parts)


def sha(rel):
    return hashlib.sha256((root / rel).read_bytes()).hexdigest()


def write(path, text):
    crlf = b"\r\n" in path.read_bytes()
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
manifest["files"] = [{"path": f, "bytes": len((root / f).read_bytes()), "sha256": sha(f)} for f in files(SKIP)]
write(root / "MANIFEST.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
write(root / "SHA256SUMS.txt", "".join(f"{sha(f)}  {f}\n" for f in files(SKIP - {"MANIFEST.json"})))
sys.exit(subprocess.run([sys.executable, str(root / "tools" / "validate_package.py"), str(root)]).returncode)
