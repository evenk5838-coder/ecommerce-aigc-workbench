const dialog=page.getByRole('dialog',{name:'结构模板',exact:true});
await expect(dialog.getByText('结构模板已保存 · v1。',{exact:true})).toBeVisible();
await expect(dialog.getByRole('button',{name:'保存结构模板',exact:true})).toBeEnabled();
return {savedTemplate:globalThis.savedTemplateResult,projectUrl:page.url(),dialogs:globalThis.dialogLog,text:await dialog.innerText(),posts:globalThis.networkLog.filter(item=>item.method==='POST')};
