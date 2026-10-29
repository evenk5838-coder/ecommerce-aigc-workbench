from importlib.machinery import SourceFileLoader
import time,sys
c=SourceFileLoader('c','/private/tmp/ecom-closing-client.py').load_module();meta=c.json.loads((c.E/'session.json').read_text());prefix='/api/projects/'+meta['pid']+'/director';stage=sys.argv[1];v=c.json.loads((c.E/(stage+'.json')).read_text());a=[x for x in v['actions'] if x['kind']=='generation'];assert len(a)==1
a=a[0];assert len(a['node_ids'])==(2 if stage=='03-boards' else 1)
c.save(stage+'-confirm',c.req(prefix+'/actions/'+a['action_id']+'/confirm',{'client_request_id':c.uuid.uuid4().hex,'user_action':'confirm_generate'}));print('submitted '+stage,flush=True)
for _ in range(180):
 r=c.req(prefix+'/actions/'+a['action_id']);c.save(stage+'-progress',r)
 if r['status'] in ['completed','failed','partial','canceled']:
  print(c.json.dumps(r,ensure_ascii=False)[:5000],flush=True);break
 time.sleep(2)
else: print('Polling limit; no retry submitted',flush=True)
c.save('ledger',c.req('/__acceptance__/ledger'))
