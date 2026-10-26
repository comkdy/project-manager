import assert from 'node:assert/strict';
import test from 'node:test';
import {knowledgeChoicesComplete,originalKnowledgeQuestion,selectKnowledgeDefinition} from '../src/chatKnowledgeState.ts';

const choices=[{id:'yield',question:'수율 의미',options:[{item_id:'yield-a',version:2,name:'수율',domain:'검사'},{item_id:'yield-b',version:4,name:'수율',domain:'출하'}]},{id:'period',question:'기간 의미',options:[{item_id:'period-a',version:3,name:'업무일',domain:'생산'},{item_id:'period-b',version:1,name:'업무일',domain:'재무'}]}];
const knowledge={context_id:'context-1',resolution:'needs_clarification',availability:'available',clarifications:choices,selected_definitions:[{item_id:'existing',version:5}]};

test('same-named definitions keep exact identity/version and require every ambiguity group',()=>{
 let selected=selectKnowledgeDefinition(knowledge,choices[0],choices[0].options[0]);
 assert.equal(knowledgeChoicesComplete(knowledge,selected),false);
 selected=selectKnowledgeDefinition(knowledge,choices[1],choices[1].options[0],selected);
 assert.equal(knowledgeChoicesComplete(knowledge,selected),true);
 selected=selectKnowledgeDefinition(knowledge,choices[0],choices[0].options[1],selected);
 assert.deepEqual(selected,[{item_id:'existing',version:5},{item_id:'period-a',version:3},{item_id:'yield-b',version:4}]);
 assert.equal(knowledgeChoicesComplete(knowledge,[{item_id:'yield-b',version:3},{item_id:'period-a',version:3}]),false);
 assert.deepEqual(knowledge.selected_definitions,[{item_id:'existing',version:5}]);
});

test('unavailable or unoffered definitions cannot be selected for a follow-up request',()=>{
 assert.throws(()=>selectKnowledgeDefinition({...knowledge,availability:'unavailable'},choices[0],choices[0].options[0]),/다시 확인/);
 assert.throws(()=>selectKnowledgeDefinition(knowledge,choices[0],{item_id:'yield-a',version:99}),/다시 확인/);
 assert.equal(knowledgeChoicesComplete({...knowledge,availability:'unavailable'},[]),false);
});

test('incomplete relevant knowledge blocks resubmission even after every ambiguity choice is made',()=>{
 const selected=choices.map(group=>({item_id:group.options[0].item_id,version:group.options[0].version}));
 const blocked={...knowledge,blocking:true};
 assert.equal(knowledgeChoicesComplete(blocked,selected),false);
 assert.throws(()=>selectKnowledgeDefinition(blocked,choices[0],choices[0].options[0]),/다시 확인/);
 assert.equal(knowledgeChoicesComplete({...knowledge,blocking:false},selected),true);
});

test('clarification retries the original user question, never the option label or assistant prose',()=>{
 const messages=[{role:'user',text:'  Lot 수율 SQL을 만들어줘\n제외 기준 확인  '},{role:'assistant',text:'어떤 수율 정의인가요?'},{role:'user',text:'추가 질문'},{role:'assistant',text:'다음 답변'}];
 assert.equal(originalKnowledgeQuestion(messages,1),messages[0].text);
 assert.equal(originalKnowledgeQuestion(messages,3),'추가 질문');
 assert.equal(originalKnowledgeQuestion([{role:'assistant',text:'앞 메시지는 아직 불러오지 않음'}],0),null);
});
