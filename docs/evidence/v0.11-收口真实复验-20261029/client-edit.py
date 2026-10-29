from importlib.machinery import SourceFileLoader
c=SourceFileLoader('c','/private/tmp/ecom-closing-client.py').load_module();m=c.json.loads((c.E/'session.json').read_text());ids=c.json.loads((c.E/'03-boards.json').read_text())['outcome']['node_ids'];c.save('before-edit-canvas',c.req('/api/projects/'+m['pid']+'/canvas'))
c.talk('04-discuss','两张图已经生成。先只讨论后续调整，不要调用任何写入或生成工具：我希望第二镜的背景改为中性浅灰，保留暖光在杯口的亮边；第一镜的木桌晨光全景完全不动。这种调整会影响哪一镜？请明确说出修改范围和仍保留的商品特征。',ids)
c.talk('05-discuss','继续只讨论、不写入：保持白色、无把手、圆口、不加文字、不加碟子杯盖。第二镜只是背景色调整，仍是杯口特写。请简短确认你理解，不重写脚本、不建视频节点。',ids)
text='现在执行刚才已澄清的单镜修改：只更新第二镜 '+ids[1]+' 的提示词并准备它单独的重新生成确认入口，背景改为中性浅灰，杯口暖光亮边保留。第一镜 '+ids[0]+' 不得更改、不得再生成；不重写脚本、不创建第三个图片节点、不创建视频。原参考图、模型、横屏2K尺寸保持。以下逐项复核要求用于检查长输入约束理解：\n'+''.join(f'{i+1}. '+['杯子白色无把手且无标识，保持圆口和略向底部收拢的杯壁。','第二镜继续采用杯口特写，浅灰仅指虚化背景，不能把杯身染灰。','不要添加碟子、杯盖、文字、花纹、道具或性能说明。','第一镜的构图、参考素材、输出文件和已有生成结果完全保留。','保留第二镜原商品参考图与图片模型，不要擅自换图或切成文生图。','只准备选定第二镜的报价，确认前不要提交任务或宣称重做已完成。'][i%6]+'\n' for i in range(48))
c.save('long-input-measure',{'characters':len(text),'repeated_constraint_pressure_case':True})
c.talk('06-single-edit',text,[ids[1]])
c.save('after-edit-canvas',c.req('/api/projects/'+m['pid']+'/canvas'));c.save('ledger',c.req('/__acceptance__/ledger'))
