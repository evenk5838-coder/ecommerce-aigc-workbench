await page.getByRole('dialog',{name:'结构模板',exact:true}).getByText('镜头时长符合当前模型要求',{exact:true}).scrollIntoViewIfNeeded();
return await page.screenshot({fullPage:false});
