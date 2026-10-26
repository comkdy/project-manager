import assert from 'node:assert/strict';
import test from 'node:test';
import {confirmationStep,confirmationMatches,confirmationInProject} from '../src/confirmationRequests.ts';

const q={id:'request',status:'open',dataset:{project_id:3},dataset_changed:false,followups:[],answers:[]};
test('one request advances from explanation through approval to completion',()=>{
 assert.equal(confirmationStep(q),'answer');
 assert.equal(confirmationStep({...q,status:'structuring'}),'processing');
 assert.equal(confirmationStep({...q,status:'review',followups:['관측 내용 필요']}),'answer');
 assert.equal(confirmationStep({...q,status:'review'}),'approval');
 assert.equal(confirmationStep({...q,status:'answered'}),'completed');
});
test('changed datasets and failed extraction are not shown as ready for approval',()=>{
 assert.equal(confirmationStep({...q,status:'review',dataset_changed:true}),'attention');
 assert.equal(confirmationStep({...q,answers:[{status:'error'}]}),'attention');
 assert.equal(confirmationStep({...q,status:'structuring',answers:[{status:'error'}]}),'processing');
});
test('an opened request stays visible when an answer or approval changes its stage',()=>{
 const done={...q,status:'answered'};
 assert.equal(confirmationMatches(done,'pending'),false);
 assert.equal(confirmationMatches(done,'pending',true),true);
 assert.equal(confirmationMatches({...q,status:'review'},'answer',true),true);
 assert.equal(confirmationMatches({...q,status:'review'},'answer'),false);
});
test('deferred deadlines and project scope remain distinct from the action filter',()=>{
 const deferred={...q,status:'deferred',deferred_until:'2026-10-25T00:00:00Z'};
 assert.equal(confirmationMatches(deferred,'pending',false,Date.parse('2026-10-24T00:00:00Z')),false);
 assert.equal(confirmationMatches(deferred,'pending',false,Date.parse('2026-10-26T00:00:00Z')),true);
 assert.equal(confirmationInProject(q,'all'),true);
 assert.equal(confirmationInProject(q,'3'),true);
 assert.equal(confirmationInProject(q,'4'),false);
 assert.equal(confirmationInProject(q,'shared'),false);
 assert.equal(confirmationInProject({...q,dataset:{project_id:null}},'shared'),true);
});
