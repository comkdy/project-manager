import {useEffect,useMemo,useState} from 'react';
import {Alert,Button,TextField} from '@mui/material';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {ArrowLeft,ArrowRight,Braces,ChevronDown,Database,Plus,Search,Table2} from 'lucide-react';
import {api,post} from './api';
import type {Bootstrap,Dataset,Proposal,QueryContext,QueryResult,Source} from './types';
import {DataGrid,Empty,ErrorNotice} from './components';
import DatasetEditor from './DatasetEditor';
import DatasetScopeBadge,{DatasetKindBadge} from './DatasetScopeBadge';
import {datasetGroups,scopedDatasets} from './datasetInventory';
import {sortTableRows} from './tableRows';
export {default as DatasetEditor} from './DatasetEditor';

const TABLE_PAGE_SIZE=100;

export default function DataManager({datasets,catalog,context,projectName,editable,onSelectDataset,onNotify,proposal,clearProposal}:{datasets:Dataset[];catalog:Source[];context:QueryContext;projectName?:string;editable:boolean;onSelectDataset:(id:string)=>void;onNotify:(s:string)=>void;proposal:Proposal['dataset']|null;clearProposal:()=>void}){
 const client=useQueryClient(),projectId=context.project_id??null;
 datasets=scopedDatasets(datasets,projectId);
 const [draft,setDraft]=useState<Dataset|null>(null);
 const [sourceCatalog,setSourceCatalog]=useState<Source[]>(catalog),[discovering,setDiscovering]=useState(false),[sourceError,setSourceError]=useState<unknown>(null);
 function sourceNames(d?:Dataset){
  if(!d)return '';
  return d.source_tables?.join(' · ')??'원천 테이블 확인 필요';
 }
 async function addDataset(){
  setDiscovering(true);setSourceError(null);
  try{
   const sources=await post<Source[]>(`/sources/discover${projectId!==null?`?project_id=${projectId}`:''}`,{});
   setSourceCatalog(sources);
   client.setQueryData<Bootstrap>(['bootstrap',projectId],old=>old?{...old,catalog:sources}:old);
   if(!sources.length){setSourceError(new Error('연결된 DB에 조회 가능한 원천 테이블이 없습니다.'));return}
   setDraft({id:'new-dataset',name:'',version:1,project_id:projectId,columns:[],spec:{source_id:sources[0].id,columns:[],filters:[],joins:[],raw_sql:sources[0].default_sql}});
  }catch(error){setSourceError(error)}finally{setDiscovering(false)}
 }
 useEffect(()=>setSourceCatalog(catalog),[catalog]);
 const [datasetId,setDatasetId]=useState(datasets[0]?.id??''),[version,setVersion]=useState(datasets[0]?.version??1),[cursor,setCursor]=useState<string|null>(null),[cursors,setCursors]=useState<(string|null)[]>([]),[sort,setSort]=useState(''),[direction,setDirection]=useState<'asc'|'desc'>('asc'),[edit,setEdit]=useState(false),[search,setSearch]=useState('');
 const visible=datasets.find(d=>d.id===datasetId);
 const versions=useQuery({queryKey:['versions',projectId,datasetId],queryFn:()=>api<Dataset[]>(`/datasets/${datasetId}/versions${projectId!==null?`?project_id=${projectId}`:''}`),enabled:!!visible});
 const dataset=versions.data?.find(d=>d.version===version)??visible;
 useEffect(()=>{setCursor(null);setCursors([])},[context]);
 useEffect(()=>{if(!visible&&datasets.length)select(datasets[0])},[visible,datasets]);
 useEffect(()=>{
  if(!proposal)return;
  const target=(datasets.find(d=>d.id===datasetId&&d.spec.source_id===proposal.spec.source_id)??datasets.find(d=>d.spec.source_id===proposal.spec.source_id));
  if(target){select(target);setEdit(true)}
 },[proposal]);
 useEffect(()=>{if(datasetId)onSelectDataset(datasetId)},[datasetId]);
 const query=useQuery({queryKey:['table',projectId,datasetId,version,context,cursor,TABLE_PAGE_SIZE],queryFn:()=>post<QueryResult>('/query',{dataset_id:datasetId,dataset_version:version,context:{...context,limit:TABLE_PAGE_SIZE,cursor,sort:undefined,direction:'asc'}}),enabled:!!visible});
 const rows=useMemo(()=>sortTableRows(query.data?.rows??[],query.data?.columns??[],sort,direction),[query.data,sort,direction]);
 function select(d:Dataset){setDatasetId(d.id);setVersion(d.version);setCursor(null);setCursors([]);setSort('');setDirection('asc')}
 function sortColumn(column:string){
  if(sort===column&&direction==='desc'){setSort('');setDirection('asc')}
  else{setDirection(sort===column?'desc':'asc');setSort(column)}
 }
 function selectVersion(next:number){setVersion(next);setCursor(null);setCursors([]);setSort('');setDirection('asc');clearProposal()}
 async function saved(result:Dataset){
  client.setQueryData<Bootstrap>(['bootstrap',projectId],old=>old?{...old,datasets:[...old.datasets.filter(d=>d.id!==result.id),result]}:old);
  client.setQueryData<Dataset[]>(['versions',projectId,result.id],old=>[...(old??[]).filter(d=>d.version!==result.version),result].sort((a,b)=>b.version-a.version));
  select(result);
  await Promise.all([
   client.invalidateQueries({queryKey:['bootstrap']}),
   client.invalidateQueries({queryKey:['versions',projectId,result.id]}),
   client.invalidateQueries({queryKey:['table',projectId,result.id]}),
   client.invalidateQueries({queryKey:['dashboard']}),
   client.invalidateQueries({queryKey:['chart']}),
   client.invalidateQueries({queryKey:['period-columns',result.id]}),
  ]);
  onNotify(`${result.project_id===null?'공통':'프로젝트'} 데이터셋 v${result.version}을 저장했습니다. 카드에도 최신 버전을 적용합니다.`);
 }
 const ignored=query.data?.meta.ignored_filters as string[]|undefined;
 return <div className="data-workspace">
  <aside className="dataset-sidebar">
   <div className="dataset-side-title"><Database size={17}/><strong>데이터셋</strong><span>{datasets.length}</span></div>
   <TextField placeholder="데이터셋 검색" fullWidth value={search} onChange={e=>setSearch(e.target.value)} slotProps={{input:{startAdornment:<Search size={15} style={{marginRight:8}}/>}}}/>
   <div className="dataset-list">{datasetGroups(datasets,search).map(group=><section key={group.kind} className="dataset-group" aria-label={group.label}><h3 title={group.description}>{group.label}<span>{group.datasets.length}</span></h3>{group.datasets.map(d=><button key={d.id} onClick={()=>select(d)} className={`dataset-item ${datasetId===d.id?'active':''}`}><Table2 size={17}/><span><strong>{d.name}</strong><DatasetScopeBadge projectId={d.project_id} projectName={projectName}/><small title={sourceNames(d)}>{sourceNames(d)}</small></span><ChevronDown size={13}/></button>)}{!group.datasets.length&&<p className="dataset-group-empty">{search?'검색 결과 없음':'등록된 데이터셋 없음'}</p>}</section>)}</div>
  </aside>
  <section className="data-main">
   <div className="data-heading"><div><h2>{dataset?.name??'데이터셋'}{dataset&&<><DatasetKindBadge kind={dataset.kind}/><DatasetScopeBadge projectId={dataset.project_id} projectName={projectName}/></>}</h2><p className="query-source-subtitle">{sourceNames(dataset)}</p>{projectId!==null&&<p className="dataset-scope-note">{projectName??'선택한 프로젝트'} 데이터만 조회합니다. 공통 데이터셋은 전체 프로젝트에서 확인하세요.</p>}</div><div className="data-heading-actions">{editable&&<Button variant="outlined" startIcon={<Plus size={16}/>} disabled={discovering} onClick={addDataset}>{discovering?'테이블 확인 중…':'데이터셋 추가'}</Button>}<Button variant="contained" startIcon={<Braces size={16}/>} disabled={!dataset} onClick={()=>setEdit(true)}>쿼리 관리</Button></div></div>
   {!datasets.length&&<Empty title="아직 등록된 데이터셋이 없습니다" description="데이터셋 추가에서 연결된 DB의 원천 테이블을 선택하세요."/>}
   <ErrorNotice error={sourceError}/>
   <ErrorNotice error={query.error}/>{ignored?.length?<Alert severity="info" sx={{mb:1}}>이 데이터셋에 없는 필터: {ignored.join(', ')}</Alert>:null}
   {!!dataset&&!query.isError&&<DataGrid columns={query.data?.columns??dataset?.columns??[]} rows={rows} loading={query.isPending} maxHeight="max(320px, calc(100dvh - 430px))" sort={sort} direction={direction} onSort={sortColumn}/>}
   <div className="pagination"><span>{query.data?.rows.length??0}행 표시 <span className="subtle-divider"/> 페이지 {cursors.length+1}</span><div><Button size="small" color="secondary" startIcon={<ArrowLeft size={14}/>} disabled={!cursors.length} onClick={()=>{setCursor(cursors[cursors.length-1]);setCursors(c=>c.slice(0,-1))}}>이전</Button><Button size="small" color="secondary" endIcon={<ArrowRight size={14}/>} disabled={!query.data?.next_cursor} onClick={()=>{setCursors(c=>[...c,cursor]);setCursor(query.data!.next_cursor)}}>다음</Button></div></div>
  </section>
  {edit&&dataset&&<DatasetEditor key={`${dataset.id}-${dataset.version}`} dataset={dataset} versions={versions.data??[dataset]} versionsError={versions.error} onSelectVersion={selectVersion} editable={editable} catalog={sourceCatalog} context={context} projectName={projectName} proposal={proposal??undefined} onClose={()=>{setEdit(false);clearProposal()}} onSaved={saved}/>}
  {draft&&<DatasetEditor key="new-dataset" isNew dataset={draft} catalog={sourceCatalog} context={context} projectName={projectName} onClose={()=>setDraft(null)} onSaved={saved}/>}
 </div>
}
