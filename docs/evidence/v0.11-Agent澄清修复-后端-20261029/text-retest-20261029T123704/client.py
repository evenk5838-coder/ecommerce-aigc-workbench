from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import httpx,json,uuid,sqlite3,hashlib
R=Path('/Users/niannian/Documents/VScode/电商工作平台');E=R/'docs/evidence/v0.11-Agent澄清修复-后端-20261029'/('text-retest-'+datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%dT%H%M%S'));E.mkdir(exist_ok=False);B='http://127.0.0.1:52665';D=Path('/private/tmp/ecom-v011-real-acceptance/closing-20261029-113020');meta=json.loads((R/'docs/evidence/v0.11-收口真实复验-20261029/session.json').read_text());prefix='/api/projects/'+meta['pid']+'/director'
def save(n,v):(E/(n+'.json')).write_text(json.dumps(v,ensure_ascii=False,indent=2))
def req(path,body=None):
 r=httpx.get(B+path,timeout=180) if body is None else httpx.post(B+path,json=body,timeout=180);r.raise_for_status();return r.json()
def snapshot():
 c=sqlite3.connect((D/'acceptance.db').as_uri()+'?mode=ro',uri=True);out={}
 for table in ['script_versions','canvas_nodes','director_actions','confirmations','confirmation_events','tasks','assets','shots']:
  rows=c.execute('SELECT * FROM '+table).fetchall();out[table]={'count':len(rows),'sha256':hashlib.sha256(json.dumps(sorted(rows,key=repr),ensure_ascii=False,default=str).encode()).hexdigest()}
 c.close();return out
save('authorization',{'user_instruction':'用刚才剩余的文本测试额度，真实复测重复追问，先别生成图片和视频','original_approval_unchanged':True,'original_run_id':D.name,'media_transport_disabled':True})
before=snapshot();save('creative-before',before);ledger_before=req('/__acceptance__/ledger');save('ledger-before',ledger_before)
cases=[('01-original-wording','继续只讨论、不写入：保持白色、无把手、圆口、不加文字、不加碟子杯盖。第二镜只是背景色调整，仍是杯口特写。请简短确认你理解，不重写脚本、不建视频节点。'),('02-explicit-resolved','中性浅灰已经确定，不需要让我再选纯灰或偏暖灰。我们现在只确认理解：第二镜是中性浅灰背景和杯口暖色亮边，第一镜完全不动。请简短复述共识，不修改任何节点，不生成素材。'),('03-latest-answer','我现在明确更正刚才的背景决定：第二镜改成浅蓝灰背景，不再用中性浅灰；第一镜不动。仅讨论并确认你理解，别修改节点或创建报价。不要再让我选择旧的纯灰/偏暖灰方案，也不要生成图片视频。')]
results=[]
for name,message in cases:
 state=req(prefix+'/conversation/state');body={'message':message,'client_request_id':uuid.uuid4().hex,'session_id':meta['session_id'],'selected_node_ids':[],'expected_workflow_revision':state['workflow_revision']};save(name+'-input',body)
 out=req(prefix+'/conversation',body);save(name,out);print(json.dumps({'case':name,'reply':out['reply'],'outcome':out['outcome'],'tools':[x['tool'] for x in out['tool_calls']]},ensure_ascii=False),flush=True)
 results.append({'case':name,'reply':out['reply'],'outcome':out['outcome'],'artifacts':out['artifacts'],'actions':out['actions']})
 if out['outcome']['status']=='failed':break
 save('ledger-progress',req('/__acceptance__/ledger'))
after=snapshot();save('creative-after',after);save('ledger-after',req('/__acceptance__/ledger'));save('summary',{'cases':results,'creative_state_unchanged':before==after,'paid_retry':False});print('EVIDENCE='+str(E),flush=True)
