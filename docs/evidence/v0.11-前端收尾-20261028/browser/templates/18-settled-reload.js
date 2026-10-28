await expect(page.getByRole('button',{name:'重新读取创作会话',exact:true})).toBeEnabled();
const before=globalThis.dialogLog.length;
let reloadError=null;
try { await page.reload({waitUntil:'domcontentloaded',timeout:5000}); } catch(error) {reloadError=error.message;}
return {url:page.url(),reloadError,extraDialogs:globalThis.dialogLog.slice(before),dialogs:globalThis.dialogLog,state:await tabbit.observe({frames:'none',maxChars:2500})};
