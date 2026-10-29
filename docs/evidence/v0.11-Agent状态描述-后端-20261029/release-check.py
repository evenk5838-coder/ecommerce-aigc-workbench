from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import sqlite3,json,hashlib,subprocess,sys
R=Path('/Users/niannian/Documents/VScode/电商工作平台');E=R/'docs/evidence/v0.11-Agent状态描述-后端-20261029'
def h(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def tables(path):
 c=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
 result={}
 for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
  rows=c.execute('SELECT * FROM "'+t.replace('"','""')+'"').fetchall();rows=sorted(rows,key=repr)
  result[t]={'count':len(rows),'sha256':hashlib.sha256(json.dumps(rows,ensure_ascii=False,default=str).encode()).hexdigest()}
 c.close();return result
def files(base):return {str(p.relative_to(base)):h(p) for p in base.rglob('*') if p.is_file()}
def idle():
 c=sqlite3.connect((R/'data/app.db').as_uri()+'?mode=ro',uri=True)
 assert not c.execute("SELECT 1 FROM tasks WHERE status NOT IN ('succeeded','failed','canceled','interrupted')").fetchall()
 assert not c.execute("SELECT 1 FROM director_requests WHERE status!='completed'").fetchall();c.close()
if sys.argv[1]=='prepare':
 idle();B=R/'data/backups'/('agent-status-'+datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%dT%H%M%S'));B.mkdir()
 rd=sqlite3.connect((R/'data/app.db').as_uri()+'?mode=ro',uri=True);wr=sqlite3.connect(B/'app.db');rd.backup(wr);rd.close();wr.close()
 (B/'director_workflow.py').write_bytes(subprocess.check_output(['git','show','HEAD:backend/app/director_workflow.py'],cwd=R))
 v={'backup':str(B),'tables':tables(B/'app.db'),'assets':files(R/'data/assets'),'frontend':files(R/'frontend/dist'),'new_source_sha256':h(R/'backend/app/director_workflow.py'),'context_source_sha256':h(R/'backend/app/director_context.py')}
 (E/'release-preparation.json').write_text(json.dumps(v,ensure_ascii=False,indent=2));print('Backup prepared; no active tasks or requests')
elif sys.argv[1]=='check-before-stop':
 idle();v=json.loads((E/'release-preparation.json').read_text());assert tables(R/'data/app.db')==v['tables'];print('Data unchanged since backup; safe to restart')
elif sys.argv[1]=='verify':
 v=json.loads((E/'release-preparation.json').read_text());assert tables(R/'data/app.db')==v['tables'];assert files(R/'data/assets')==v['assets'];assert files(R/'frontend/dist')==v['frontend'];assert h(R/'backend/app/director_workflow.py')==v['new_source_sha256'];assert h(R/'backend/app/director_context.py')==v['context_source_sha256']
 c=sqlite3.connect((R/'data/app.db').as_uri()+'?mode=ro',uri=True);assert c.execute('PRAGMA quick_check').fetchone()[0]=='ok';c.close()
 result={'business_tables_identical':True,'table_count':len(v['tables']),'assets_identical':True,'frontend_identical':True,'source_sha256':v['new_source_sha256'],'real_model_calls':0,'backup':v['backup']};(E/'release-verification.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
