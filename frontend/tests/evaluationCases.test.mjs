import assert from 'node:assert/strict';
import test from 'node:test';
import {evaluationSuggestions,evaluationNarrative,measurementIdentityEvidence} from '../src/evaluationCases.ts';

test('case suggestions require the exact available evidence version',()=>{
 const suggestions=[{id:'a',case_ref:{id:'case',version:1}},{id:'b',case_ref:{id:'case',version:2}},{id:'c',case_ref:{id:'other',version:2}}];
 const answer={status:'ready',cases:[{id:'case',version:2}],suggestions};
 assert.deepEqual(evaluationSuggestions(answer),[suggestions[1]]);
 assert.equal(answer.suggestions.length,3);
});

test('empty and withdrawn results never leave an actionable old suggestion',()=>{
 const answer={cases:[{id:'case',version:2}],suggestions:[{id:'a',case_ref:{id:'case',version:2}}]};
 assert.deepEqual(evaluationSuggestions({...answer,status:'unavailable'}),[]);
 assert.deepEqual(evaluationSuggestions({...answer,status:'empty'}),[]);
});

const explained=()=>({status:'ready',cases:[{id:'a',version:2,description:'과거 관측',details:{analysis_result:'Inline 상관 0.98'}}],
 narrative:{status:'ready',notice:'관측 설명',findings:[{text:'Inline과 함께 변했습니다.',citations:[{case_ref:{id:'a',version:2},field:'analysis_result',quote:'Inline 상관 0.98'}]}],next_checks:[],caveats:[]}});
test('natural explanation retains exact citations to available approved versions',()=>{
 const value=explained();assert.equal(evaluationNarrative(value),value.narrative);
});

test('saved SQL measurement identity citations render and invalidate with changed identity',()=>{
 const value=explained();
 value.cases[0].execution_evidence={run_id:'r1',measurement_items:{labels:{inline_main:'Inline oper B CD / MAIN'}}};
 const quote='원본 실행에 보존된 데이터셋 SQL의 항목 식별: inline_main = Inline oper B CD / MAIN. Inline은 operation+parameter 단위이며 같은 parameter를 다른 공정까지 일반화하지 않습니다.';
 assert.equal(measurementIdentityEvidence(value.cases[0]),quote);
 value.narrative.findings[0].citations.push({case_ref:{id:'a',version:2},field:'measurement_identity',quote});
 assert.equal(evaluationNarrative(value),value.narrative);
 value.cases[0].execution_evidence.measurement_items.labels.inline_main='Inline oper A CD / MAIN';
 assert.equal(evaluationNarrative(value),undefined);
 delete value.cases[0].execution_evidence.measurement_items;
 assert.equal(evaluationNarrative(value),undefined);
});
test('withdrawn, changed, or unsupported citations hide the whole old explanation',()=>{
 for(const modify of [v=>v.status='unavailable',v=>v.cases[0].version=3,v=>v.cases=[],v=>v.cases[0].details.analysis_result='다른 결과',v=>v.cases[0].execution_evidence={unavailable:true},v=>v.narrative.findings[0].citations=[]]){
  const value=explained();modify(value);assert.equal(evaluationNarrative(value),undefined);
 }
 assert.equal(evaluationNarrative({status:'ready',cases:[]}),undefined);
});
