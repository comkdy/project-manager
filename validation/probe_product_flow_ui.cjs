// Fresh headless test browser. Does not attach to the user's browser or profile.
const {chromium}=require('C:/Users/comkd/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1050}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(process.argv[2]??'http://127.0.0.1:8001/');
  await page.locator('.board-toolbar').waitFor();
  await page.screenshot({path:'.local/product-flow-project.png',fullPage:true});
  console.log((await page.locator('body').innerText()).slice(0,10000));
  console.log(JSON.stringify({errors}));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
