await page.getByRole('button',{name:'结构模板',exact:true}).click();
const dialog=page.getByRole('dialog',{name:'结构模板',exact:true});
await dialog.getByRole('combobox',{name:'模板',exact:true}).selectOption(globalThis.savedTemplateResult.template_id);
await dialog.getByRole('textbox',{name:'指定版本',exact:true}).fill('1');
await dialog.getByRole('button',{name:'查看此版本',exact:true}).click();
await expect(dialog.getByRole('button',{name:'套用此结构 · v1',exact:true})).toBeVisible();
return {text:await dialog.innerText(),checkboxes:await dialog.getByRole('checkbox').evaluateAll(inputs=>inputs.map(input=>input.closest('label')?.textContent))};
