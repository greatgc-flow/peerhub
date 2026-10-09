"""Live-canary support (T1/Wave 8): lowest-tier profiles, isolated workspaces, project-tree snapshots, sanitized evidence.

Never imported by default runs of the live tests (they are opt-in); safe to import offline (pure helpers, no provider calls)."""
from __future__ import annotations

import json
import os
import tempfile
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = ROOT / "docs" / "implementation" / "live_evidence"
OPT_IN_ENV = "PEERHUB_LIVE"
PROVIDERS = ("cc", "cx", "ag")
PROMPT = "Reply with OK"  # <= 20 tokens, fixed tiny response contract
# `.peerhub` is PeerHub's own git-ignored runtime state (databases, live-gate output): a user's running `peerhub monitor`
# rewrites it at any time, and no provider call may be blamed for that. Provider writes elsewhere in the project still fail LIVE-006.
_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "build", "dist", ".mypy_cache", ".ruff_cache", ".peerhub"}
_CACHE: dict[str, dict] = {}


def opt_in_reason() -> str | None:
    """None when live runs are allowed; otherwise a machine-readable skip reason."""
    if os.environ.get("CI"):
        return "LIVE-CI-DISABLED: real provider calls are local-only"
    if os.environ.get(OPT_IN_ENV) == "1":
        return None
    return f"LIVE-OPT-IN[env={OPT_IN_ENV};required=1;marker=live]: real provider calls are disabled unless {OPT_IN_ENV}=1 and -m live"


def lowest_profile(kind: str) -> dict:
    """The packaged `<kind>.standard` profile (lowest tier). Read from the packaged defaults, not hardcoded here."""
    data = tomllib.loads((ROOT / "peerhub" / "config_data" / "model-defaults.toml").read_text(encoding="utf-8"))
    p = data["profiles"][f"{kind}.standard"]
    return {"profile": f"{kind}.standard", "model": p["model"], "effort": p.get("reasoning_effort")}


def tree_snapshot(root: Path = ROOT, *, ignore: tuple[Path, ...] = (EVIDENCE_DIR,)) -> dict[str, tuple[int, int]]:
    """path -> (size, mtime_ns) for every project file except caches and the designated evidence directory."""
    snap: dict[str, tuple[int, int]] = {}
    ign = {Path(i).resolve() for i in ignore}
    for dp, dns, fns in os.walk(root):
        d = Path(dp).resolve()
        dns[:] = [n for n in dns if n not in _SKIP_DIRS and (d / n).resolve() not in ign]
        for fn in fns:
            if fn.endswith(".pyc"):
                continue
            p = d / fn
            try:
                st = p.stat()
            except OSError:
                continue
            snap[str(p.relative_to(root.resolve()))] = (st.st_size, st.st_mtime_ns)
    return snap


def diff_snapshots(a: dict, b: dict) -> list[str]:
    return sorted(k for k in a.keys() | b.keys() if a.get(k) != b.get(k))


def evidence_path() -> Path:
    d = Path(os.environ.get("PEERHUB_LIVE_EVIDENCE_DIR", EVIDENCE_DIR))
    return d / f"{datetime.now(timezone.utc).date().isoformat()}.json"


def load_evidence(path: Path | None = None) -> dict:
    p = path or evidence_path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"schema": "peerhub-m1-live-evidence/1", "providers": {}}


def replace_with_retry(src: Path, dst: Path, attempts: int = 20, delay: float = 0.05) -> None:
    """os.replace that rides out a transient Windows PermissionError (another process briefly holds the target open)."""
    for n in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if n == attempts - 1:
                raise
            time.sleep(min(delay * (n + 1), 0.5))


def merge_evidence(kind: str, section: str, entry: dict, path: Path | None = None) -> Path:
    """Merge one sanitized section for a provider (atomic replace). Entries never contain prompts, responses or secrets."""
    p = path or evidence_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = load_evidence(p)
    doc["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    doc["providers"].setdefault(kind, {})[section] = entry
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    replace_with_retry(tmp, p)
    return p


def make_adapter(kind: str, workspace: Path, timeout_s: float = 240.0):
    from peerhub.extensions.adapters import CliRuntimeTarget

    prof = lowest_profile(kind)
    return CliRuntimeTarget(kind, workspace, model=prof["model"], effort=prof["effort"], timeout_s=timeout_s), prof


def discovery(kind: str) -> dict:
    """One no-token discovery per provider per process (cached)."""
    key = f"discover:{kind}"
    if key not in _CACHE:
        with tempfile.TemporaryDirectory(prefix=f"ph_live_disc_{kind}_") as ws:
            adapter, _ = make_adapter(kind, Path(ws))
            _CACHE[key] = adapter.discover(timeout_s=30)
    return _CACHE[key]


def canary(kind: str) -> dict:
    """Exactly one real call per provider per process: minimal prompt, isolated workspace, project tree snapshotted around it."""
    key = f"canary:{kind}"
    if key in _CACHE:
        return _CACHE[key]
    from tests.communication.bridge_helpers import delivery_rows, kinds, responses
    from tests.communication.harness.bridge import BridgeHarness
    from tests.communication.helpers import req

    before = tree_snapshot()
    base = Path(tempfile.mkdtemp(prefix=f"ph_live_{kind}_"))
    provider_ws = base / "provider_ws"
    provider_ws.mkdir()
    adapter, prof = make_adapter(kind, provider_ws)
    h2 = BridgeHarness(base / "bridge_ws")
    for p in ("a", "b"):
        h2.create_peer({"peer_id": p})
    h2.create_stream({"stream_id": "s", "members": ["a", "b"]})
    rec = h2.append_record(req(body=PROMPT, key="canary", author="a", stream="s"))
    t0 = time.monotonic()
    res = h2.delivery_cycle("b", "s", adapter)
    dur = time.monotonic() - t0
    resp = responses(h2)
    did = delivery_rows(h2, rec.record_id)[-1][0]
    text = ""
    if resp:
        body = json.loads(resp[0][4])
        text = body.get("response", "") if isinstance(body, dict) else str(body)
    after = tree_snapshot()
    out = {
        "status": res.status, "certainty": res.certainty, "response_record": bool(resp), "response_chars": len(text),
        "response_contains_ok": "ok" in text.lower(), "duration_s": round(dur, 2), "evidence_kinds": kinds(h2, did),
        "error": (adapter.evidence[-1].get("error") if adapter.evidence else None),
        "provider_ws_files_after": sorted(p.name for p in provider_ws.iterdir()),
        "workspace_outside_project": ROOT.resolve() not in (base.resolve(), *base.resolve().parents),
        "project_tree_changed": diff_snapshots(before, after),
        "model": prof["model"], "effort": prof["effort"], "profile": prof["profile"],
    }
    _CACHE[key] = out
    return out
