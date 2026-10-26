import {useState} from 'react';
import {Alert,Button,Checkbox,Chip,Dialog,DialogActions,DialogContent,DialogTitle,FormControlLabel} from '@mui/material';
import {useQuery} from '@tanstack/react-query';
import {api,ApiError} from './api';
import {ErrorNotice,Loading} from './components';
import type {DatasetCardImpact,DatasetImpact} from './datasetImpact.generated';
import {canSaveDatasetImpact,datasetImpactQueryOptions,type DatasetImpactSnapshot} from './datasetImpactReview';
import DatasetScopeBadge from './DatasetScopeBadge';
import './dataset-impact.css';

const statusNames={compatible:'설정상 호환',review:'추가 확인 필요',incompatible:'호환 문제'} as const;
const changeNames={added:'추가',removed:'삭제',type_changed:'타입 변경'} as const;
const kindNames:Record<string,string>={kpi:'KPI',bar:'막대',line:'선',scatter:'산점도',histogram:'히스토그램',box:'상자',wafer:'Wafer map',gantt:'간트'};

export default function DatasetImpactDialog({snapshot,projectName,onClose,onSave,onRefreshVersions}:{snapshot:DatasetImpactSnapshot;projectName?:string;onClose:()=>void;onSave:(snapshot:DatasetImpactSnapshot)=>Promise<void>;onRefreshVersions:()=>Promise<void>}){
 const query=useQuery(datasetImpactQueryOptions(snapshot,(value,signal)=>api<DatasetImpact>(`/datasets/${encodeURIComponent(value.datasetId)}/impact`,{method:'POST',body:JSON.stringify(value.body),signal})));
 const [acknowledgedAt,setAcknowledgedAt]=useState<number|null>(null),[saving,setSaving]=useState(false),[refreshingVersions,setRefreshingVersions]=useState(false),[saveError,setSaveError]=useState<unknown>(null);
 const result=query.data,error=saveError??query.error,busy=saving||refreshingVersions;
 const conflict=error instanceof ApiError&&error.status===409;
 const acknowledged=acknowledgedAt!==null&&acknowledgedAt===query.dataUpdatedAt;
 const allowed=!busy&&!saveError&&canSaveDatasetImpact(snapshot,query,acknowledged);
 const incompatible=result?.cards.filter(card=>card.status==='incompatible').length??0;
 const boards=new Map<string,DatasetCardImpact[]>();
 for(const card of result?.cards??[])boards.set(card.dashboard_id,[...(boards.get(card.dashboard_id)??[]),card]);
 async function save(){
  if(!allowed)return;
  setSaving(true);setSaveError(null);
  try{await onSave(snapshot)}catch(e){setSaveError(e);setAcknowledgedAt(null)}finally{setSaving(false)}
 }
 async function refreshVersions(){
  setRefreshingVersions(true);
  try{await onRefreshVersions()}catch(e){setSaveError(e)}finally{setRefreshingVersions(false)}
 }
 function retry(){setSaveError(null);setAcknowledgedAt(null);void query.refetch()}
 return <Dialog open fullWidth maxWidth="md" onClose={()=>!busy&&onClose()} aria-labelledby="dataset-impact-title">
  <DialogTitle id="dataset-impact-title">변경 영향 확인</DialogTitle>
  <DialogContent className="dataset-impact-content">
   <div className="dataset-impact-heading"><strong>{snapshot.body.name}</strong><DatasetScopeBadge projectId={snapshot.body.project_id} projectName={projectName}/><span>현재 v{snapshot.body.expected_version} → 저장 후 v{snapshot.body.expected_version+1}</span></div>
   {snapshot.viewedVersion!==snapshot.body.expected_version&&<Alert severity="info" sx={{mb:2}}>편집을 시작한 정의는 v{snapshot.viewedVersion}입니다. 영향은 현재 최신 v{snapshot.body.expected_version}과 비교하며, 저장하면 새 버전으로 반영됩니다.</Alert>}
   <Alert severity="info" sx={{mb:2}}>현재 저장된 카드 설정을 기준으로 확인한 결과입니다. 실제 데이터 값·분포나 Python 스크립트를 실행해 검증한 결과는 아닙니다. 확인 이후 다른 사용자의 변경이 생길 수 있습니다.</Alert>
   {query.isFetching?<Loading label="컬럼과 연결 카드의 변경 영향을 확인하는 중"/>:query.isSuccess&&result&&<>
    <section aria-labelledby="dataset-impact-columns"><h3 id="dataset-impact-columns">출력 컬럼 변경 <small>{result.column_changes.length}개</small></h3>
     {result.column_changes.length?<div className="dataset-impact-table-wrap"><table className="dataset-impact-table"><thead><tr><th scope="col">컬럼</th><th scope="col">변경</th><th scope="col">이전 타입</th><th scope="col">새 타입</th></tr></thead><tbody>{result.column_changes.map(column=><tr key={column.name}><th scope="row">{column.name}</th><td>{changeNames[column.change]}</td><td>{column.previous_type??'—'}</td><td>{column.next_type??'—'}</td></tr>)}</tbody></table></div>:<p>출력 컬럼의 이름·타입 변경은 없습니다. 데이터 값이나 집계 결과가 동일하다는 의미는 아닙니다.</p>}
    </section>
    <section aria-labelledby="dataset-impact-cards"><h3 id="dataset-impact-cards">연결된 카드 <small>{boards.size}개 보드 · {result.cards.length}개 카드</small></h3>
     {!result.cards.length?<p>현재 확인 가능한 보드 중 이 데이터셋을 참조하는 저장된 카드는 없습니다.</p>:[...boards].map(([id,cards])=><div className="dataset-impact-board" key={id}>
      <h4>{cards[0].project_id===null?'전체 프로젝트 보드':`${cards[0].project_name??'프로젝트'} 보드`} <small>보드 v{cards[0].dashboard_version}</small></h4>
      <ul className="dataset-impact-card-list">{cards.map(card=><li key={card.card_id}><div><strong>{card.card_title}</strong><span>{kindNames[card.kind]??card.kind}</span><Chip size="small" label={statusNames[card.status]} color={card.status==='incompatible'?'error':card.status==='review'?'warning':'default'} variant="outlined"/></div>{card.issues.length>0&&<ul>{card.issues.map((issue,index)=><li key={index}>{issue}</li>)}</ul>}</li>)}</ul>
     </div>)}
    </section>
    {result.notes.length>0&&<section className="dataset-impact-notes"><h3>확인 범위와 참고 사항</h3><ul>{result.notes.map((note,index)=><li key={index}>{note}</li>)}</ul></section>}
    {incompatible>0&&<Alert severity="warning" sx={{mt:2}}>호환 문제가 있는 카드 {incompatible}개가 있습니다. 그대로 저장하면 해당 카드의 조회가 실패하거나 표시가 달라질 수 있습니다.<FormControlLabel control={<Checkbox checked={acknowledged} disabled={busy} onChange={(_,checked)=>setAcknowledgedAt(checked?query.dataUpdatedAt:null)}/>} label="영향받는 카드와 호환 문제를 확인했으며, 이 변경을 저장합니다."/></Alert>}
   </>}
   <ErrorNotice error={error}/>
   {conflict?<Alert severity="warning" action={<Button disabled={busy} onClick={()=>void refreshVersions()}>최신 버전 목록 확인</Button>}>다른 변경과 충돌했습니다. 최신 버전을 확인한 뒤 조회 결과와 변경 영향을 다시 검토해 주세요. 현재 편집 내용은 유지됩니다.</Alert>:error&&<Button disabled={busy||query.isFetching} onClick={retry}>영향 다시 확인</Button>}
  </DialogContent>
  <DialogActions><Button disabled={busy} onClick={onClose}>편집으로 돌아가기</Button><Button variant="contained" disabled={!allowed} onClick={()=>void save()}>{saving?'저장 중…':`확인 후 v${snapshot.body.expected_version+1} 저장`}</Button></DialogActions>
 </Dialog>;
}
