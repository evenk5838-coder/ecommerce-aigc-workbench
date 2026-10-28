await page.goto('http://127.0.0.1:52663/', {waitUntil: 'domcontentloaded'});
globalThis.dialogLog = [];
globalThis.dialogMode = 'dismiss';
page.on('dialog', async dialog => { globalThis.dialogLog.push({type:dialog.type(), message:dialog.message(), action:globalThis.dialogMode}); if (globalThis.dialogMode === 'accept') await dialog.accept(); else await dialog.dismiss(); });
globalThis.networkLog = [];
page.on('response', response => { const request=response.request(); if (response.url().startsWith('http://127.0.0.1:52663/api/')) globalThis.networkLog.push({url:response.url(),method:request.method(),status:response.status()}); });
return await tabbit.observe({frames:'none', maxChars:7000});
