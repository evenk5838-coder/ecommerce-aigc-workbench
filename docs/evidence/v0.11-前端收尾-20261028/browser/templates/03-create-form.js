await page.getByRole('button',{name:'新建创作项目',exact:true}).click();
return await tabbit.observe({frames:'none',maxChars:6500});
