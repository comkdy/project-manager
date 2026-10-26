import test from 'node:test';
import assert from 'node:assert/strict';
import {scopedDatasets,datasetGroups} from '../src/datasetInventory.ts';

test('common and other project entries never enter a project list, even from a stale mixed response',()=>{
 const rows=[{id:'common',name:'Shared',project_id:null,kind:'source'},
  {id:'a',name:'Inline',project_id:1,kind:'source'},
  {id:'b',name:'Other',project_id:2,kind:'source'},
  {id:'calculated',name:'Inline comparison',project_id:1,kind:'derived'}];
 assert.deepEqual(scopedDatasets(rows,1).map(d=>d.id),['a','calculated']);
 assert.deepEqual(scopedDatasets(rows,null).map(d=>d.id),['common']);
 const groups=datasetGroups(scopedDatasets(rows,1),'INLINE');
 assert.deepEqual(groups.map(g=>g.datasets.map(d=>d.id)),[['a'],['calculated']]);
});

test('missing origin is never guessed from an original-looking name or id',()=>{
 const groups=datasetGroups([{id:'auto_x',name:'inline_data',project_id:1}]);
 assert.equal(groups[0].datasets.length,0);
 assert.equal(groups[1].datasets.length,1);
});
