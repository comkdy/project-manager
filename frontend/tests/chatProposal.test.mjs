import assert from 'node:assert/strict';
import test from 'node:test';
import {historyContent,proposalDefinition,proposalSqlSpec,proposalTitle} from '../src/chatProposal.ts';

const p={kind:'card',explanation:'Lot 평균',dataset:{name:'평균',spec:{source_id:'inline',raw_sql:'-- exact\nselect lot_code from public.inline_measurements;'}},card:{id:'draft',dataset_id:'draft-dataset'},query_result:{rows:[{sensitive:'do not send'}]},workspace_token:'workspace',proposal_id:'id'};
test('bundle retains both artifacts but never sends result rows to save or model history',()=>{
 const request=proposalDefinition(p);
 assert.equal(request.dataset.spec.raw_sql,p.dataset.spec.raw_sql);
 assert.deepEqual(request.card,p.card);
 assert.equal('query_result' in request,false);
 assert.equal('workspace_token' in request,false);
 const history=historyContent('결과',p);
 assert.ok(history.includes('select lot_code'));
 assert.ok(!history.includes('sensitive'));
 assert.equal(proposalTitle(p),'데이터셋 + 카드 제안');
});
test('long history is bounded to API message limit',()=>{
 assert.equal(historyContent('x'.repeat(8000),p).length,6000);
});
test('preview and apply keep the server knowledge context ID without copying evidence content or rows',()=>{
 const proposed={...p,knowledge_context_id:'reviewed-context',knowledge:{sources:[{name:'provided definition'}],warnings:['review required']}};
 const request=proposalDefinition(proposed);
 assert.equal(request.knowledge_context_id,'reviewed-context');
 assert.equal('knowledge' in request,false);
 assert.equal('query_result' in request,false);
 assert.equal(proposalDefinition({...proposed,dataset:{...proposed.dataset,name:'edited'}}).knowledge_context_id,'reviewed-context');
});
test('pivot metadata follows save and follow-up history but detaches on a handwritten SQL edit',()=>{
 const spec={source_id:'inline',raw_sql:'SELECT generated_pivot',pivot:{keys:['wafer_id'],outputs:[{value:'CD',name:'cd'}]}};
 const draft={...p,dataset:{name:'피벗',spec}};
 assert.deepEqual(proposalDefinition(draft).dataset.spec.pivot,spec.pivot);
 assert.ok(historyContent('피벗',draft).includes('"pivot"'));
 assert.deepEqual(proposalSqlSpec(spec,spec.raw_sql).pivot,spec.pivot);
 const edited=proposalSqlSpec(spec,'SELECT handwritten_join');
 assert.equal(edited.pivot,null);
 assert.equal(edited.raw_sql,'SELECT handwritten_join');
 assert.ok(spec.pivot); // Editing the review must not mutate the chat history.
});
