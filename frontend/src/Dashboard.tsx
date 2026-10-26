import {useCallback,useEffect,useMemo,useState,type CSSProperties} from 'react';
import {Alert,Button,IconButton,Menu,MenuItem,Tooltip,useMediaQuery} from '@mui/material';
import {useIsFetching,useQuery,useQueryClient} from '@tanstack/react-query';
import {Responsive,WidthProvider,type Layout} from 'react-grid-layout';
import {Plus,MoreHorizontal,GripVertical,Copy,Pencil,Trash2,Download,RefreshCw,LayoutGrid,LockKeyhole,Check,Sparkles} from 'lucide-react';
import {api,ApiError,requestChart} from './api';
import {refreshDashboardCharts} from './dashboardRefresh';
import {displayedChartVersion,receivedChartVersion,type ChartVersion} from './chartVersion';
import type {CardSpec,ChartResult,Dashboard as DashboardType,Dataset,ISOWeek,QueryContext} from './types';
import {Chart} from './Charts';
import {downloadCardImage} from './chartExport';
import CardEditor,{newCard} from './CardEditor';
import {Empty,ErrorNotice,Loading} from './components';
import {GridCard} from './GridCard';
import {GRID_ROW_HEIGHT,GRID_GAP,GRID_BREAKPOINTS,GRID_COLUMNS,COMPACT_COLUMNS,gridSteps,type GridMetrics} from './dashboardGrid';
import {DashboardPeriodPicker} from './PeriodControls';
import {prepareCardSave,cardConflictSnapshot} from './cardDraftSave';
const Grid=WidthProvider(Responsive);
export default function Dashboard({context,period,onPeriodChange,datasets,editable,onSelectCard,onNotify,proposalCard,clearProposal}:{context:QueryContext;period:ISOWeek;onPeriodChange:(period:ISOWeek)=>void;datasets:Dataset[];editable:boolean;onSelectCard:(c:CardSpec|null)=>void;onNotify:(s:string)=>void;proposalCard:CardSpec|null;clearProposal:()=>void}){
 const client=useQueryClient(),project=context.project_id??null,key=['dashboard',project],compact=useMediaQuery('(max-width:480px)');
 const dashboard=useQuery({queryKey:key,queryFn:()=>api<DashboardType>(`/dashboards${project?`?project_id=${project}`:''}`)});
 const data=dashboard.data;
 const [editing,setEditing]=useState<CardSpec|null>(null),[layoutEdit,setLayoutEdit]=useState(false),[draftLayout,setDraftLayout]=useState<Layout[]|null>(null),[error,setError]=useState<unknown>(null),[refreshing,setRefreshing]=useState(false),[busy,setBusy]=useState(false),[menu,setMenu]=useState<{element:HTMLElement;card:CardSpec}|null>(null),[selected,setSelected]=useState<string|null>(null);
 const [editingVersion,setEditingVersion]=useState<number|null>(null),[editingTarget,setEditingTarget]=useState('new');
 function editCard(card:CardSpec){if(!data)return;setEditingVersion(data.version);setEditingTarget(data.cards.some(item=>item.id===card.id)?card.id:'new');setEditing(card)}
 useEffect(()=>{setLayoutEdit(false);setDraftLayout(null);setError(null);setSelected(null)},[project]);
 useEffect(()=>{if(compact){setLayoutEdit(false);setDraftLayout(null)}},[compact]);
 useEffect(()=>{if(proposalCard)editCard(proposalCard)},[proposalCard,data?.id]);
 const [focusValue,setFocusValue]=useState(false);
 const [chartVersions,setChartVersions]=useState<Record<string,ChartVersion>>({});
 const recordChartVersion=useCallback((card:CardSpec,queryContext:QueryContext,result:ChartResult)=>{
  const next=receivedChartVersion(card,queryContext,result);
  setChartVersions(previous=>previous[card.id]?.query===next.query&&previous[card.id]?.version===next.version?previous:{...previous,[card.id]:next});
 },[]);
 const [gridMetrics,setGridMetrics]=useState<GridMetrics>({width:0,cols:12});
 const gridStep=gridSteps(gridMetrics);
 const gridStyle={'--grid-col-step':`${gridStep.x}px`,'--grid-row-step':`${gridStep.y}px`,'--grid-gap':`${GRID_GAP}px`} as CSSProperties;
 useEffect(()=>{if(selected){const card=data?.cards.find(c=>c.id===selected);if(data){onSelectCard(card??null);if(!card)setSelected(null)}}},[data?.cards,selected,onSelectCard]);
 const fetchingCharts=useIsFetching({queryKey:['chart']})>0;
 async function refreshData(){
  if(!data||refreshing||fetchingCharts)return;
  setRefreshing(true);
  try{await Promise.all([refreshDashboardCharts(client,data.cards,context,requestChart),dashboard.refetch(),client.invalidateQueries({queryKey:['versions']})])}
  catch{onNotify('일부 카드를 갱신하지 못했습니다. 잠시 후 다시 시도해 주세요.')}
  finally{setRefreshing(false)}
 }
 const layout=useMemo(()=>data?.layout.map(l=>({...l,minW:3,minH:3,maxH:30}))??[],[data?.layout]);
 const compactLayout=useMemo(()=>{
  let y=0;
  return [...(data?.layout??[])].sort((a,b)=>a.y-b.y||a.x-b.x).map(item=>{
   const card=data?.cards.find(c=>c.id===item.i),h=Math.max(item.h,card?.kind==='kpi'?4:5);
   const result={...item,x:0,y,w:1,h,minW:1,maxW:1,minH:3,maxH:30};
   y+=h;
   return result;
  });
 },[data?.layout,data?.cards]);
 async function save(cards:CardSpec[],nextLayout=data!.layout){if(!data)return;setBusy(true);setError(null);try{const result=await api<DashboardType>(`/dashboards/${data.id}`,{method:'PUT',body:JSON.stringify({version:data.version,cards,layout:nextLayout})});client.setQueryData(key,result)}catch(e){setError(e);throw e}finally{setBusy(false)}}
 async function saveCard(card:CardSpec,expectedVersion?:number){if(!data)throw new Error('보드를 불러온 뒤 저장해 주세요.');const update=prepareCardSave(data,card,expectedVersion);if(!update)throw new ApiError(409,'편집을 시작한 뒤 보드가 변경되었습니다. 최신 카드와 비교해 주세요.');await save(update.cards,update.layout);setSelected(card.id);onSelectCard(card);onNotify('시각화 카드를 저장했습니다.');clearProposal();}
 async function latestEditingCard(cardId:string){const result=await dashboard.refetch();if(result.error||!result.data)throw result.error??new Error('최신 보드를 불러오지 못했습니다.');return cardConflictSnapshot(result.data,cardId)}
 async function duplicate(card:CardSpec){const clone={...structuredClone(card),id:crypto.randomUUID(),title:`${card.title} (복사)`};try{await saveCard(clone)}catch{}}
 async function remove(card:CardSpec){if(!data)return;try{await save(data.cards.filter(c=>c.id!==card.id),data.layout.filter(l=>l.i!==card.id));onNotify('카드를 삭제했습니다.')}catch{}}
 async function download(card:CardSpec,format:'png'|'svg'){const element=menu?.element.closest<HTMLElement>('.chart-card');setMenu(null);try{if(!element)throw new Error('카드를 찾을 수 없습니다.');await downloadCardImage(element,card.title,format);onNotify(`${format.toUpperCase()} 파일을 현재 차트 모습으로 내보냈습니다.`)}catch(e){setError(e)}}
 if(dashboard.isPending)return <Loading/>;
 if(dashboard.error)return <ErrorNotice error={dashboard.error}/>;
 return <><div className="board-toolbar"><div className="board-count"><LayoutGrid size={16}/><strong>{data?.cards.length??0}개 분석 카드</strong><span className="subtle-divider"/><span>팀 공용 보드</span><span className="version-pill">v{data?.version}</span></div><div className="board-period"><DashboardPeriodPicker value={period} onChange={onPeriodChange}/></div><div className="toolbar-actions"><Tooltip title="대시보드를 보고 있는 동안 1분마다 자동으로 갱신합니다."><span className="board-auto-refresh"><span className="connection-dot"/>1분 자동 갱신</span></Tooltip><Tooltip title="지금 최신 데이터 조회"><span><IconButton size="small" disabled={refreshing||fetchingCharts} onClick={()=>void refreshData()} aria-label="데이터 새로고침"><RefreshCw size={16} className={refreshing||fetchingCharts?'refresh-spinning':undefined}/></IconButton></span></Tooltip>{editable&&<><Tooltip title={compact?'배치 편집은 넓은 화면(481px 이상)에서 사용할 수 있습니다.':'격자에 맞춰 카드를 이동하고 크기를 조절합니다.'}><span><Button size="small" color="secondary" startIcon={layoutEdit?<Check size={15}/>:<GripVertical size={15}/>} disabled={busy||compact} onClick={async()=>{if(layoutEdit&&draftLayout){try{await save(data!.cards,draftLayout.map(({i,x,y,w,h})=>({i,x,y,w,h})));setLayoutEdit(false);onNotify('카드 배치를 저장했습니다.')}catch{}}else {setDraftLayout(null);setLayoutEdit(v=>!v)}}}>{layoutEdit?'배치 저장':'배치 편집'}</Button></span></Tooltip><Button variant="contained" size="small" startIcon={<Plus size={16}/>} disabled={!datasets.length} onClick={()=>editCard(newCard(datasets.find(d=>d.spec.source_id==='wafer_summary')??datasets[0]))}>카드 만들기</Button></>}</div></div>{layoutEdit&&<Alert severity="info" sx={{mb:1}}>{gridMetrics.cols}열 격자에 맞춰 자동 정렬됩니다. 카드 상단으로 이동하고 오른쪽 아래 모서리로 크기를 조절하세요. <Button size="small" onClick={()=>{setLayoutEdit(false);setDraftLayout(null)}}>취소</Button></Alert>}<ErrorNotice error={error}/>{error&&<Button size="small" onClick={()=>{dashboard.refetch();setError(null)}}>최신 보드 불러오기</Button>}{!data?.cards.length?<Empty title="프로젝트의 첫 인사이트를 만들어보세요" description="데이터셋에서 시각화 카드를 추가하거나 AI 어시스턴트에게 물어보세요." action={editable?<Button startIcon={<Plus size={17}/>} variant="contained" onClick={()=>editCard(newCard(datasets.find(d=>d.spec.source_id==='wafer_summary')??datasets[0]))}>첫 카드 만들기</Button>:undefined}/>:<Grid key={`${compact?'compact':'standard'}-${layoutEdit?'editing':'viewing'}`} className={`dashboard-grid ${layoutEdit?'grid-editing':''}`} style={gridStyle} layouts={compact?{lg:compactLayout,md:compactLayout,sm:compactLayout,xs:compactLayout}:{lg:draftLayout??layout,md:draftLayout??layout}} breakpoints={GRID_BREAKPOINTS} cols={compact?COMPACT_COLUMNS:GRID_COLUMNS} rowHeight={GRID_ROW_HEIGHT} margin={[GRID_GAP,GRID_GAP]} useCSSTransforms={false} onWidthChange={(width,_margin,cols)=>setGridMetrics(previous=>previous.width===width&&previous.cols===cols?previous:{width,cols})} containerPadding={[0,0]} isDraggable={layoutEdit&&!compact} isResizable={layoutEdit&&!compact} draggableHandle=".card-drag-handle" onLayoutChange={next=>layoutEdit&&!compact&&setDraftLayout(next)}>{data.cards.map(card=><GridCard key={card.id} grid={gridMetrics} className={`chart-card ${layoutEdit?'layout-edit':''} ${selected===card.id?'card-selected':''}`} onClick={()=>{setSelected(card.id);onSelectCard(card)}}><div className="chart-card-heading card-drag-handle"><div className="chart-card-heading-text"><span className="card-category">{datasets.find(d=>d.id===card.dataset_id)?.name??card.dataset_id} <span>v{displayedChartVersion(card,context,chartVersions[card.id])}</span></span><h3>{card.title}</h3></div>{layoutEdit?<GripVertical size={18} className="grip"/>:<IconButton size="small" aria-label={`${card.title} 메뉴`} onClick={e=>{e.stopPropagation();setMenu({element:e.currentTarget,card})}}><MoreHorizontal size={19}/></IconButton>}</div><Chart card={card} context={context} autoRefresh onResult={result=>recordChartVersion(card,context,result)} onEditExpression={editable&&!layoutEdit?()=>{setFocusValue(true);editCard(card)}:undefined}/></GridCard>)}</Grid>}<Menu anchorEl={menu?.element} open={!!menu} onClose={()=>setMenu(null)}>{editable&&<MenuItem onClick={()=>{editCard(menu!.card);setMenu(null)}}><Pencil size={15}/>카드 편집</MenuItem>}{editable&&<MenuItem onClick={()=>{duplicate(menu!.card);setMenu(null)}}><Copy size={15}/>복제</MenuItem>}<MenuItem onClick={()=>download(menu!.card,'png')}><Download size={15}/>PNG 내보내기</MenuItem><MenuItem onClick={()=>download(menu!.card,'svg')}><Download size={15}/>SVG 내보내기</MenuItem>{editable&&<MenuItem sx={{color:'error.main'}} onClick={()=>{remove(menu!.card);setMenu(null)}}><Trash2 size={15}/>삭제</MenuItem>}</Menu>{editing&&<CardEditor key={editing.id} initial={editing} datasets={datasets} context={context} focusValue={focusValue} baseVersion={editingVersion??undefined} draftTargetId={editingTarget} onLoadLatest={latestEditingCard} onClose={()=>{setEditing(null);setFocusValue(false);clearProposal()}} onSave={saveCard}/>}<div className="board-footer"><span><LockKeyhole size={12}/>원본 데이터는 읽기 전용으로 연결되어 있습니다.</span><span><Sparkles size={12}/>PROJECT MANAGER</span></div></>
}
