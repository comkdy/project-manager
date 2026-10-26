// A disposable local QA app only; never accesses the live app on port 8000.
const {chromium}=require('C:/Users/comkd/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const base='http://127.0.0.1:8001';
const sourceFixture=JSON.parse(fs.readFileSync('.local/evaluation-qa-wide.json','utf8'));
const fixture={dataset:sourceFixture.name,group:sourceFixture.group_column,metrics:sourceFixture.metrics,baseline:sourceFixture.baseline,comparison:sourceFixture.comparison};
const narrative='operation A에서 recipe1, recipe2, recipe3 평가. Inline A CD alpha 증가, EPM parameter1 감소. 평가 보고서 EV-001. 장비 영향은 분리하지 못함. 제품 P, 장비 E1, 2026년 9월';
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'}),report={},errors=[];let page;
 try{
  page=await browser.newPage({viewport:{width:1440,height:1050},timezoneId:'Asia/Seoul'});
  page.setDefaultTimeout(30000);page.on('pageerror',error=>errors.push(error.message));
  const nav=()=>page.getByRole('navigation',{name:'주요 기능'});
  const registered=()=>page.getByRole('region',{name:'등록한 평가 사례'});
  const panel=()=>page.getByRole('region',{name:'평가 사례 기반 현재 데이터 분석'});
  const response=(suffix)=>page.waitForResponse(r=>r.url().includes('/api/'+suffix)&&r.request().method()==='POST');
  async function approve(note){
   await registered().getByLabel('수정·검토 사유',{exact:true}).waitFor({timeout:90000});
   await page.getByLabel('수정·검토 사유',{exact:true}).fill(note);
   const done=page.waitForResponse(r=>/\/api\/semantic\/collection\/drafts\/[^/]+\/review$/.test(r.url())&&r.request().method()==='POST');
   await page.getByRole('button',{name:'승인하고 지식 반영',exact:true}).click();
   const result=await done;assert.equal(result.ok(),true);return result.json();
  }
  async function select(control,name){await control.click();await page.getByRole('option',{name,exact:true}).click();}
  async function metrics(names){
   await panel().getByRole('combobox',{name:/^측정 항목(?:\s|$)/}).click();
   for(const name of names)await page.getByRole('option',{name,exact:true}).click();
   await page.keyboard.press('Escape');
  }
  async function prepare(){
   const prepared=response('evaluation-analysis/prepare');
   await page.getByRole('button',{name:'현재 데이터로 분석',exact:true}).first().click();
   const result=await prepared;assert.equal(result.ok(),true);const run=await result.json();report.last_prepare={id:run.id,status:run.status,project_id:run.project_id};
   await panel().getByRole('combobox',{name:'확인할 내용',exact:false}).waitFor();return run;
  }
  async function execute(run){
   const executed=response(`evaluation-analysis/${run.id}/execute`);
   await panel().getByRole('button',{name:'현재 데이터로 분석 실행',exact:true}).click();
   assert.equal((await executed).ok(),true);
   await panel().getByRole('region',{name:'현재 데이터 계산 결과',exact:true}).waitFor({timeout:90000});
   const current=await page.request.get(base+`/api/evaluation-analysis/${run.id}${run.project_id==null?'':`?project_id=${run.project_id}`}`,{headers:{'X-Workspace-Token':run.workspace_token}});
   assert.equal(current.ok(),true);return current.json();
  }
  await page.goto(base);await page.getByRole('button',{name:'데이터 관리',exact:true}).waitFor();
  assert.deepEqual(await nav().locator('button').allTextContents(),['프로젝트','AI 지식']);report.primary_navigation_simplified=true;
  await nav().getByRole('button',{name:'AI 지식',exact:true}).click();await page.getByRole('tab',{name:/평가 사례/}).click();
  await page.getByRole('button',{name:'평가 사례 등록',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'평가 사례 등록'});
  await dialog.getByRole('combobox',{name:'관련 데이터셋',exact:false}).click();await page.getByRole('option',{name:new RegExp('^'+fixture.dataset+' ·')}).click();
  await dialog.getByLabel('평가 내용과 관측 결과',{exact:false}).fill(narrative);
  const registeredResponse=response('semantic/collection/evaluations');await dialog.getByRole('button',{name:'등록하고 확인 요청에서 확인',exact:true}).click();
  assert.equal((await registeredResponse).ok(),true);await registered().locator('.collection-question-card').waitFor({timeout:90000});
  const alreadyApproved=registered().getByText('검토를 거쳐 지식 저장소에 반영했습니다.',{exact:true});
  await registered().getByLabel('수정·검토 사유',{exact:true}).or(alreadyApproved).waitFor({timeout:90000});
  if(await alreadyApproved.isVisible())report.existing_source_case=true;else{const source=await approve('평가 원문과 조건을 확인했습니다. 보고된 관측만 참고합니다.');report.source_item_id=source.item_id;}

  await nav().getByRole('button',{name:'프로젝트',exact:true}).click();
  await page.getByRole('navigation',{name:'프로젝트 선택'}).getByRole('button',{name:/검증 프로젝트/}).click();
  await page.getByRole('button',{name:'검증 평균 메뉴',exact:true}).click();await page.getByRole('menuitem',{name:'카드 편집',exact:true}).click();
  await page.getByRole('heading',{name:'카드 구성',exact:true}).waitFor();await page.getByRole('button',{name:'취소',exact:true}).click();
  await page.getByRole('button',{name:'데이터 관리',exact:true}).click();
  assert.equal(await page.getByRole('button',{name:'데이터셋 추가',exact:true}).isVisible(),true);
  await page.getByRole('button',{name:'쿼리 관리',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'닫기',exact:true}).click();
  await page.getByRole('navigation',{name:'프로젝트 선택'}).getByRole('button',{name:'전체 프로젝트',exact:true}).click();
  // Keep the independently reconciled full fixture range explicit through the existing period UI.
  await page.getByRole('button',{name:'대시보드',exact:true}).click();await page.getByRole('button',{name:/조회 기간 설정:/}).click();
  const period=page.getByRole('dialog',{name:'조회 기간 설정'});await period.getByLabel('조회 시작 연도',{exact:true}).fill('2020');await period.getByLabel('조회 시작 연도',{exact:true}).press('Tab');
  await period.getByRole('combobox').click();await page.getByRole('option',{name:'WW01',exact:true}).click();await period.getByRole('button',{name:'적용',exact:true}).click();
  await page.getByRole('button',{name:'데이터 관리',exact:true}).click();
  await page.locator('.dataset-item').filter({hasText:fixture.dataset}).click();report.project_dataset_query_card_preserved=true;
  await page.locator('.assistant-fab').click();
  await page.locator('.chat-compose textarea:not([aria-hidden="true"])').fill('현재 데이터와 관련된 평가 사례를 찾아 추가 분석을 제안해줘');
  await page.getByRole('button',{name:'메시지 보내기',exact:true}).click();
  await page.getByRole('region',{name:'관련 평가 사례와 추가 분석 제안'}).waitFor({timeout:90000});
  const run=await prepare();report.run_id=run.id;
  assert.equal(run.status,'needs_input');assert.match(await panel().innerText(),new RegExp(fixture.dataset));
  await select(panel().getByRole('combobox',{name:'그룹을 나눌 항목',exact:false}),fixture.group);await metrics(fixture.metrics);
  await panel().getByText('두 그룹의 평균 차이도 계산 (선택)',{exact:true}).click();
  await panel().getByLabel('기준 그룹 값',{exact:true}).fill(fixture.baseline);await panel().getByLabel('비교 그룹 값',{exact:true}).fill(fixture.comparison);
  const computed=await execute(run);assert.equal(computed.status,'completed');assert.equal(computed.result.knowledge_eligible,true);assert.equal(computed.result.method,'group_compare');
  assert.ok(computed.result.groups.length>=2);assert.ok(computed.result.deltas.length>=1);
  for(const expected of sourceFixture.expected){
   const group=computed.result.groups.find(row=>row.value===expected.lot_cd);assert.ok(group);assert.equal(group.row_count,expected.rows);
   for(const [column,suffix] of [['epm_a','a'],['epm_b','b']]){const metric=group.metrics.find(row=>row.column===column);assert.equal(metric.valid_count,expected['n_'+suffix]);assert.ok(Math.abs(metric.mean-expected['mean_'+suffix])<1e-10,`${column} mean matches independent SQL`)}
  }
  report.independent_source_sql_reconciled=true;
  const resultText=await panel().getByRole('region',{name:'현재 데이터 계산 결과',exact:true}).innerText();
  for(const group of computed.result.groups)for(const metric of group.metrics)if(metric.mean!=null)assert.ok(resultText.includes(metric.mean.toLocaleString('ko-KR',{maximumSignificantDigits:7})));
  await page.screenshot({path:'.local/evaluation-execution-result.png',fullPage:true});report.real_data_group_summary_and_delta=true;
  await panel().getByLabel('검토할 메모 (선택)',{exact:true}).fill('선택한 그룹과 측정 단위를 확인했습니다. 현재 범위의 관측 결과로 검토합니다.');
  const submitted=response(`evaluation-analysis/${run.id}/submit-review`);await panel().getByRole('button',{name:'AI 지식 검토 요청',exact:true}).click();
  const reviewed=await submitted;assert.equal(reviewed.ok(),true);const review=await reviewed.json();report.review_question_id=review.question_id;
  await registered().getByText('현재 데이터에서 계산한 결과',{exact:true}).waitFor();
  assert.match(await registered().innerText(),/계산|조회/);
  await registered().getByLabel('수정·검토 사유',{exact:true}).click();
  assert.equal(await page.getByLabel('현재 데이터 분석 결과',{exact:true}).isDisabled(),true);
  assert.match(await page.getByLabel('현재 데이터 분석 결과',{exact:true}).inputValue(),/./);
  await page.getByRole('button',{name:'확인 요청',exact:true}).click();
  const approved=await approve('계산 결과, 표본 수와 범위를 확인했습니다. 원인 확정 없이 관측 결과로 승인합니다.');
  assert.equal(approved.status,'approved');report.result_item_id=approved.item_id;report.computed_evidence_review_and_approval=true;
  await page.getByRole('button',{name:'지식 목록에서 확인',exact:true}).click();
  await page.locator('.knowledge-list-item').filter({hasText:/현재 데이터|분석 결과/}).first().waitFor();

  await nav().getByRole('button',{name:'프로젝트',exact:true}).click();await page.locator('.assistant-fab').click();
  if(await panel().isVisible())await panel().getByRole('button',{name:'채팅으로',exact:true}).click();
  await page.getByRole('button',{name:'이전 대화',exact:true}).click();await page.locator('.chat-history-open').filter({hasText:/평가 사례/}).first().click();
  await page.getByRole('button',{name:'현재 데이터 분석 열기',exact:true}).first().waitFor();await page.getByRole('button',{name:'현재 데이터 분석 열기',exact:true}).first().click();
  await panel().getByText('검토를 거쳐 AI 지식에 반영했습니다.',{exact:true}).waitFor();report.saved_chat_reopens_approved_execution=true;
  await panel().getByRole('button',{name:'채팅으로',exact:true}).click();
  const correlation=await prepare();
  await select(panel().getByRole('combobox',{name:'확인할 내용',exact:false}),'두 측정 항목이 함께 변하는 관계');await metrics(fixture.metrics);
  const correlated=await execute(correlation);assert.equal(correlated.result.method,'correlation');assert.ok(correlated.result.correlation.pair_count>1);
  assert.equal(typeof correlated.result.correlation.pearson,'number');assert.match(await panel().innerText(),/Pearson 상관계수/);report.real_data_correlation=true;
  await page.screenshot({path:'.local/evaluation-execution-correlation.png',fullPage:true});
  await panel().getByRole('button',{name:'채팅으로',exact:true}).click();
  const failing=await prepare();await select(panel().getByRole('combobox',{name:'그룹을 나눌 항목',exact:false}),fixture.group);await metrics([fixture.metrics[0]]);
  await panel().getByText('두 그룹의 평균 차이도 계산 (선택)',{exact:true}).click();await panel().getByLabel('기준 그룹 값',{exact:true}).fill('__missing_qa_group__');await panel().getByLabel('비교 그룹 값',{exact:true}).fill(fixture.comparison);
  const failedExecution=response(`evaluation-analysis/${failing.id}/execute`);await panel().getByRole('button',{name:'현재 데이터로 분석 실행',exact:true}).click();assert.equal((await failedExecution).ok(),true);
  await panel().getByText('분석 실패',{exact:true}).waitFor({timeout:90000});await panel().getByRole('button',{name:'다시 시도',exact:true}).click();
  const retried=response('workspace/activity/actions');await page.getByRole('dialog').getByRole('button',{name:'재시도 시작',exact:true}).click();
  const retriedResponse=await retried;assert.equal(retriedResponse.ok(),true);const child=await retriedResponse.json();assert.notEqual(child.target.id,failing.id);
  await page.waitForResponse(r=>r.url().includes(`/api/evaluation-analysis/${child.target.id}`)&&r.request().method()==='GET');await panel().getByText('분석 실패',{exact:true}).waitFor({timeout:90000});
  report.retry_preserves_original_and_opens_child=true;report.retry_run_id=child.target.id;
  await panel().getByRole('button',{name:'채팅으로',exact:true}).click();
  const previousAnswers=await page.locator('.chat-message.assistant').count();
  await page.locator('.chat-compose textarea:not([aria-hidden="true"])').fill('현재 데이터와 관련된 평가 사례를 다시 찾아줘');
  await page.getByRole('button',{name:'메시지 보내기',exact:true}).click();
  await page.waitForFunction(count=>document.querySelectorAll('.chat-message.assistant').length>count,previousAnswers);
  const currentCase=page.locator('.chat-message.assistant').last().locator('.chat-evaluation-case').filter({hasText:'승인된 분석 결과'}).first();await currentCase.waitFor({timeout:90000});
  await currentCase.getByText('평가 조건과 근거 보기',{exact:true}).click();await currentCase.getByText('승인된 계산 결과',{exact:true}).waitFor();await currentCase.getByRole('button',{name:'근거 분석 열기',exact:true}).click();
  await panel().getByRole('region',{name:'현재 데이터 계산 결과',exact:true}).waitFor();report.approved_execution_returns_as_related_knowledge=true;
  report.approval_return_auto_refresh=report.saved_chat_reopens_approved_execution;
  report.history_restored=report.saved_chat_reopens_approved_execution;
  report.approved_result_retrieved=report.approved_execution_returns_as_related_knowledge;
  assert.deepEqual(errors,[]);report.browser_errors=errors;report.finished=true;
  fs.writeFileSync('validation/evaluation-execution-ui.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }catch(error){report.finished=false;report.browser_errors=errors;report.failure=String(error);if(page){report.visible_text=(await page.locator('body').innerText()).slice(-16000);await page.screenshot({path:'.local/evaluation-execution-failure.png',fullPage:true})}fs.writeFileSync('validation/evaluation-execution-ui.json',JSON.stringify(report,null,2));throw error}
 finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
