import test from 'node:test';
import assert from 'node:assert/strict';
import {historyMatches,comparisonChangeNames} from '../src/analysisHistoryState.ts';

test('history and evidence cannot render a response from a different workspace, schedule or selected run',()=>{
 const history={workspace_token:'db:1',schedule_id:'schedule'};
 const comparison={...history,current:{id:'run'}};
 assert.equal(historyMatches(history,'db:1','schedule'),true);
 assert.equal(historyMatches(comparison,'db:1','schedule','run'),true);
 for(const data of [undefined,{...history,workspace_token:'db:2'},{...history,schedule_id:'other'}])assert.equal(historyMatches(data,'db:1','schedule'),false);
 assert.equal(historyMatches(comparison,'db:1','schedule','other'),false);
 assert.equal(historyMatches(history,'db:1','schedule','run'),false);
});
test('numeric drift, alert transitions and conditions that cannot be compared have distinct labels',()=>{
 assert.equal(new Set(Object.values(comparisonChangeNames)).size,7);
 assert.equal(comparisonChangeNames.values,'수치만 변동');
 assert.equal(comparisonChangeNames.not_comparable,'조건 확인 필요');
});
