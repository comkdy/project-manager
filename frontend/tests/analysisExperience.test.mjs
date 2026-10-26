import test from 'node:test';
import assert from 'node:assert/strict';
import {comparisonValues,focusCounts,focusSeries,runProject,resultTitle} from '../src/analysisExperience.ts';

test('EPM and Inline summaries remain separate and never rank incomparable units by delta',()=>{
 const run={request:{},result:{series:[{family:'epm',parameter:'Z',delta:1000},{family:'inline',operation:'OP1',parameter:'CD',delta:10},{family:'epm',parameter:'A',delta:.001}]}};
 assert.deepEqual(focusSeries(run,'epm').map(s=>s.parameter),['A','Z']);
 assert.deepEqual(focusSeries(run,'inline').map(s=>s.parameter),['CD']);
 assert.equal(run.result.series[0].parameter,'Z');
});
test('missing and nonnumeric values never appear as a comparable change; zero is valid',()=>{
 for(const mean of [null,undefined,NaN,Infinity])assert.equal(comparisonValues({group_a:{mean,valid_count:4},group_b:{mean:1,valid_count:4}}).comparable,false);
 assert.equal(comparisonValues({baseline:{mean:0,valid_count:4},current:{mean:1,valid_count:4}}).comparable,true);
 assert.equal(comparisonValues({group_a:{mean:1,valid_count:0},group_b:{mean:1,valid_count:4}}).comparable,false);
});
test('summary uses existing server judgments, without inventing a significant change',()=>{
 const groups={group_a:{mean:0,valid_count:3},group_b:{mean:100,valid_count:3}};
 assert.deepEqual(focusCounts([{...groups,status:'needs_criteria'},{...groups,status:'changed'},{status:'insufficient_data'}]),{total:3,changed:1,unconfirmed:1,insufficient:1});
});
test('confirmed threshold exceedance appears first even when its numeric delta is smaller',()=>{
 const run={result:{series:[{family:'epm',parameter:'A',status:'stable',delta:100},{family:'epm',parameter:'B',status:'changed',delta:.001}]}};
 assert.deepEqual(focusSeries(run,'epm').map(s=>s.parameter),['B','A']);
});
test('project and historical scope survive presentation and result titles use actual recipes',()=>{
 assert.equal(runProject({request:{project_id:3}}),3);
 assert.equal(runProject({request:{_project_scope:{id:7}}}),7);
 assert.equal(runProject({request:{}}),null);
 assert.equal(resultTitle({kind:'split',request:{operation:'OP1',recipe_a:'A',recipe_b:'B'}}),'OP1 · A / B');
});
