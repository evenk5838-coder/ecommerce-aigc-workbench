import httpx as requests; import json,pathlib,uuid
E=pathlib.Path('/Users/niannian/Documents/VScode/电商工作平台/docs/evidence/v0.11-收口真实复验-20261029'); B='http://127.0.0.1:52665'
def req(path,body=None):
 r=requests.get(B+path,timeout=180) if body is None else requests.post(B+path,json=body,timeout=180)
 v=r.json()
 if r.status_code>=400:raise RuntimeError(str(v))
 return v
def save(name,v): (E/(name+'.json')).write_text(json.dumps(v,ensure_ascii=False,indent=2))
def talk(name,text,selected=[]):
 meta=json.loads((E/'session.json').read_text());prefix='/api/projects/'+meta['pid']+'/director';state=req(prefix+'/conversation/state');body={'message':text,'client_request_id':uuid.uuid4().hex,'selected_node_ids':selected,'expected_workflow_revision':state['workflow_revision']}
 if meta.get('session_id'):body['session_id']=meta['session_id']
 save(name+'-input',body);v=req(prefix+'/conversation',body);save(name,v)
 meta['session_id']=v['session_id'];save('session',meta)
 print(json.dumps(v,ensure_ascii=False)[:9000],flush=True);return v
if __name__=='__main__':
 p=pathlib.Path('/Users/niannian/Documents/VScode/电商工作平台/data/acceptance/run-20261028-real-v011/assets/ce/f5/cef5b44088ef4f7bb245bbbaa2098ccf5614f2bf1ede6339d282245b8ace5336.jpg')
 with p.open('rb') as f:r=requests.post(B+'/api/assets',files={'file':('reference.jpg',f,'image/jpeg')},timeout=40)
 r.raise_for_status();a=r.json();save('reference',a)
 pr=req('/api/projects',{'name':'收口真实复验 · 白色无把手杯','brief':{'subject':'白色无把手陶瓷杯','quantity':1,'style':'自然晨光、干净产品画面','target_duration_s':10}});save('session',{'pid':pr['project_id']})
 talk('01-script','请作为编剧直接写两镜10秒横屏脚本。商品是一只白色无把手陶瓷杯，圆口，杯壁略向底部收拢，无标识。第一镜全景，第二镜杯口特写，各5秒，木桌晨光，不新增碟子、杯盖、文字、把手，不声称任何产品性能。所有创作需求已明确，请直接保存脚本并给确认入口，先不要准备图片或视频。参考图素材ID为 '+a['asset_id']+'，供下一阶段两镜共用。')
