from pathlib import Path
from datetime import datetime,timezone
import copy,hashlib,json,os,shutil,sqlite3,sys,tempfile
from unittest.mock import patch
ROOT=Path('/Users/niannian/Documents/VScode/电商工作平台')
SOURCE=Path('/private/tmp/ecom-v011-real-acceptance/run-20261028-real-v011')
EVIDENCE=ROOT/'docs/evidence/v0.11-真实联调与整版验收-20261028'/('real-context-budget-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
EVIDENCE.mkdir(exist_ok=False)
TMP=Path(tempfile.mkdtemp(prefix='real-context-budget-',dir='/private/tmp'))
read_db=sqlite3.connect('file:'+str(SOURCE/'acceptance.db')+'?mode=ro',uri=True)
copy_db=sqlite3.connect(TMP/'check.db')
read_db.backup(copy_db)
read_db.close();copy_db.close()
shutil.copytree(SOURCE/'assets',TMP/'assets')
(TMP/'models').mkdir()
os.environ.clear()
os.environ.update(PATH='/usr/bin:/bin',LANG='en_US.UTF-8',PYTHONDONTWRITEBYTECODE='1')
sys.path.insert(0,str(ROOT/'backend'))
read=Path.read_text
with patch.object(Path,'read_text',lambda path,*a,**kw:'' if path.name=='.env' else read(path,*a,**kw)),patch.object(Path,'mkdir',lambda *a,**kw:None):
    from app import config
config.DATA=TMP;config.DB_PATH=TMP/'check.db';config.ASSETS=TMP/'assets';config.MODELS=TMP/'models'
config.DIRECTOR_MAX_COST_PER_CALL_CNY=.05;config.DIRECTOR_MAX_SESSION_COST_CNY=.30
connect=sqlite3.connect
def private(database,*a,**kw):
    if Path(str(database)).resolve()!=config.DB_PATH.resolve():raise AssertionError('Only copied DB allowed')
    return connect(database,*a,**kw)
with patch('sqlite3.connect',side_effect=private),patch.object(Path,'read_text',lambda path,*a,**kw:'' if path.name=='.env' else read(path,*a,**kw)),patch('socket.socket.connect',side_effect=AssertionError('No network')),patch('socket.socket.connect_ex',side_effect=AssertionError('No network')),patch('socket.create_connection',side_effect=AssertionError('No network')):
    from app import db,director_workflow as workflow,director_chat as legacy
    from app.assistant.provider import FixedReplyProvider
    pid='prj_7d52f89107db4ce4b9b4'
    original=db.query('SELECT request_json FROM director_requests WHERE project_id=? AND client_request_id=?',(pid,'8adece98-7b6d-423d-80d7-17f6fe046ae6'))[0]
    old=db.loads(original['request_json'])
    selected=old['selected_node_ids']
    context=workflow.creation.creation_context(pid,selected)
    messages=[{'role':'system','content':workflow.SYSTEM_PROMPT},{'role':'context','content':{'current_role':context['current_role'],'creation':context,'project':legacy._tool_state(pid)}},*legacy._history(pid),{'role':'user','content':old['message']}]
    initial=workflow._estimate(messages,config.DIRECTOR_MAX_OUTPUT_TOKENS,provider_name='deepseek',model='deepseek-flash')
    body={'message':old['message'],'client_request_id':'offline-current-real-context-budget','session_id':old['session_id'],'selected_node_ids':selected}
    class Capture(FixedReplyProvider):
        name='deepseek'
        def __init__(self):
            super().__init__([{'tool_calls':[{'id':'read-models-offline','name':'list_models','arguments':{'kind':'video'}}]}, {'tool_calls':[{'id':'respond-offline','name':'respond_to_user','arguments':{'kind':'discuss','message':'仅在数据库副本检查预算。','missing_fields':[]}}]}],model='deepseek-flash')
            self.payloads=[]
        def chat(self,messages,tools,tool_results=None,*,max_tokens=None):
            self.payloads.append(copy.deepcopy(messages))
            return super().chat(messages,tools,tool_results,max_tokens=max_tokens)
    blocked_capture=Capture()
    blocked_result=workflow.conversation(pid,body,provider=blocked_capture)
    body['client_request_id']='offline-capture-continuation'
    config.DIRECTOR_MAX_COST_PER_CALL_CNY=0;config.DIRECTOR_MAX_SESSION_COST_CNY=0
    capture=Capture()
    result=workflow.conversation(pid,body,provider=capture)
    rounds=[]
    for i,payload in enumerate(capture.payloads):
        estimate=workflow._estimate(payload,config.DIRECTOR_MAX_OUTPUT_TOKENS,provider_name='deepseek',model='deepseek-flash')
        rounds.append({'round':i+1,'estimated_cny':estimate,'blocked_by_default_005':estimate>.05,'characters':len(json.dumps(payload,ensure_ascii=False)),'messages_sha256':hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()})
    summary={'project_id':pid,'selected_node_ids':selected,'context_node_count':len(context['nodes']),'context_role':context['current_role'],'history_messages':len(legacy._history(pid)),'initial_before_simulation_cny':initial,'default_cap_cny':.05,'default_session_cap_cny':.30,'actual_default_result':blocked_result['outcome'],'default_provider_calls':len(blocked_capture.calls),'capture_rounds':rounds,'capture_result':result['outcome'],'real_model_calls':0,'network_blocked':True,'original_db_opened_readonly':True,'only_copy_mutated':True,'copy_directory':str(TMP),'product_sha256':hashlib.sha256((ROOT/'backend/app/director_workflow.py').read_bytes()).hexdigest()}
    (EVIDENCE/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    shutil.copy2(__file__,EVIDENCE/'check.py')
    print(json.dumps({'evidence':str(EVIDENCE),**summary},ensure_ascii=False,indent=2))
