import os, sys, json, sqlite3, tempfile, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import r103
from r103 import FakeStateStore, run_install, run_restore, now_utc

def seed(store):
    with store.unit_of_work() as uow:
        for rid, p in (("receipt-1", "/path/to/bin1"), ("receipt-2", r"C:\Tools\Node\node.exe")):
            uow.conn.execute(
                "INSERT INTO manifest_admission_receipts (admission_receipt_id, manifest_canonical_sha256,"
                " schema_version, adapter_id, peer_kind, inventory_generation, trust_root_json,"
                " observed_vendor_json, chain_complete, aggregate_chain_digest, timestamp_utc,"
                " transitive_executable_chain_json, companion_binaries_json, admitted_at_utc,"
                " prov_chain_complete) VALUES (?, 'sha','2','ad','pk',1,'{}','{}',0,'dig','t',?,'[]','t',0)",
                (rid, json.dumps([{"canonical_path": p}])))
        uow.commit()

print("=" * 72)
print("PROBE 4: a *protocol-mandated abort* after INTENT_DECLARED -- is it terminal?")
print("  Spec L1054: step 4 'Any other outcome -> abort ERR_SHIM_EXTERNALLY_MODIFIED'")
print("=" * 72)
with tempfile.TemporaryDirectory() as td:
    store = FakeStateStore(os.path.join(td, "t.db")); store.initialize(); seed(store)
    P = os.path.join(td, "cc.bat")
    run_install(store, "k1", "cc", P, b"V1", "prof", "/path/to/bin1", "receipt-1")
    # begin a legitimate MANAGED_UPDATE, crash before FS write
    run_install(store, "k2", "cc", P, b"V2", "prof", "/path/to/bin1", "receipt-1",
                crash_before_fs_write=True)
    # an external editor touches the shim while we are down
    with open(P, "wb") as f:
        f.write(b"EXTERNALLY EDITED")
    try:
        run_install(store, "k2", "cc", P, b"V2", "prof", "/path/to/bin1", "receipt-1")
        print("  -> resumed and wrote anyway (!)")
    except ValueError as e:
        print(f"  -> abort raised as designed: {e}")
    with store.unit_of_work() as uow:
        row = uow.conn.execute("SELECT operation_state FROM shim_pending_operations WHERE idempotency_key='k2'").fetchone()
        print(f"  -> pending-operation state after the abort: {row[0]}  (non-terminal, forever)")
        allowed = uow.conn.execute("SELECT sql FROM sqlite_master WHERE name='shim_pending_operations'").fetchone()[0]
        print(f"  -> states the schema permits: "
              f"{[s for s in ['INTENT_DECLARED','FS_STAGED','COMPLETED','ABORTED','FAILED','CANCELLED'] if s in allowed]}")
    print("  -> every later attempt on this shim:")
    for k in ("k9",):
        try:
            run_install(store, k, "cc", P, b"V3", "prof", "/path/to/bin1", "receipt-1")
            print("     succeeded")
        except Exception as e:
            print(f"     BLOCKED: {e}")
    store._keeper_conn.close()

print()
print("=" * 72)
print("PROBE 5: item B <-> item C interface -- admission-target comparison is raw '!='")
print("  Item B stores canonical_path = os.path.normpath(os.path.abspath(...)), which on")
print("  Windows preserves case. Item C (L1225) compares with '!=', no case-folding, no")
print("  real-identity resolution -- unlike its own canonical_shim_path invariant (L890).")
print("=" * 72)
with tempfile.TemporaryDirectory() as td:
    store = FakeStateStore(os.path.join(td, "t.db")); store.initialize(); seed(store)
    P = os.path.join(td, "node.bat")
    print(r"  receipt-2 pinned entrypoint: C:\Tools\Node\node.exe")
    for supplied in (r"C:\Tools\Node\node.exe", r"c:\tools\node\node.exe",
                     r"C:\Tools\Node\..\Node\node.exe"):
        try:
            run_install(store, "k-" + supplied, "n" + str(abs(hash(supplied)) % 999),
                        P + str(abs(hash(supplied)) % 999), b"P", "prof", supplied, "receipt-2")
            print(f"  supplied {supplied!r:42} -> ACCEPTED")
        except ValueError as e:
            print(f"  supplied {supplied!r:42} -> REJECTED ({e})")
    store._keeper_conn.close()

print()
print("=" * 72)
print("PROBE 6: does Section C model the real doc's SS2.3 fail-closed/--force gate?")
print("=" * 72)
import inspect
sig = inspect.signature(run_install)
print(f"  run_install signature: {list(sig.parameters)}")
print(f"  any 'force'/authorization parameter present: "
      f"{any('force' in p for p in sig.parameters)}")
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "r103.py"), encoding="utf-8").read()
print(f"  'ERR_SHIM_COLLISION_DETECTED' appears in delivered impl: "
      f"{'ERR_SHIM_COLLISION_DETECTED' in src}")
print("  => a foreign file at P is silently backed up and overwritten with no")
print("     explicit user authorization recorded anywhere (override_reason is a free")
print("     string the caller passes, not an authorization gate).")
