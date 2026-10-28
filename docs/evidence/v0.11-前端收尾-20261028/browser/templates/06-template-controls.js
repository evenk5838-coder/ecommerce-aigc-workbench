const dialog=page.getByRole('dialog',{name:'结构模板',exact:true});
return {text:await dialog.innerText(),inputs:await dialog.locator('input').evaluateAll(elements=>elements.map(e=>({placeholder:e.getAttribute('placeholder'),type:e.getAttribute('type'),label:e.closest('label')?.textContent,value:e.value}))),buttons:await dialog.getByRole('button').allTextContents()};
