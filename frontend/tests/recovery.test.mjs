import test from 'node:test';
import assert from 'node:assert/strict';
import {RecoveryRequests,recoveryMatches} from '../src/recoveryState.ts';

test('unknown network outcome reuses request id; duplicate clicks never create a second operation',()=>{
 const state=new RecoveryRequests();const id=state.begin('create');
 assert.match(id,/^[a-f0-9]{32}$/);assert.equal(state.begin('create'),null);assert.equal(state.begin('verify:1'),null);
 state.finish(false);assert.equal(state.begin('create'),id);
 state.finish(true);assert.notEqual(state.begin('create'),id);
});
test('reverification cannot reuse an id from another backup or a failed creation',()=>{
 const state=new RecoveryRequests();const create=state.begin('create');state.finish(false);
 const first=state.begin('backup1');state.finish(false);const second=state.begin('backup2');state.finish(true);
 assert.notEqual(first,create);assert.notEqual(first,second);
 assert.equal(state.begin('backup1'),first);state.finish(true);assert.equal(state.begin('create'),create);
});
test('stale workspace responses cannot expose backup actions after switching source',()=>{
 assert.equal(recoveryMatches(undefined,'a:1'),false);
 assert.equal(recoveryMatches({workspace_token:'a:1'},'a:2'),false);
 assert.equal(recoveryMatches({workspace_token:'b:1'},'a:1'),false);
 assert.equal(recoveryMatches({workspace_token:'a:1'},'a:1'),true);
});
