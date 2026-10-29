from importlib.machinery import SourceFileLoader
import sqlite3,shutil,hashlib,collections
from decimal import Decimal
from PIL import Image,ImageOps,ImageDraw
c=SourceFileLoader('c','/private/tmp/ecom-closing-client.py').load_module();m=c.json.loads((c.E/'session.json').read_text());prefix='/api/projects/'+m['pid'];after=c.req(prefix+'/canvas');c.save('final-canvas',after);before=c.json.loads((c.E/'before-edit-canvas.json').read_text());ids=c.json.loads((c.E/'03-boards.json').read_text())['outcome']['node_ids'];get=lambda x,n:next(v for v in x['nodes'] if v['node_id']==n)
assert get(before,ids[0])==get(after,ids[0]),'First node changed'
assert len(after['nodes'])==3
b=c.json.loads((c.E/'06-single-edit-progress.json').read_text());r=c.req(prefix+'/director/actions/'+b['action_id']+'/confirm',{'client_request_id':b['submit_client_request_id'],'user_action':'confirm_generate'});c.save('single-confirm-replay',r)
ledger=c.req('/__acceptance__/ledger');c.save('ledger',ledger);counts=collections.Counter(x['kind'] for x in ledger['calls']);assert counts['image_to_image']==3 and counts['image_to_video']==0 and counts['text_to_video']==0
ap=c.json.loads((c.E/'approval.json').read_text());d=c.pathlib.Path('/private/tmp/ecom-v011-real-acceptance')/ap['run_id'];db=sqlite3.connect((d/'acceptance.db').as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
rows=[dict(x) for x in db.execute('SELECT id,kind,status,attempts,output_asset_id FROM tasks')];assert len(rows)==3 and all(x['kind']=='image' and x['status']=='succeeded' and x['attempts']==1 for x in rows)
assets=[dict(x) for x in db.execute('SELECT id,sha256,rel_path,source FROM assets ORDER BY created_at')];media=c.E/'media';media.mkdir(exist_ok=True)
sheet=Image.new('RGB',(1280,790),'#171b24');draw=ImageDraw.Draw(sheet)
for i,a in enumerate(assets):
 p=d/'assets'/a['rel_path'];assert hashlib.sha256(p.read_bytes()).hexdigest()==a['sha256'];dest=media/(['reference.jpg','shot-01.jpg','shot-02.jpg','shot-02-regenerated.jpg'][i]);shutil.copy2(p,dest);pic=Image.open(dest);tile=ImageOps.contain(pic,(630,355));x=(i%2)*640;y=(i//2)*395;sheet.paste(tile,(x,y+28));draw.text((x+8,y+6),dest.stem,fill='white')
sheet.save(media/'comparison.jpg')
amount=sum(Decimal(x.get('observed_cny',x['reserved_cny'])) if x.get('settled') else max(Decimal(x['reserved_cny']),Decimal(x.get('observed_cny','0'))) for x in ledger['calls'])
summary={'project_id':m['pid'],'counts':dict(counts),'conservative_guard_cny':str(amount),'provider_bill_verified':False,'first_node_byte_equivalent':True,'node_count':len(after['nodes']),'single_confirm_replay_no_new_calls':True,'tasks':rows,'input_characters':c.json.loads((c.E/'long-input-measure.json').read_text())['characters'],'real_model_quality':'pending visual inspection','minor_issue':'Agent repeated an already resolved background-color clarification in two discussion replies','assets':assets}
c.save('summary',summary);print(c.json.dumps({k:v for k,v in summary.items() if k not in ['tasks','assets']},ensure_ascii=False))
