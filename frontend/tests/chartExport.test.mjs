import test from 'node:test';
import assert from 'node:assert/strict';
import {livePlotSvg} from '../src/chartExport.ts';

test('export uses the live graph with its zoom, visibility and exact panel dimensions',async()=>{
 const plot={dataset:{exportReady:'true'},isConnected:true,clientWidth:613,clientHeight:805,
  layout:{xaxis:{range:[2,7]},yaxis:{type:'log'},annotations:[{text:'Wafer 1'},{text:'Wafer 2'}]},
  data:[{visible:'legendonly'},{visible:true}]};
 const before=structuredClone(plot);
 const source='<svg xmlns="http://www.w3.org/2000/svg"><text>수율</text></svg>';
 let called=0;
 const output=await livePlotSvg(plot,{toImage:async(element,options)=>{
  called++;assert.equal(element,plot);
  assert.deepEqual(options,{format:'svg',width:613,height:805});
  return `data:image/svg+xml,${encodeURIComponent(source)}`;
 }});
 assert.equal(output,source);assert.equal(called,1);assert.deepEqual(plot,before);
});

test('unrendered, detached or hidden charts cannot fall back to a different rendering',async()=>{
 const ready={dataset:{exportReady:'true'},isConnected:true,clientWidth:600,clientHeight:300};
 for(const patch of [{dataset:{exportReady:'false'}},{isConnected:false},{clientWidth:0}]){
  await assert.rejects(livePlotSvg({...ready,...patch},{toImage:()=>assert.fail('must not export')}),/차트가 표시된 후/);
 }
});

test('base64 SVG snapshots preserve Korean text',async()=>{
 const source='<svg><text>랏코드</text></svg>';
 const result=await livePlotSvg({dataset:{exportReady:'true'},isConnected:true,clientWidth:300,clientHeight:200},
  {toImage:async()=>`data:image/svg+xml;base64,${Buffer.from(source).toString('base64')}`});
 assert.equal(result,source);
});
