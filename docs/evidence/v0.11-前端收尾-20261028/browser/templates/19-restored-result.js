await expect(page.getByRole('button',{name:'重新读取创作会话',exact:true})).toBeEnabled();
await page.getByRole('button',{name:'结构模板',exact:true}).click();
const dialog=page.getByRole('dialog',{name:'结构模板',exact:true});
await expect(dialog.getByRole('textbox',{name:'模板名称',exact:true})).toHaveValue('结构收尾专用 · 双镜节奏 · 1028');
await dialog.getByRole('combobox',{name:'模板',exact:true}).selectOption(globalThis.savedTemplateResult.template_id);
await dialog.getByRole('button',{name:'查看此版本',exact:true}).click();
await expect(dialog.getByRole('button',{name:'套用此结构 · v1',exact:true})).toBeVisible();
return {url:page.url(),restoredName:await dialog.getByRole('textbox',{name:'模板名称',exact:true}).inputValue(),restoredRoles:await dialog.getByRole('textbox',{name:'镜头职能',exact:true}).evaluateAll(inputs=>inputs.map(input=>input.value)),savedTemplate:globalThis.savedTemplateResult,appliedTemplate:globalThis.appliedTemplate,dialogs:globalThis.dialogLog,mutations:globalThis.networkLog.filter(item=>!['GET','HEAD'].includes(item.method)),generationRequests:globalThis.networkLog.filter(item=>item.method==='POST'&&/generate|conversation|chat|render|films/.test(item.url))};
