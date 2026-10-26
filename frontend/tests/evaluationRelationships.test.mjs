import test from 'node:test';
import assert from 'node:assert/strict';
import {validEvaluationClaims,reportedDisplay,relationshipLabels} from '../src/evaluationRelationships.ts';
import {validKnowledgeContent} from '../src/semanticDrafts.ts';

const claim={source_term:'A operation',target_term:'Inline A CD / alpha',relationship:'reported_influence',answer_id:'answer',quote:'A operation은 Inline A CD / alpha에 영향을 준다.',direction:null,magnitude:null};
test('one-line relationship preserves unknown direction and size during draft restore',()=>{
 assert.equal(validEvaluationClaims([claim]),true);
 assert.equal(reportedDisplay(claim.direction),'미정 · 언급 없음');
 assert.equal(reportedDisplay(claim.magnitude),'미정 · 언급 없음');
 assert.equal(relationshipLabels.reported_influence,'영향이 있다는 진술');
 assert.equal(validKnowledgeContent({name:'관계',domain:'',description:claim.quote,aliases:[],unresolved:[],references:[],details:{claims:[claim]},change_note:''}),true);
});
test('restored claims require exact endpoints and attributed direction',()=>{
 assert.equal(validEvaluationClaims([{...claim,target_term:'다른 공정 alpha'}]),false);
 assert.equal(validEvaluationClaims([{...claim,direction:{value:'증가',quote:'감소',answer_id:'reply'}}]),false);
 assert.equal(validEvaluationClaims([{...claim,magnitude:0.95}]),false);
 const direction={value:'감소',quote:'alpha가 2 nm 감소',answer_id:'reply'};
 assert.equal(validEvaluationClaims([{...claim,direction}]),true);
 assert.equal(reportedDisplay(direction),'감소');
});
