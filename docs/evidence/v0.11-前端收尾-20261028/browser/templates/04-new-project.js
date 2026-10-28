await page.getByRole('textbox',{name:'项目名称',exact:true}).fill('结构模板收尾 · 保存与离开 · 1028');
await page.getByRole('textbox',{name:'商品名称 选填，之后可以补充',exact:true}).fill('测试水杯');
await page.getByRole('button',{name:'创建并进入画布',exact:true}).click();
await expect(page.getByRole('button',{name:'返回导演台',exact:true})).toBeVisible();
globalThis.testProjectUrl=page.url();
return {projectUrl:page.url(), dialogs:globalThis.dialogLog, state:await tabbit.observe({frames:'none',maxChars:6500})};
