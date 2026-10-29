from importlib.machinery import SourceFileLoader
c=SourceFileLoader('c','/private/tmp/ecom-closing-client.py').load_module()
meta=c.json.loads((c.E/'session.json').read_text());prefix='/api/projects/'+meta['pid']+'/director';a=c.json.loads((c.E/'01-script.json').read_text())['actions'][0]
c.save('02-script-confirm',c.req(prefix+'/actions/'+a['action_id']+'/confirm',{'client_request_id':c.uuid.uuid4().hex,'user_action':'confirm_script'}))
ref=c.json.loads((c.E/'reference.json').read_text())['asset_id']
c.talk('03-boards','脚本已确认。请作为导演按脚本创建两张图片分镜节点并准备整组报价，不要生成视频。两镜都必须使用商品参考图 '+ref+'，在prepare_storyboards顶层指定input_asset_id让本组共用；模型doubao-seedream-5-0-flash-260915，size=2688x1536。不改变白色无把手杯外形、不加碟子杯盖标识；按已确认的两镜构图即可。',['script'])
