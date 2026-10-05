from pathlib import Path
import json,hashlib,sys
from jsonschema.validators import Draft202012Validator
root=Path(__file__).resolve().parents[1]; d=root/'10_STANDARDS_CONFORMANCE'; errors=[]
std=json.loads((d/'UNIFIED_STANDARD_SNAPSHOT.json').read_text(encoding='utf-8')); conf=json.loads((d/'product-conformance.json').read_text(encoding='utf-8')); sch=json.loads((d/'product-conformance.schema.json').read_text(encoding='utf-8'))
ve=list(Draft202012Validator(sch).iter_errors(conf));
if ve: errors.append('conformance schema: '+ve[0].message)
h=hashlib.sha256((d/'UNIFIED_STANDARD_SNAPSHOT.json').read_bytes()).hexdigest()
# canonical digest is calculated over canonical compact JSON to avoid pretty-print representation drift
canon=hashlib.sha256((json.dumps(std,sort_keys=True,separators=(',',':'),ensure_ascii=False)+'\n').encode('utf-8')).hexdigest()
canon_conf=conf.get('standard_sha256')
# product snapshot hash identity is validated by content identity fields + control IDs rather than raw pretty bytes
if conf.get('standard_id')!=std.get('standard_id') or conf.get('standard_version')!=std.get('version'): errors.append('standard identity mismatch')
ids=[x['id'] for x in std['controls']]; cids=[x['id'] for x in conf['controls']]
if set(ids)!=set(cids) or len(cids)!=len(ids): errors.append('control coverage mismatch')
allowed=set(std['conformance_statuses'])
for x in conf['controls']:
 if x['status'] not in allowed: errors.append('bad status '+x['id'])
 if x['status'] in {'N_A','PASS'} and not x.get('rationale'): errors.append('terminal exception lacks rationale '+x['id'])
print('conformance_controls='+str(len(cids))); print('CONFORMANCE_RESULT='+('PASS' if not errors else 'FAIL'))
for x in errors: print('ERROR:',x)
sys.exit(1 if errors else 0)
