// Read-only navigation smoke test on the running app. Never submits an analysis.
const {chromium}=require('C:/Users/comkd/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  let submitted=0;page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/analytics/runs'))submitted++});
  await page.goto('http://127.0.0.1:8000/');await page.locator('.board-toolbar').waitFor();
  assert.equal(await page.locator('.analysis-paths').count(),0);
  await page.getByRole('navigation',{name:'주요 기능'}).getByRole('button',{name:'원인 및 영향 분석',exact:true}).click();
  await page.locator('.analysis-paths button').first().waitFor();
  assert.equal(await page.locator('.analysis-paths button').count(),2);
  await page.screenshot({path:'.local/product-flow-analysis-live.png',fullPage:true});
  await page.locator('.analysis-paths button').filter({hasText:'EPM 중심'}).click();
  await page.getByRole('heading',{name:'Inline과 Recipe에 따라 EPM이 어떻게 달라졌나요?',exact:true}).waitFor();
  await page.getByRole('navigation',{name:'주요 기능'}).getByRole('button',{name:'프로젝트',exact:true}).click();
  await page.locator('.board-toolbar').waitFor();
  assert.equal(await page.getByRole('button',{name:'데이터 관리',exact:true}).isVisible(),true);
  assert.equal(submitted,0);assert.deepEqual(errors,[]);
  const result={project_surface_preserved:true,analysis_choices_separate:true,epm_form_opens:true,new_analyses_submitted:submitted,browser_errors:errors};
  fs.writeFileSync('validation/product-flow-live-ui.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
