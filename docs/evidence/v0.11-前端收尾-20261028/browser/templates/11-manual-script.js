await page.getByRole('button',{name:'打开项目：结构模板收尾 · 保存与离开 · 1028',exact:true}).click();
await page.getByRole('button',{name:'手写脚本',exact:true}).click();
return await tabbit.observe({frames:'none',maxChars:11000});
