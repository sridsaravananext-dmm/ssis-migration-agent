"""Version-scoped, append-only review decisions; never override execution gates."""
import json
import sqlite3
from datetime import datetime,timezone


class ReviewStore:
    def __init__(self,path):
        from pathlib import Path
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS decisions (version TEXT NOT NULL, finding TEXT NOT NULL, revision INTEGER NOT NULL, decision TEXT NOT NULL, author TEXT NOT NULL, note TEXT NOT NULL, evidence TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(version,finding,revision))')
    def connect(self):return sqlite3.connect(self.path,timeout=15)
    def record(self,version,finding,*,decision,author,note,evidence=(),expected_revision=0):
        if decision not in {'OPEN','NEEDS_IMPLEMENTATION','ACCEPTED_WARNING','EVIDENCE_RECORDED'}:raise ValueError('Unknown review decision')
        if not isinstance(version,str) or not version.strip() or not isinstance(finding,dict) or not finding.get('finding_id'):raise ValueError('Version and an existing finding record are required')
        if any(not isinstance(v,str) or not v.strip() for v in (author,note)):raise ValueError('Author and review note are required')
        if not isinstance(evidence,(list,tuple)) or any(not isinstance(v,str) or not v.strip() for v in evidence):raise ValueError('Evidence must contain nonempty references')
        if decision=='ACCEPTED_WARNING' and finding.get('severity')!='WARNING':raise ValueError('Blocking findings cannot be accepted as warnings')
        if decision=='EVIDENCE_RECORDED' and not evidence:raise ValueError('Evidence references are required')
        if isinstance(expected_revision,bool) or not isinstance(expected_revision,int) or expected_revision<0:raise ValueError('Expected revision must be nonnegative')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            last=db.execute('SELECT MAX(revision) FROM decisions WHERE version=? AND finding=?',(version,finding['finding_id'])).fetchone()[0] or 0
            if last!=expected_revision:raise ValueError('Stale review revision; reload before writing')
            created=datetime.now(timezone.utc).isoformat()
            db.execute('INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)',(version,finding['finding_id'],last+1,decision,author,note,json.dumps(evidence),created))
        return {'version':version,'finding_id':finding['finding_id'],'revision':last+1,'decision':decision,'execution_gate_changed':False}
    def history(self,version,finding_id=None):
        with self.connect() as db:
            query='SELECT finding,revision,decision,author,note,evidence,created FROM decisions WHERE version=?';args=[version]
            if finding_id is not None:query+=' AND finding=?';args.append(finding_id)
            rows=db.execute(query+' ORDER BY finding,revision',args).fetchall()
        return [{'finding_id':f,'revision':r,'decision':d,'author':a,'note':n,'evidence':json.loads(e),'created_at_utc':t} for f,r,d,a,n,e,t in rows]


def revalidate_notebook(path,*,findings=()):
    """Read an edited notebook without altering it; retain source blockers."""
    import hashlib
    from pathlib import Path
    from ssis_migration.validator.native_spark import validate_native_spark
    raw=Path(path).read_bytes();notebook=json.loads(raw);results=[]
    if not isinstance(notebook,dict) or not isinstance(notebook.get('cells'),list):raise ValueError('Invalid notebook structure')
    for i,cell in enumerate(notebook['cells']):
        if cell.get('cell_type')=='code':results.append({'cell':i,**validate_native_spark(''.join(cell.get('source',[])))})
    source_blockers=[f['finding_id'] for f in findings if f.get('severity')=='ERROR']
    accepted=bool(results) and not source_blockers and all(r['status']=='PASS' for r in results)
    return {'status':'STATIC_CHECKS_PASSED' if accepted else 'REVALIDATION_REQUIRED',
        'notebook_sha256':hashlib.sha256(raw).hexdigest(),'cells':results,'blocking_source_findings':source_blockers,
        'runtime_verification':'NOT_MEASURED','execution_gate_changed':False}
