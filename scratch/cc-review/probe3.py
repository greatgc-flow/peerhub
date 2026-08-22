"""No crash, no process restart: a single-run external edit triggers the design's
own correct ERR_SHIM_EXTERNALLY_MODIFIED abort -- and that abort wedges the shim."""
import os, sys, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import r103
from r103 import FakeStateStore, run_install

ARMED = {"on": False, "P": None}
_orig = r103.SqliteShimRegistryUnitOfWork.mark_backup_staged

def hooked(self, *a, **k):
    r = _orig(self, *a, **k)
    if ARMED["on"]:
        with open(ARMED["P"], "wb") as f:      # an admin hand-edits cc.bat right now
            f.write(b"@echo off\r\n:: hand-edited\r\n")
    return r

r103.SqliteShimRegistryUnitOfWork.mark_backup_staged = hooked

td = tempfile.mkdtemp()
store = FakeStateStore(os.path.join(td, "t.db"))
store.initialize()
with store.unit_of_work() as uow:
    uow.conn.execute(
        "INSERT INTO manifest_admission_receipts (admission_receipt_id, manifest_canonical_sha256,"
        " schema_version, adapter_id, peer_kind, inventory_generation, trust_root_json,"
        " observed_vendor_json, chain_complete, aggregate_chain_digest, timestamp_utc,"
        " transitive_executable_chain_json, companion_binaries_json, admitted_at_utc,"
        " prov_chain_complete) VALUES ('r1','s','2','a','p',1,'{}','{}',0,'d','t',?,'[]','t',0)",
        (json.dumps([{"canonical_path": "/bin/x"}]),))
    uow.commit()

P = os.path.join(td, "cc.bat")
ARMED["P"] = P

run_install(store, "k1", "cc", P, b"V1", "prof", "/bin/x", "r1")
print("1. initial install COMPLETED, shim live on disk:", open(P, "rb").read())

print("2. operator runs a MANAGED_UPDATE; an admin hand-edits cc.bat mid-operation.")
print("   NO crash. NO process restart. One continuous run.")
ARMED["on"] = True
try:
    run_install(store, "k2", "cc", P, b"V2", "prof2", "/bin/x", "r1")
    print("3. -> completed (unexpected)")
except ValueError as e:
    print(f"3. design behaves CORRECTLY and refuses to clobber the edit: {e}")
ARMED["on"] = False

with store.unit_of_work() as uow:
    st = uow.conn.execute("SELECT operation_state FROM shim_pending_operations"
                          " WHERE idempotency_key='k2'").fetchone()[0]
print(f"4. pending operation is left at: {st}   <-- no terminal failure state exists")

print("5. operator investigates, is happy with the file, retries -- any key, any number:")
for k in ("k2", "k3", "k4"):
    try:
        run_install(store, k, "cc", P, b"V2", "prof2", "/bin/x", "r1")
        print(f"     key={k}: succeeded")
    except ValueError as e:
        print(f"     key={k}: BLOCKED -> {e}")

with store.unit_of_work() as uow:
    n = uow.conn.execute(
        "SELECT COUNT(*) FROM shim_registry_entries r JOIN shim_pending_operations o"
        " ON o.shim_registration_id=r.shim_registration_id AND o.operation_state!='COMPLETED'"
        " WHERE r.shim_name='cc' AND r.status='ACTIVE'").fetchone()[0]
print(f"6. per the visibility rule (doc L1034), consumers must treat this ACTIVE row as")
print(f"   unusable while {n} non-terminal op exists. Shim 'cc' is now permanently")
print("   unusable AND permanently un-updatable. Recovery = hand-editing SQLite.")
store._keeper_conn.close()
