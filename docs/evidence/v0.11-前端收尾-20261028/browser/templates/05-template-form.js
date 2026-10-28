await page.getByRole('button',{name:'结构模板',exact:true}).click();
return await tabbit.observe({frames:'none',maxChars:7000});
