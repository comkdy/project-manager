import assert from 'node:assert/strict';
import test from 'node:test';
import {trellisPanels,trellisColumns,trellisFigure} from '../src/plotTrellis.ts';
import {changeKind,changeDataset,cardIssue,previewQueryCard} from '../src/cardEditing.ts';

const card={id:'t',title:'Trellis',dataset_id:'data',dataset_version:1,kind:'scatter',renderer:'plotly',x:'x',y:'y',aggregate:'none',filters:[],options:{},trellis:'group'};
const columns=[{name:'x',type:'numeric'},{name:'y',type:'numeric'},{name:'group',type:'text'}];
const axis={type:'log',range:[0,3],title:{text:'Value'}};
const layout={height:450,margin:{t:6},xaxis:axis,yaxis:axis};

test('Trellis groups preserve NULL and scalar identities and adapt column count',()=>{
 assert.deepEqual(trellisPanels([{trellis:null},{trellis:'2'},{trellis:'10'},{trellis:'2'},{trellis:'(빈 값)'}]),['(빈 값)','2','10',null]);
 assert.equal(trellisColumns(3,400),1);assert.equal(trellisColumns(3,600),2);
 assert.equal(trellisColumns(3,1200),3);assert.equal(trellisColumns(3,600,1),1);
});

test('panels keep each point, shared log ranges, colors, and one legend per series',()=>{
 const traces=[{name:'Machine A',x:[1,10,100],y:[10,100,1000],customdata:['A','B','A'],marker:{color:'#123456'}},
  {name:'Machine B',x:[2,20],y:[20,200],customdata:['A','B'],marker:{color:'#abcdef'}}];
 const result=trellisFigure(traces,layout,card,['A','B'],2);
 assert.deepEqual(result.data.map(t=>t.x),[[1,100],[2],[10],[20]]);
 assert.deepEqual(result.data.map(t=>t.showlegend),[true,true,false,false]);
 assert.deepEqual(result.data.map(t=>t.marker.color),['#123456','#abcdef','#123456','#abcdef']);
 assert.equal(result.layout.xaxis2.matches,'x');assert.equal(result.layout.yaxis2.matches,'y');
 assert.equal(result.layout.xaxis2.type,'log');assert.deepEqual(result.layout.yaxis2.range,[0,3]);
 assert.ok(result.layout.xaxis.domain[1]<result.layout.xaxis2.domain[0]);
 assert.deepEqual(traces[0].x,[1,10,100]);
});

test('numeric color scales and literal panel labels remain consistent',()=>{
 const result=trellisFigure([{x:[1,2],y:[3,4],customdata:['<b>A</b>','B'],marker:{color:[1,100],showscale:true}}],layout,card,['<b>A</b>','B'],1);
 assert.equal(result.layout.annotations[0].text,'&lt;b&gt;A&lt;/b&gt;');
 assert.deepEqual(result.data.map(t=>[t.marker.cmin,t.marker.cmax,t.marker.showscale]),[[1,100,true],[1,100,false]]);
});

test('text categories and dates share a stable axis type across panels',()=>{
 const base={...layout,xaxis:{type:'-',title:{text:'Group'}}};
 const trace={x:['001','ABC'],y:[1,2],customdata:['A','B'],marker:{color:'blue'}};
 const figure=trellisFigure([trace],base,card,['A','B'],2);
 assert.equal(figure.layout.xaxis.type,'category');assert.equal(figure.layout.xaxis2.type,'category');
 assert.deepEqual(figure.layout.xaxis2.categoryarray,['001','ABC']);
 const date=trellisFigure([{...trace,x:['2026-10-01','2026-10-02']}],base,card,['A','B'],2);
 assert.equal(date.layout.xaxis2.type,'date');
});

test('Trellis persists in the query but not in unsupported charts or a missing dataset column',()=>{
 assert.equal(previewQueryCard(card).trellis,'group');
 assert.deepEqual(previewQueryCard(card),previewQueryCard({...card,options:{trellis_columns:4}}));
 assert.equal(changeKind(card,'kpi',columns).trellis,undefined);
 assert.equal(changeKind(card,'wafer',columns).trellis,'group');
 assert.equal(changeKind(card,'bar',columns).trellis,'group');
 assert.equal(changeDataset(card,{id:'other',version:2,columns:columns.slice(0,2)}).trellis,undefined);
 assert.match(cardIssue(card,columns.slice(0,2)),/group 컬럼이 없습니다/);
});

test('wafer panels split repeated coordinates, share colors and anchor equal aspect locally',()=>{
 const trace={x:[0,1,0],y:[0,1,0],customdata:[1,1,2],marker:{symbol:'square',color:[10,20,90],size:10,showscale:true}};
 const base={width:600,height:235,margin:{t:6,l:40,r:38,b:38},xaxis:{title:{text:'X'}},yaxis:{title:{text:'Y'},scaleanchor:'x',scaleratio:1}};
 const result=trellisFigure([trace],base,{...card,kind:'wafer'},[1,2],2);
 assert.deepEqual(result.data.map(t=>t.marker.color),[[10,20],[90]]);
 assert.deepEqual(result.data.map(t=>[t.marker.cmin,t.marker.cmax,t.marker.showscale]),[[10,90,true],[10,90,false]]);
 assert.ok(result.data.every(t=>t.showlegend===false));
 assert.equal(result.layout.yaxis.scaleanchor,'x');assert.equal(result.layout.yaxis2.scaleanchor,'x2');
 for(const name of ['xaxis','yaxis','xaxis2','yaxis2']){
  assert.equal(result.layout[name].matches,undefined);
  assert.deepEqual(result.layout[name].range,[-.5,1.5]);
  assert.equal(result.layout[name].constrain,'domain');
 }
 assert.equal(trace.marker.size,10);
});
