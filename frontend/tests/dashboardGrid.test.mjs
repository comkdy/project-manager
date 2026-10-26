import assert from 'node:assert/strict';
import test from 'node:test';
import {snapGridStyle,gridSteps,GRID_GAP} from '../src/dashboardGrid.ts';

test('resizing stays at the current cell until halfway to the next grid line',()=>{
 const metrics={width:1188,cols:12};
 const box={left:'0px',top:'0px',width:'337px',height:'235px',position:'absolute'};
 assert.deepEqual(snapGridStyle(box,metrics),{left:0,top:0,width:288,height:201,position:'absolute'});
 assert.equal(snapGridStyle({...box,width:'339px'},metrics).width,388);
 assert.equal(snapGridStyle({...box,height:'237px'},metrics).height,272);
});

test('movement snaps during a drag and stays inside horizontal boundaries',()=>{
 const metrics={width:1188,cols:12};
 const box={left:164,top:113,width:588,height:343};
 assert.deepEqual(snapGridStyle(box,metrics),{left:200,top:142,width:588,height:343});
 assert.equal(snapGridStyle({...box,left:-90,top:-80},metrics).left,0);
 assert.equal(snapGridStyle({...box,left:-90,top:-80},metrics).top,0);
 assert.equal(snapGridStyle({...box,left:1000},metrics).left,600);
});

test('rendered sizes round trip to the persisted grid at fractional widths and every breakpoint',()=>{
 for(const metrics of [{width:599,cols:6},{width:853,cols:12},{width:1277,cols:12}]){
  const step=gridSteps(metrics);
  for(let w=3;w<=metrics.cols;w++)for(let x=0;x<=metrics.cols-w;x++){
   const box={left:Math.round(x*step.x),top:142,width:Math.round(w*step.x-GRID_GAP),height:343};
   assert.deepEqual(snapGridStyle(box,metrics),box);
  }
 }
});

test('unmeasured and percentage geometry passes through until the grid has a pixel size',()=>{
 const box={left:'0%',top:'0px',width:'50%',height:'337px'};
 assert.equal(snapGridStyle(box,{width:599,cols:6}),box);
 assert.equal(snapGridStyle({...box,left:0,width:599},{width:0,cols:6}).width,599);
});
