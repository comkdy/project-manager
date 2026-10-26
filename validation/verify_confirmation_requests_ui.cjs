// Real UI + APIs on disposable metadata only. The provider fixture is deterministic.
const {chromium}=require('C:/Users/comkd/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const base='http://127.0.0.1:8001',api=base+'/api/semantic/collection';
const initial='operation A에서 recipe1, recipe2, recipe3 평가. 확인 요청 통합 검증용 가상 사례 '+Date.now()+'.';
const full='operation A에서 recipe1, recipe2, recipe3 평가. Inline A CD alpha 증가, EPM parameter1 감소. 제품 P, 장비 E1, 2026년 9월. 평가 보고서 EV-001. 장비 영향은 분리하지 못함.';
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'}),report={},errors=[];
 const page=await browser.newPage({viewport:{width:1440,height:1050},timezoneId:'Asia/Seoul'});
 page.setDefaultTimeout(25000);page.on('pageerror',e=>errors.push(e.message));
 const postResponse=path=>page.waitForResponse(r=>r.url()===api+path&&r.request().method()==='POST');
 try{
  await page.goto(base);await page.getByRole('button',{name:'데이터 관리',exact:true}).waitFor();
  const nav=page.getByRole('navigation',{name:'주요 기능'});
  await nav.getByRole('button',{name:'AI 지식',exact:true}).click();
  assert.deepEqual(await page.getByRole('navigation',{name:'AI 지식 관리'}).locator('button').allTextContents(),['지식 목록','확인 요청']);
  await page.getByRole('tab',{name:/평가 사례/}).click();await page.getByRole('button',{name:'평가 사례 등록',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'평가 사례 등록'});
  await dialog.getByRole('combobox',{name:/관련 데이터셋/}).click();await page.getByRole('option',{name:/^검증 공통 측정 · 공통/}).click();
  await dialog.getByLabel('평가 내용과 관측 결과',{exact:false}).fill(initial);
  const receiptPromise=postResponse('/evaluations');await dialog.getByRole('button',{name:'등록하고 확인 요청에서 확인',exact:true}).click();
  const receipt=await (await receiptPromise).json();report.question_id=receipt.question_id;
  const card=page.locator(`[data-question-id="${receipt.question_id}"]`),state=card.locator(':scope > button .collection-state');
  await card.waitFor();await page.waitForFunction(id=>document.querySelector(`[data-question-id="${id}"] > button .collection-state`)?.textContent==='추가 설명 필요',receipt.question_id);
  assert.equal(await page.locator(`[data-question-id="${receipt.question_id}"]`).count(),1);
  const answerPromise=postResponse(`/questions/${receipt.question_id}/answers`);
  await card.getByLabel('추가 답변',{exact:true}).fill(full);await card.getByRole('button',{name:'추가 답변으로 초안 보완',exact:true}).click();
  assert.equal((await answerPromise).ok(),true);
  await page.waitForFunction(id=>document.querySelector(`[data-question-id="${id}"] > button .collection-state`)?.textContent==='승인 필요',receipt.question_id);
  await card.getByLabel('수정·검토 사유',{exact:true}).waitFor();
  assert.match(await card.getByLabel('Inline에서 관측한 변화',{exact:true}).inputValue(),/alpha/);
  report.same_item_answer_to_review=true;
  // Reject and answer again without moving to another screen or creating a new request.
  await card.getByLabel('수정·검토 사유',{exact:true}).fill('같은 항목의 반려·보완 흐름 검증');
  const rejectPromise=page.waitForResponse(r=>r.url().endsWith('/review')&&r.request().method()==='POST');
  await card.getByRole('button',{name:'반려 · 다시 질문',exact:true}).click();assert.equal((await rejectPromise).ok(),true);
  await card.getByLabel('업무 답변',{exact:true}).fill(full+' 추가 확인 완료.');
  const secondPromise=postResponse(`/questions/${receipt.question_id}/answers`);await card.getByRole('button',{name:'답변 보내고 초안 만들기',exact:true}).click();assert.equal((await secondPromise).ok(),true);
  await card.getByLabel('수정·검토 사유',{exact:true}).waitFor();report.rejection_and_new_answer_same_item=true;
  // A new answer from another session must not silently replace local review edits.
  await card.getByLabel('수정·검토 사유',{exact:true}).fill('보존해야 할 작성 중 검토 의견');
  const latest=await (await page.request.get(api+`/questions/${receipt.question_id}`)).json();
  const concurrent=await page.request.post(api+`/questions/${receipt.question_id}/answers`,{data:{expected_revision:latest.revision,text:full+' 다른 담당자 보완.'}});assert.equal(concurrent.ok(),true);
  await card.getByText(/작성 중인 검토 내용은 보관했으며 이전 초안의 승인은 중지/).waitFor();
  assert.equal(await card.getByLabel('수정·검토 사유',{exact:true}).inputValue(),'보존해야 할 작성 중 검토 의견');
  assert.equal(await card.getByRole('button',{name:'승인하고 지식 반영',exact:true}).isDisabled(),true);
  await card.getByRole('button',{name:'새 답변의 초안 보기',exact:true}).click();
  await card.getByLabel('수정·검토 사유',{exact:true}).fill('최신 답변과 원문 근거 확인 완료');
  await card.getByLabel('지식 이름',{exact:true}).fill('확인 요청 연결 검증 사례');
  const savePromise=page.waitForResponse(r=>/\/drafts\/[^/]+$/.test(r.url())&&r.request().method()==='POST');
  await card.getByRole('button',{name:'초안 수정 저장',exact:true}).click();assert.equal((await savePromise).ok(),true);
  await card.getByLabel('수정·검토 사유',{exact:true}).fill('수정된 이름과 최신 원문 확인 완료');
  report.concurrent_answer_preserves_review_edits=true;
  await page.screenshot({path:'.local/confirmation-requests-review.png',fullPage:true});
  const approvalPromise=page.waitForResponse(r=>r.url().endsWith('/review')&&r.request().method()==='POST');
  await card.getByRole('button',{name:'승인하고 지식 반영',exact:true}).click();const approved=await (await approvalPromise).json();assert.equal(approved.status,'approved');
  await card.getByText('검토를 거쳐 지식 저장소에 반영했습니다.',{exact:true}).waitFor();assert.equal(await state.innerText(),'반영 완료');
  report.approval_keeps_same_item_visible=true;
  await page.screenshot({path:'.local/confirmation-requests-complete.png',fullPage:true});
  // Selected deep links obey project scope, including after their approval.
  await page.getByRole('combobox',{name:/^프로젝트/}).click();await page.getByRole('option',{name:'검증 프로젝트',exact:true}).click();
  await card.waitFor({state:'hidden'});report.project_scope_preserved=true;
  await page.getByRole('combobox',{name:/^프로젝트/}).click();await page.getByRole('option',{name:'공통 요청',exact:true}).click();await card.waitFor();
  // Narrow layout, one surface, no overflow.
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),true);
  await page.screenshot({path:'.local/confirmation-requests-mobile.png',fullPage:true});
  report.mobile_no_overflow=true;report.browser_errors=errors;assert.deepEqual(errors,[]);report.finished=true;
 }catch(error){report.finished=false;report.failure=String(error);report.browser_errors=errors;await page.screenshot({path:'.local/confirmation-requests-failure.png',fullPage:true});throw error}
 finally{fs.writeFileSync('validation/confirmation-requests-ui.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
