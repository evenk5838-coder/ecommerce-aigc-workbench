#!/usr/bin/env python3
"""Offline S2/R3 review. Only use with sibling commit snapshot in /private/tmp.
No .env, real provider, worker, socket or business DB access. Interleaving probes
are deterministic scheduling injections, not claims of threaded load testing.
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import socket
import sys
import tempfile
import threading
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parent
assert ROOT.is_relative_to(Path('/private/tmp'))
assert not (ROOT/'.env').exists()
os.environ.clear()
os.environ.update({'APP_PROVIDER':'ark','APP_BUDGET_CNY':'100','ASSISTANT_PROVIDER':'none','PYTHONDONTWRITEBYTECODE':'1'})
sys.dont_write_bytecode=True
sys.modules['torch']=None
sys.path.insert(0,str(ROOT/'backend'))
tempfile.tempdir=str(ROOT)
guards={'socket_attempts':0,'thread_start_attempts':0,'outside_db_attempts':0}
def audit(event,args):
    if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)) and Path(os.fsdecode(args[0])).name=='.env':
        raise RuntimeError('.env access forbidden')
    if event=='sqlite3.connect' and not Path(args[0]).resolve().is_relative_to(ROOT):
        guards['outside_db_attempts']+=1
        raise RuntimeError('Business/outside DB forbidden')
    if event in ('socket.connect','socket.connect_ex','socket.getaddrinfo','socket.gethostbyname'):
        guards['socket_attempts']+=1
        raise RuntimeError('Network forbidden')
sys.addaudithook(audit)
def no_network(*a,**k):
    guards['socket_attempts']+=1
    raise RuntimeError('Network forbidden')
def no_thread(*a,**k):
    guards['thread_start_attempts']+=1
    raise RuntimeError('Worker/thread start forbidden')
socket.create_connection=no_network
socket.socket.connect=no_network
socket.socket.connect_ex=no_network
threading.Thread.start=no_thread

suites=[]
with (ROOT/'suite-run.log').open('w') as log, contextlib.redirect_stdout(log):
    for name in ('tools_s1_fix_tests.py','tools_s2_projects_tests.py'):
        ns=runpy.run_path(str(ROOT/'backend'/name),run_name='isolated_'+name.replace('.','_'))
        code=ns['run']()
        suites.append({'script':name,'exit_code':code,'total':len(ns['RESULTS']),
                       'passed':sum(x['ok'] for x in ns['RESULTS']),
                       'db':str(ns['config'].DB_PATH)})
        assert code==0,suites[-1]

from app import config,db,main,projects
from app.assistant import provider as prov
from app.errors import AppError
# A third, new DB isolates independent probes from the provided suites.
extra_dir=Path(tempfile.mkdtemp(prefix='s2-independent-'))
config.DB_PATH=extra_dir/'app.db'
config.ASSETS=extra_dir/'assets';config.ASSETS.mkdir()
db.init_db()
prov.set_provider(prov.UnavailableProvider())
results=[]
def project(name):
    return projects.create_project(name,{'subject':'单瓶精华液','quantity':1})['project_id']
def summarize_script(s):
    return {k:s.get(k) for k in ('script_id','version','confirmed_at','content')}

# F1: stale is returned on PATCH but not persisted/recoverable through GET.
pid=project('stale restoration')
s1=projects.add_script(pid,{'summary':'old brief script'})
projects.confirm_script(s1['script_id'])
patch=projects.update_project(pid,brief={'subject':'两件套装','quantity':2})
restored=projects.project_detail(pid)
restored_script=projects.get_script(s1['script_id'])
assert patch['stale']==['script_confirm','shot_plan_confirm']
assert restored['status']=='script_confirmed'
assert restored['latest_script']['confirmed_at'] is not None
assert 'stale' not in restored and 'stale' not in restored_script
results.append({'case':'brief_change_stale_not_restored','finding_confirmed':True,
                'patch_stale':patch['stale'],'get_project':restored,
                'get_script':summarize_script(restored_script)})

# F2a: deterministic schedule: A checks base 1, B commits 2, A then commits 3.
pid=project('optimistic conflict interleaving')
s1=projects.add_script(pid,{'summary':'v1'})
orig_next=projects._next_version
armed=True
nested=[]
def scheduled_next(project_id):
    global armed
    observed=orig_next(project_id)
    if armed and project_id==pid:
        armed=False
        nested.append(projects.edit_script(s1['script_id'],{'summary':'B based on v1'},base_version=1))
    return observed
projects._next_version=scheduled_next
try:
    outer=projects.edit_script(s1['script_id'],{'summary':'A based on v1'},base_version=1)
finally:
    projects._next_version=orig_next
assert nested[0]['version']==2 and outer['version']==3
results.append({'case':'base_version_check_not_atomic','finding_confirmed':True,
                'method':'deterministic interleaving at first _next_version return; no threads',
                'both_requested_base_version':1,'request_B_version':nested[0]['version'],
                'request_A_version':outer['version'],'conflict_409_returned':False,
                'versions':[{k:s[k] for k in ('version','content')} for s in projects.list_scripts(pid)]})

# F2b: both additions calculate v2 before the first insert -> UNIQUE constraint.
pid=project('version allocation interleaving')
projects.add_script(pid,{'summary':'v1'})
orig_next=projects._next_version
armed=True
nested=[]
def allocation_next(project_id):
    global armed
    observed=orig_next(project_id)
    if armed and project_id==pid:
        armed=False
        nested.append(projects.add_script(pid,{'summary':'B adds v2'}))
    return observed
projects._next_version=allocation_next
allocation_error=None
try:
    projects.add_script(pid,{'summary':'A also adds v2'})
except Exception as exc:
    allocation_error={'type':type(exc).__name__,'message':str(exc)}
finally:
    projects._next_version=orig_next
assert allocation_error and allocation_error['type']=='IntegrityError'
results.append({'case':'version_allocation_not_atomic','finding_confirmed':True,
                'method':'deterministic interleaving during version allocation; no threads',
                'error':allocation_error,'retained_versions':len(projects.list_scripts(pid))})

# Related upstream-state window: brief changes while a slow LLM call runs.
pid=project('inflight brief update')
class ChangesBriefProvider:
    name='fixed-review';model='offline-review'
    def available(self): return True
    def chat(self,messages,tools,tool_results=None):
        before=json.loads(messages[1]['content'])['brief']
        assert before['subject']=='单瓶精华液'
        projects.update_project(pid,brief={'subject':'两件套装','quantity':2})
        return prov.ChatResult(reply=json.dumps({'summary':'针对旧需求单瓶的脚本','shots':[]}),input_tokens=1,output_tokens=1)
generated=projects.generate_script(pid,provider=ChangesBriefProvider())
restored=projects.project_detail(pid)
assert restored['brief']['quantity']==2 and generated['content']['summary']=='针对旧需求单瓶的脚本'
results.append({'case':'inflight_generation_has_no_brief_version_binding','finding_confirmed':True,
                'returned_script':generated,'current_brief':restored['brief'],
                'stale_in_result':generated.get('stale'),'stale_in_get_project':restored.get('stale')})

# F3: available() only indicates configuration; a runtime provider error escapes.
pid=project('runtime provider failure')
class TimeoutProvider:
    name='fixed-review';model='offline-review'
    def available(self): return True
    def chat(self,*a,**k): raise AppError('ASSISTANT_TIMEOUT','offline timeout stub',http=504)
failed=None
try:
    projects.generate_script(pid,provider=TimeoutProvider())
except AppError as exc:
    failed={'code':exc.code,'http':exc.http}
assert failed=={'code':'ASSISTANT_TIMEOUT','http':504}
assert projects.list_scripts(pid)==[]
manual=projects.add_script(pid,{'summary':'manual continuation'})
results.append({'case':'runtime_unavailable_does_not_return_unavailable_envelope','finding_confirmed':True,
                'escaped_error':failed,'manual_fallback_still_usable':manual['version']==1})

from app.routers import creative
creative_routes=[{'path':r.path,'methods':sorted(r.methods)} for r in creative.router.routes
                 if hasattr(r,'methods') and (r.path.startswith('/api/projects') or r.path.startswith('/api/scripts'))]
assert len(creative_routes)==10,creative_routes
manifest=json.loads((ROOT/'source-manifest.json').read_text())
assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==sha for p,sha in manifest['files'].items())
summary={'revision':manifest['revision'],'executed_at':datetime.now(timezone.utc).isoformat(),
         'source_manifest':str(ROOT/'source-manifest.json'),'source_hashes_unchanged':True,
         'provided_suites':suites,'creative_routes':creative_routes,'independent_results':results,
         'independent_db':str(config.DB_PATH),
         'isolation':{'business_db_opened':False,'env_read':False,'network_sent':0,
                      'paid_calls':0,'real_llm_calls':0,'worker_started':False,'guards':guards},
         'limits':['No ASGI/HTTP transport exercised; direct Python handlers/services only.',
                   'Version races are deterministic interleaving reproductions, not a concurrent load test.',
                   'LLM content and runtime timeout are explicit substitutes; no real DeepSeek quality claim.',
                   'Provided suites pass but do not test persisted stale restoration or overlapping edit/write schedules.']}
path=ROOT/'independent-review-results.json'
path.write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print(json.dumps({'result':str(path),'provided_suites':suites,'independent_probes':len(results),
                  'source_hashes_unchanged':True},ensure_ascii=False))
