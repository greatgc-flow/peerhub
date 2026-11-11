"""Disposable initialization measurements; never uses a user database or provider."""
from __future__ import annotations
import hashlib,json,math,platform,sqlite3,statistics,time
from pathlib import Path
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from contextlib import closing
from peerhub.core.store import CoreStore
from peerhub.core.workspace import Workspace
from peerhub.extensions.bridge_claims import ClaimStore
from peerhub.extensions.bridge import Bridge
import peerhub.extensions.bridge as module

def open_store(db,ws):
    a=time.perf_counter();store=CoreStore(db);b=time.perf_counter()
    claims=ClaimStore(db,ws.generation);c=time.perf_counter();Bridge(store,claims);d=time.perf_counter()
    return dict(core_ms=(b-a)*1000,claims_ms=(c-b)*1000,bridge_ms=(d-c)*1000,total_ms=(d-a)*1000)
def cookie(db):
    with closing(sqlite3.connect(db)) as c:return c.execute('PRAGMA schema_version').fetchone()[0]
rows=[]
with TemporaryDirectory(prefix='bridge-init-') as directory:
    for n in (1,2,4,8):
        root=Path(directory)/str(n);ws=Workspace(root);db=root/'core.db';open_store(db,ws);before=cookie(db);samples=[]
        for _ in range(5):
            barrier=Barrier(n)
            def work(_):barrier.wait(timeout=30);return open_store(db,ws)
            with ThreadPoolExecutor(max_workers=n) as pool:samples.extend(pool.map(work,range(n)))
        values=sorted(s['total_ms'] for s in samples)
        rows.append(dict(workers=n,samples=len(samples),p50_ms=statistics.median(values),p95_ms=values[math.ceil(len(values)*.95)-1],bridge_p50_ms=statistics.median(s['bridge_ms'] for s in samples),schema_cookie_delta=cookie(db)-before,raw_samples=samples))
print(json.dumps(dict(python=platform.python_version(),os=platform.system(),sqlite=sqlite3.sqlite_version,provider_calls=0,rounds=5,source_sha256=hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),cases=rows),indent=2))
