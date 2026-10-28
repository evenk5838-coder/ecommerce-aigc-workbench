await page.getByRole('button',{name:'收起创作对话',exact:true}).click();
await page.getByRole('button',{name:'适配全图',exact:true}).click();
await page.getByRole('textbox',{name:'脚本内容',exact:true}).fill('环境开场展示测试水杯，接着用固定镜头观察外观细节，最后停留收尾。');
await page.getByRole('button',{name:'确认脚本并继续',exact:true}).click();
await expect(page.getByRole('button',{name:'脚本已确认',exact:true})).toBeVisible();
return {text:await page.getByRole('region',{name:'脚本设置',exact:true}).innerText(),dialogs:globalThis.dialogLog,requests:globalThis.networkLog.filter(item=>item.method==='POST')};
