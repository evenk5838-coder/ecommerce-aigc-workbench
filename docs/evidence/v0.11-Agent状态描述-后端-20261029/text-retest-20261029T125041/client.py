from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import httpx,json,uuid,sqlite3,hashlib
R=Path('/Users/niannian/Documents/VScode/电商工作平台');E=R/'docs/evidence/v0.11-Agent状态描述-后端-20261029'/('text-retest-'+datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%dT%H%M%S'));E.mkdir(exist_ok=False);B='http://127.0.0.1:52665';D=Path('/private/tmp/ecom-v011-real-acceptance/closing-20261029-113020');meta=json.loads((R/'docs/evidence/v0.11-收口真实复验-20261029/session.json').read_text());prefix='/api/projects/'+meta['pid']+'/director'
def save(n,v):(E/(n+'.json')).write_text(json.dumps(v,ensure_ascii=False,indent=2))
def req(path,body=None):
 r=httpx.get(B+path,timeout=180) if body is None else httpx.post(B+path,json=body,timeout=180);r.raise_for_status();return r.json()
def snapshot():
 c=sqlite3.connect((D/'acceptance.db').as_uri()+'?mode=ro',uri=True);out={}
 for table in ['script_versions','canvas_nodes','director_actions','confirmations','confirmation_events','tasks','assets','shots']:
  rows=c.execute('SELECT * FROM '+table).fetchall();out[table]={'count':len(rows),'sha256':hashlib.sha256(json.dumps(sorted(rows,key=repr),ensure_ascii=False,default=str).encode()).hexdigest()}
 c.close();return out
save('authorization',{'user_instruction':'继续修复任务状态描述；沿用原批次剩余文本额度，不生成图片视频','original_approval_unchanged':True,'original_run_id':D.name,'media_transport_disabled':True})
before=snapshot();save('creative-before',before);ledger_before=req('/__acceptance__/ledger');save('ledger-before',ledger_before)
cases=[('01-completed-versus-new-idea','只讨论、不执行：第二镜中性浅灰的那次重做现在是什么状态？新说的浅蓝灰还只是想法。请简短区分已有结果与新想法。')]
results=[]
for name,message in cases:
 state=req(prefix+'/conversation/state');body={'message':message,'client_request_id':uuid.uuid4().hex,'session_id':meta['session_id'],'selected_node_ids':[],'expected_workflow_revision':state['workflow_revision']};save(name+'-input',body)
 out=req(prefix+'/conversation',body);save(name,out);print(json.dumps({'case':name,'reply':out['reply'],'outcome':out['outcome'],'tools':[x['tool'] for x in out['tool_calls']]},ensure_ascii=False),flush=True)
 results.append({'case':name,'reply':out['reply'],'outcome':out['outcome'],'artifacts':out['artifacts'],'actions':out['actions']})
 if out['outcome']['status']=='failed':break
 save('ledger-progress',req('/__acceptance__/ledger'))
after=snapshot();save('creative-after',after);save('ledger-after',req('/__acceptance__/ledger'));save('summary',{'cases':results,'creative_state_unchanged':before==after,'paid_retry':False});print('EVIDENCE='+str(E),flush=True)
