import assert from 'node:assert/strict';
import test from 'node:test';
import {draftReviewChanged,draftReviewReducer,initialDraftReview} from '../src/draftReviewState.ts';

const draft=revision=>({id:'draft-1',revision,proposal:{kind:'term',scope:'database',content:{name:`Server revision ${revision}`,description:'Server definition',unresolved:[]}}});
const receive=(state,value)=>draftReviewReducer(state,{type:'receive',draft:value});

test('remote polling preserves unsaved text, review note and the original save revision',()=>{
 let state=initialDraftReview(draft(1));
 const localProposal={...state.proposal,content:{...state.proposal.content,description:'Unsubmitted review'}};
 state=draftReviewReducer(state,{type:'edit',value:{proposal:localProposal,unresolved:'Need confirmation\n',note:'My evidence',target:'existing-item',dirty:true}});
 state=receive(state,draft(2));
 state=receive(state,draft(3));
 assert.equal(state.proposal.content.description,'Unsubmitted review');
 assert.equal(state.unresolved,'Need confirmation\n');
 assert.equal(state.note,'My evidence');
 assert.equal(state.target,'existing-item');
 assert.equal(state.dirty,true);
 assert.equal(state.base.revision,1,'a save must not silently adopt another reviewer’s revision');
 assert.equal(state.latest.revision,3);
 assert.equal(draftReviewChanged(state),true);
});

test('explicit reload replaces the draft only after the reviewer chooses to discard their input',()=>{
 let state=initialDraftReview(draft(1));
 state=draftReviewReducer(state,{type:'edit',value:{note:'My note',dirty:true}});
 state=receive(state,draft(2));
 state=draftReviewReducer(state,{type:'reload'});
 assert.equal(state.base.revision,2);
 assert.equal(state.proposal.content.name,'Server revision 2');
 assert.equal(state.note,'');
 assert.equal(state.dirty,false);
 assert.equal(draftReviewChanged(state),false);
});

test('our successful save advances the baseline and an older in-flight poll cannot roll it back',()=>{
 let state=initialDraftReview(draft(1));
 state=draftReviewReducer(state,{type:'saved',draft:draft(2)});
 state=receive(state,draft(1));
 assert.equal(state.base.revision,2);
 assert.equal(state.latest.revision,2);
 assert.equal(draftReviewChanged(state),false);
 state=receive(state,draft(3));
 assert.equal(draftReviewChanged(state),true);
});

test('polling the same revision does not erase a review note even before proposal edits',()=>{
 let state=initialDraftReview(draft(1));
 state=draftReviewReducer(state,{type:'edit',value:{note:'Reviewed with process owner'}});
 state=receive(state,draft(1));
 assert.equal(state.note,'Reviewed with process owner');
 assert.equal(draftReviewChanged(state),false);
});

test('a save response cannot hide a newer remote revision already received while saving',()=>{
 let state=initialDraftReview(draft(1));
 state=receive(state,draft(3));
 state=draftReviewReducer(state,{type:'saved',draft:draft(2)});
 assert.equal(state.base.revision,2);
 assert.equal(state.latest.revision,3);
 assert.equal(draftReviewChanged(state),true);
});
