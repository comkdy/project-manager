import test from 'node:test';
import assert from 'node:assert/strict';
import {operationActions,operationCounts,operationsExpired,operationsMatches} from '../src/operationsState.ts';
test('diagnostics from another source or revision cannot render',()=>{
 assert.equal(operationsMatches(undefined,'a:1'),false);
 assert.equal(operationsMatches({workspace_token:'a:0'},'a:1'),false);
 assert.equal(operationsMatches({workspace_token:'b:1'},'a:1'),false);
 assert.equal(operationsMatches({workspace_token:'a:1'},'a:1'),true);
});
test('a manual result becomes old after a minute and invalid timestamps are not current',()=>{
 const now=Date.parse('2026-10-24T03:00:00Z');
 assert.equal(operationsExpired(new Date(now).toISOString(),now),false);
 assert.equal(operationsExpired(new Date(now-61000).toISOString(),now),true);
 assert.equal(operationsExpired(new Date(now+6000).toISOString(),now),true);
 assert.equal(operationsExpired('invalid',now),true);
});
test('unknown and informational checks are never counted as verified',()=>{
 assert.deepEqual(operationCounts(['ok','attention','unknown','info','unknown'].map(status=>({status}))),{ok:1,attention:1,unknown:2,info:1});
 assert.deepEqual(operationCounts([]),{ok:0,attention:0,unknown:0,info:0});
 assert.equal(Object.keys(operationActions).length,6);
});
