"""Command-line interface for PeerHub."""

import argparse
import sys
import time
import uuid
import sqlite3
from importlib.metadata import version
from pathlib import Path

from peerhub.core.context import Clock, IdSource, PathLayout, RuntimeContext
from peerhub.runtime import create_runtime

class SystemClock(Clock):
    """Real system clock for production use."""
    def now(self) -> int:
        return int(time.time())

class UuidSource(IdSource):
    """Real UUID source for production use."""
    def new_id(self, namespace: str) -> str:
        # e.g., command_12345
        # The prompt says namespace is passed, so we can prepend it
        if not namespace:
            return str(uuid.uuid4())
        return f"{namespace}_{uuid.uuid4().hex[:8]}"

def main(args: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PeerHub Local Coordination CLI")
    parser.add_argument("--version", action="version", version=version("peerhub"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    status_parser = subparsers.add_parser("status", help="Show the current workspace status")
    status_parser.add_argument(
        "--workspace", 
        default=".", 
        help="Path to the workspace root (default: current directory)"
    )
    
    parsed = parser.parse_args(args)
    
    if parsed.command == "status":
        workspace_root = Path(parsed.workspace).resolve()
        paths = PathLayout.for_workspace(workspace_root)
        
        print(f"Workspace: {workspace_root}")
        print(f"Database: {paths.database_path}")
        
        if not paths.database_path.exists():
            print("Status: Workspace uninitialized (no database found)")
            return 0
            
        context = RuntimeContext(
            workspace_home_id=workspace_root.name,
            paths=paths,
            clock=SystemClock(),
            ids=UuidSource(),
        )
        
        # create_runtime will construct all services
        with create_runtime(context, adapter_peer_kind="fake") as runtime:
            # Check migrations
            conn = runtime.state_store._connect()  # pyright: ignore[reportPrivateUsage]
            try:
                migrations = runtime.state_store._migration_versions(conn)  # pyright: ignore[reportPrivateUsage]
                print(f"Schema Migrations Applied: {len(migrations)}")
            except sqlite3.OperationalError:
                print("Schema Migrations Applied: 0 (table missing)")
            finally:
                conn.close()
                
            # Health circuit
            print("Health Circuit ('system'): (no listing API exists yet -- not queryable from the CLI)")
            
            # Active leases
            active_leases = runtime.dispatch_service.count_active_leases()
            print(f"Active Leases: {active_leases}")
            print("Status: OK")
            
    return 0

if __name__ == "__main__":
    sys.exit(main())
