(() => {
  'use strict';
  const app = document.getElementById('app');
  const media = 'showcase/reference-results/';
  const state = { view: 'home', mode: 'text_to_image', result: false, stage: 0, nodes: [], selected: new Set(), history: [], tool: 'select', scale: .8, pan: {x: 36, y: 90}, settings: null, userMessage: '' };
  const modes = { text_to_image: '文生图', image_to_image: '图生图', text_to_video: '文生视频', image_to_video: '图生视频' };
  const roleNames = ['AI 编剧', 'AI 导演', 'AI 摄影师', 'AI 剪辑师', '创作完成'];
  const roleCopy = [
    '想拍什么商品，呈现怎样的感觉？\n这里用“白色陶瓷杯的 10 秒广告”演示。确认需求后，我会起草脚本。',
    '脚本已放进画布。\n分镜建议：柔光静物开场，再用细节与缓慢运镜展示杯身。确认后，画布会出现两张示例分镜。',
    '分镜准备好了。\n保持商品完整，镜头缓慢移动。确认后，展示已有实测视频作为本阶段示例。',
    '镜头视频已就绪。\n下一步按脚本顺序组织片段。本原型展示剪辑阶段与结果节点，不在浏览器中真实合成。',
    '已走完脚本、分镜、镜头视频与剪辑四个阶段。\n这些都是固定演示内容。真实模型结果与 Bad Case 可在仓库附件中查看。'
  ];
  let toastTimer;
  const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const showToast = text => { const el = document.getElementById('toast'); el.textContent = text; el.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.hidden = true; }, 2800); };
  const remember = () => { state.history.push(JSON.stringify(state.nodes)); if (state.history.length > 30) state.history.shift(); };
  const setView = view => { state.view = ['home','quick','director'].includes(view) ? view : 'home'; render(); };
  function renderHome() {
    return `<div class="home"><h1>每个好作品，<br>从一个清晰的想法开始。</h1><div class="launch-grid">
      <section class="launch-card"><div class="launch-copy"><span class="card-icon" aria-hidden="true">▱</span><h2>AI 导演台</h2><p>从一句想法，到完整成片。<br>对话推进，自由画布创作。</p><button class="primary" data-view="director">新建创作项目 <span>→</span></button></div><div class="launch-visual"><img src="${media}reference.jpg" alt="虚构白杯参考图"><span class="visual-tag first">脚本</span><span class="visual-tag middle">分镜</span><span class="visual-tag last">视频</span></div></section>
      <section class="launch-card"><div class="launch-copy"><span class="card-icon" aria-hidden="true">▧</span><h2>快捷工作台</h2><p>一张图片，或一段视频。<br>选择模型，直接开始创作。</p><button class="primary" data-view="quick">进入快捷工作台 <span>→</span></button></div><div class="launch-visual"><img src="${media}image-result.jpg" alt="白杯图生图实测结果"><span class="visual-tag first">图片生成</span><span class="visual-tag last">视频生成</span></div></section>
      </div><section class="home-section"><h2>产品实际界面</h2><p>以下截图来自本地应用，当前页面中的交互属于独立静态原型。</p><a class="screen-link" href="showcase/product/home-light.jpg"><img src="showcase/product/home-light.jpg" alt="物料间真实产品首页截图" loading="lazy"></a></section>
      <div class="home-foot"><span>免费体验交互；所有媒体均读取仓库内的既有样例。</span><a href="https://github.com/evenk5838-coder/ecommerce-aigc-workbench#项目演示">查看真实生成结果与修正对照 ↗</a></div></div>`;
  }
  function renderQuick() {
    const video = state.mode.includes('video'), reference = state.mode.startsWith('image_');
    return `<div class="page"><header class="page-heading"><div><h1>快捷工作台</h1><p>从描述到素材，直接开始。</p></div><span class="fine">固定样例演示</span></header><div class="quick-layout"><section class="settings" aria-label="示例生成设置"><div class="segmented" role="group" aria-label="生成模式">${Object.entries(modes).map(([id,label])=>`<button data-mode="${id}" class="${state.mode===id?'active':''}" aria-pressed="${state.mode===id}">${label}</button>`).join('')}</div>
    ${reference?`<div class="reference"><img src="${media}reference.jpg" alt="本地白杯参考图"><span>使用既有白杯参考图<br>无需上传个人素材</span></div>`:''}<label><span class="field-label">描述你想创作的画面</span><textarea id="quick-prompt" placeholder="描述商品、场景和构图">白色陶瓷杯，柔和自然光，干净的浅色背景，保持商品完整。</textarea></label><label><span class="field-label">模型示例</span><select aria-label="模型示例"><option>${video?'Seedance Fast · 演示':'Seedream Flash · 演示'}</option><option>切换仅用于演示</option></select></label><div class="fields-row"><label><span class="field-label">画面比例</span><select aria-label="示例画面比例"><option>${video?'16:9 横屏':'1:1 方图'}</option><option>9:16 竖屏</option></select></label><label><span class="field-label">${video?'时长':'清晰度'}</span><select aria-label="示例输出规格"><option>${video?'5 秒':'1K'}</option></select></label></div><button class="primary" id="simulate">${state.result?'重新展示示例':'展示生成示例'} <span>↗</span></button><p class="fine">读取已保存的实测结果。修改描述不会改变固定样例，也不会调用模型。</p></section><section class="result-panel" aria-label="示例生成结果">${state.result?`${video?`<video class="result-media" controls playsinline preload="metadata" src="${media}${reference?'image-to-video':'text-to-video'}.mp4"></video>`:`<img class="result-media" src="${media}${reference?'image-result':'reference'}.jpg" alt="已有白杯图片样例">`}<div class="result-caption"><span>已有实测素材 · ${modes[state.mode]}</span><a href="${media}${video?(reference?'image-to-video.mp4':'text-to-video.mp4'):(reference?'image-result.jpg':'reference.jpg')}" download>下载样例 ↓</a></div><p class="fine">这里展示固定文件，不能据此评估当前输入描述的生成效果。</p>`:`<span class="empty-icon" aria-hidden="true">▧</span><h2>让想法成为画面。</h2><p>选择输入方式，查看一份已有生成样例。</p>`}</section></div></div>`;
  }
  function nodeMarkup(node) {
    return `<article class="node ${state.selected.has(node.id)?'selected':''}" data-node="${node.id}" style="left:${node.x}px;top:${node.y}px" tabindex="0" aria-label="${escape(node.title)}"><div class="node-head"><span>${escape(node.type==='text'?'脚本 / 文本':node.type==='image'?'分镜 / 图片':'视频 / 片段')}</span><span aria-hidden="true">⠿</span></div><div class="node-content"><h3>${escape(node.title)}</h3>${node.type==='text'?`<p>${escape(node.content)}</p>`:node.type==='image'?`<img src="${media}${node.file||'reference.jpg'}" alt="已有白杯示例">`:`<video src="${media}${node.file||'image-to-video.mp4'}" preload="metadata" muted playsinline aria-label="既有视频样例"></video>`}</div><div class="node-foot"><span>演示节点</span><span>点击设置</span></div>${state.settings===node.id?`<div class="node-settings"><label>提示词<textarea data-prompt="${node.id}">${escape(node.prompt||'白色陶瓷杯，柔光，商品完整，构图简洁。')}</textarea></label><label>模型示例<select aria-label="节点模型"><option>${node.type==='video'?'Seedance Fast':'Seedream Flash'} · 演示</option></select></label><button class="primary" data-save-node="${node.id}">更新示例设置</button></div>`:''}</article>`;
  }
  function renderDirector() {
    return `<div class="director-page"><div class="director-heading"><strong>导演台 / 白杯广告示例</strong><p>对话推进 · 自由画布</p></div><div class="director-layout"><section class="chat" aria-label="多角色示例对话"><header class="chat-heading"><h2>${roleNames[state.stage]}</h2><p>从想法，到你的作品</p></header><div class="messages">${Array.from({length:state.stage+1},(_,i)=>`<div class="message"><small>${roleNames[i]}</small><div class="bubble">${roleCopy[i]}</div></div>${i<state.stage?'<div class="message user"><small>我</small><div class="bubble">确认，继续下一步。</div></div>':''}`).join('')}${state.userMessage?`<div class="message user"><small>我</small><div class="bubble">${escape(state.userMessage)}</div></div><div class="message"><small>演示说明</small><div class="bubble">已展示你的输入。本原型使用固定流程，点击下方按钮继续体验，不会发送给 AI。</div></div>`:''}</div><div class="chat-compose"><label class="field-label" for="director-input">你的创作想法</label><textarea id="director-input" placeholder="例如：做一条柔和、简洁的白杯广告"></textarea><button class="primary" id="continue-stage" ${state.stage===4?'disabled':''}>${['确认需求，起草脚本','确认脚本，展示分镜','确认分镜，展示视频','确认片段，进入剪辑','流程已完成'][state.stage]} →</button><button class="secondary" id="send-note" style="width:100%;margin-top:7px">发送演示输入</button><p class="fine">角色与回复为预设示例，不代表实时 AI 回答。</p></div></section><section class="canvas-frame ${state.tool==='hand'?'hand':''}" id="canvas" aria-label="可拖动与缩放的示例画布"><div class="canvas-header"><strong>自由画布</strong><span>${state.nodes.length} 个示例节点</span></div><div class="add-node"><button data-add="text">＋文本</button><button data-add="image">＋图片</button><button data-add="video">＋视频</button></div>${!state.nodes.length?'<div class="canvas-empty"><b>从一句想法开始。</b>对话确认后，示例节点会出现在这里。<br>也可以直接添加文本、图片或视频节点。</div>':''}<div class="world" id="world" style="transform:translate(${state.pan.x}px,${state.pan.y}px) scale(${state.scale})"><svg class="connections" width="1800" height="1000" aria-hidden="true">${state.nodes.slice(1).map((node,i)=>{const from=state.nodes[i];return `<path d="M${from.x+260} ${from.y+100} C${from.x+300} ${from.y+100},${node.x-40} ${node.y+100},${node.x} ${node.y+100}"></path>`}).join('')}</svg>${state.nodes.map(nodeMarkup).join('')}</div><div class="canvas-tip">拖动节点标题移动 · 空白处框选 · 抓手平移 · 点击节点调整示例设置</div><div class="canvas-tools" aria-label="画布工具"><button data-tool="select" class="${state.tool==='select'?'active':''}" aria-label="选择与框选" title="选择与框选">↖</button><button data-tool="hand" class="${state.tool==='hand'?'active':''}" aria-label="抓手平移" title="抓手平移">✋</button><span class="divider"></span><button id="zoom-out" aria-label="缩小">−</button><span>${Math.round(state.scale*100)}%</span><button id="zoom-in" aria-label="放大">＋</button><button id="fit" aria-label="适配画布">适配</button><span class="divider"></span><button id="delete-nodes" aria-label="删除选中节点" ${!state.selected.size?'disabled':''}>删除</button><button id="undo" aria-label="撤销节点修改" ${!state.history.length?'disabled':''}>↶</button></div></section></div></div>`;
  }
  function render() {
    app.innerHTML = state.view==='home'?renderHome():state.view==='quick'?renderQuick():renderDirector();
    document.querySelectorAll('[data-view]').forEach(button => button.classList.toggle('active', button.dataset.view===state.view));
    if (state.view==='director') { wireCanvas(); const messages=app.querySelector('.messages'); messages.scrollTop=messages.scrollHeight; }
  }
  function addNode(type, title, x, y, extras={}) { state.nodes.push({id:`node-${crypto.randomUUID ? crypto.randomUUID() : Date.now()+'-'+Math.random()}`,type,title,x,y,...extras}); }
  function advance() {
    if (state.stage>=4) return;
    remember();
    if (state.stage===0) addNode('text','白杯广告 · 创作脚本',0,0,{content:'开场：白杯置于浅色桌面，柔和侧光。\n中段：杯身细节与缓慢运镜。\n收尾：回到完整商品，留下干净空间。'});
    if (state.stage===1) { addNode('image','分镜 01 · 柔光静物',320,0,{file:'reference.jpg'});addNode('image','分镜 02 · 场景调整',320,320,{file:'image-result.jpg'}); }
    if (state.stage===2) { addNode('video','镜头 01 · 视频样例',640,0,{file:'text-to-video.mp4'});addNode('video','镜头 02 · 视频样例',640,320,{file:'image-to-video.mp4'}); }
    if (state.stage===3) addNode('text','剪辑方案 · 结果占位',960,0,{content:'镜头 01 → 镜头 02\n按脚本顺序组织片段。\n本原型不执行真实合成；可在仓库中查看真实复验原片。'});
    state.stage++; state.settings=null; state.userMessage=''; fitCanvas(); render();
  }
  function fitCanvas() {
    const canvas=document.getElementById('canvas'); if(!canvas) return;
    const right=Math.max(300,...state.nodes.map(n=>n.x+285)),bottom=Math.max(300,...state.nodes.map(n=>n.y+320));
    state.scale=Math.min(1,Math.max(.25,Math.min((canvas.clientWidth-65)/right,(canvas.clientHeight-150)/bottom)));
    state.pan={x:30,y:85};
  }
  function updateWorld(){const world=document.getElementById('world');if(world)world.style.transform=`translate(${state.pan.x}px,${state.pan.y}px) scale(${state.scale})`;}
  function wireCanvas() {
    const canvas=document.getElementById('canvas');
    canvas.addEventListener('pointerdown',event=>{
      if(event.button!==0||event.target.closest('button,textarea,select,input'))return;
      const nodeElement=event.target.closest('.node');const start={x:event.clientX,y:event.clientY};let moved=false;
      if(nodeElement){
        const id=nodeElement.dataset.node;
        if(!state.selected.has(id)){if(!event.shiftKey)state.selected.clear();state.selected.add(id);}
        if(!event.target.closest('.node-head')) {state.settings=state.settings===id?null:id;render();return;}
        remember();const origins=new Map(state.nodes.filter(n=>state.selected.has(n.id)).map(n=>[n.id,{x:n.x,y:n.y}]));
        canvas.setPointerCapture(event.pointerId);
        const move=e=>{const dx=(e.clientX-start.x)/state.scale,dy=(e.clientY-start.y)/state.scale;moved ||= Math.abs(dx)+Math.abs(dy)>3;for(const node of state.nodes){const origin=origins.get(node.id);if(origin){node.x=origin.x+dx;node.y=origin.y+dy;const el=canvas.querySelector(`[data-node="${node.id}"]`);el.style.left=node.x+'px';el.style.top=node.y+'px';}}};
        const end=()=>{canvas.removeEventListener('pointermove',move);canvas.removeEventListener('pointerup',end);canvas.removeEventListener('pointercancel',end);if(!moved)state.history.pop();render();};
        canvas.addEventListener('pointermove',move);canvas.addEventListener('pointerup',end);canvas.addEventListener('pointercancel',end);return;
      }
      const origin={...state.pan};canvas.setPointerCapture(event.pointerId);let box;
      if(state.tool==='hand')canvas.classList.add('panning');else{if(!event.shiftKey)state.selected.clear();box=document.createElement('div');box.className='selection-box';canvas.appendChild(box);}
      const rect=canvas.getBoundingClientRect();
      const move=e=>{const dx=e.clientX-start.x,dy=e.clientY-start.y;moved ||= Math.abs(dx)+Math.abs(dy)>3;
        if(state.tool==='hand'){state.pan={x:origin.x+dx,y:origin.y+dy};updateWorld();}else{const left=Math.min(start.x,e.clientX)-rect.left,top=Math.min(start.y,e.clientY)-rect.top,width=Math.abs(dx),height=Math.abs(dy);Object.assign(box.style,{left:left+'px',top:top+'px',width:width+'px',height:height+'px'});for(const node of state.nodes){const x=node.x*state.scale+state.pan.x,y=node.y*state.scale+state.pan.y;if(x+260*state.scale>=left&&x<=left+width&&y+250*state.scale>=top&&y<=top+height)state.selected.add(node.id);else if(!event.shiftKey)state.selected.delete(node.id);const el=canvas.querySelector(`[data-node="${node.id}"]`);el.classList.toggle('selected',state.selected.has(node.id));}}};
      const end=()=>{canvas.removeEventListener('pointermove',move);canvas.removeEventListener('pointerup',end);canvas.removeEventListener('pointercancel',end);if(!moved&&state.tool==='select'){state.settings=null;state.selected.clear();}render();};
      canvas.addEventListener('pointermove',move);canvas.addEventListener('pointerup',end);canvas.addEventListener('pointercancel',end);
    });
    canvas.addEventListener('wheel',e=>{e.preventDefault();if(e.ctrlKey||e.metaKey){state.scale=Math.max(.25,Math.min(1.6,state.scale*(e.deltaY<0?1.05:.95)));}else{state.pan.x-=e.deltaX;state.pan.y-=e.deltaY;}updateWorld();},{passive:false});
  }
  document.addEventListener('click',event=>{
    const target=event.target.closest('button');if(!target)return;
    if(target.dataset.view){setView(target.dataset.view);return;}
    if(target.id==='theme'){const dark=document.documentElement.dataset.theme==='dark';document.documentElement.dataset.theme=dark?'light':'dark';target.setAttribute('aria-label',dark?'切换到深色主题':'切换到浅色主题');return;}
    if(target.dataset.mode){state.mode=target.dataset.mode;state.result=false;render();return;}
    if(target.id==='simulate'){state.result=true;render();showToast('已展示固定样例，没有调用模型。');return;}
    if(target.id==='continue-stage'){advance();return;}
    if(target.id==='send-note'){const value=document.getElementById('director-input').value.trim();if(!value)return showToast('先写下一句想法。');state.userMessage=value;render();return;}
    if(target.dataset.add){remember();const type=target.dataset.add;const canvas=document.getElementById('canvas');addNode(type,type==='text'?'新的文本节点':type==='image'?'新的图片示例':'新的视频示例',Math.max(0,(canvas.clientWidth/2-state.pan.x)/state.scale-130),Math.max(0,(canvas.clientHeight/2-state.pan.y)/state.scale-100),{content:'在对话中推进创作，也可以自由添加与整理节点。'});render();return;}
    if(target.dataset.tool){state.tool=target.dataset.tool;render();return;}
    if(target.id==='delete-nodes'){if(!state.selected.size)return;remember();state.nodes=state.nodes.filter(n=>!state.selected.has(n.id));state.selected.clear();state.settings=null;render();showToast('节点已删除，可以点击 ↶ 撤销。');return;}
    if(target.id==='undo'){const last=state.history.pop();if(last){state.nodes=JSON.parse(last);state.selected.clear();state.settings=null;render();}return;}
    if(target.id==='zoom-in'||target.id==='zoom-out'){state.scale=Math.max(.25,Math.min(1.6,state.scale+(target.id==='zoom-in'?.1:-.1)));render();return;}
    if(target.id==='fit'){fitCanvas();render();return;}
    if(target.dataset.saveNode){const id=target.dataset.saveNode;const node=state.nodes.find(n=>n.id===id);const prompt=document.querySelector(`[data-prompt="${id}"]`);if(node&&prompt){remember();node.prompt=prompt.value;state.settings=null;render();showToast('示例设置已更新，没有发起生成。');}}
  });
  document.addEventListener('keydown',event=>{if(state.view!=='director'||event.target.closest('input,textarea,select'))return;if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'){event.preventDefault();document.getElementById('undo')?.click();}else if(event.key==='Delete'||event.key==='Backspace'){event.preventDefault();document.getElementById('delete-nodes')?.click();}});
  render();
})();
