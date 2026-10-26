import {useEffect,useState} from 'react';
import {Alert,Button,Chip,FormControlLabel,Switch} from '@mui/material';
import {useQuery} from '@tanstack/react-query';
import {api} from './api';
import {ErrorNotice,Loading} from './components';
import type {OperationsStatus} from './operations.generated';
import {operationActions,operationCounts,operationNames,operationsExpired,operationsMatches,type OperationAction} from './operationsState';
import './operations.css';

export default function OperationsPanel({workspace,userId,onAction}:{workspace:string;userId:string;onAction:(action:OperationAction)=>void}){
 const [all,setAll]=useState(false),[now,setNow]=useState(Date.now());
 useEffect(()=>{const interval=window.setInterval(()=>setNow(Date.now()),15000);return()=>window.clearInterval(interval)},[]);
 const query=useQuery({queryKey:['admin-operations',workspace,userId],queryFn:({signal})=>api<OperationsStatus>('/admin/operations',{signal}),enabled:false,retry:false,refetchOnWindowFocus:false,refetchOnReconnect:false});
 const data=!query.isError&&operationsMatches(query.data,workspace)?query.data:undefined;
 const counts=operationCounts(data?.checks??[]),expired=!!data&&operationsExpired(data.checked_at,now);
 const visible=data?.checks.filter(item=>all||item.status==='attention'||item.status==='unknown')??[];
 return <section className="operations-panel" aria-label="운영 점검과 문제 안내">
  <div className="activity-section-heading"><div><h2>운영 점검</h2><p>관리자 전용 · 전체 프로젝트의 실행 준비와 문제 원인을 수동으로 확인합니다.</p></div><Button variant="contained" disabled={query.isFetching} onClick={()=>{setNow(Date.now());void query.refetch()}}>{query.isFetching?'점검 중…':data?'다시 점검':'운영 상태 점검'}</Button></div>
  <p className="activity-help">원천 연결·자동 처리기·질문과 승인·정기 예약·백업 기록을 확인합니다. 점검만으로 분석·승인·백업이 실행되지는 않습니다.</p>
  <ErrorNotice error={query.error}/>{query.isError&&<Alert severity="warning">이번 점검 결과를 받지 못했습니다. 이전 결과를 현재 상태로 표시하지 않습니다. 서버 연결과 로그인 상태를 확인하세요.</Alert>}
  {query.isFetching&&<Loading label="기록과 연결을 확인하는 중"/>}
  {data&&<>
   <div className="operations-summary" aria-live="polite"><Chip color={counts.attention?'warning':'default'} label={`확인 필요 ${counts.attention}`}/><Chip variant="outlined" label={`미확인 ${counts.unknown}`}/><span>확인됨 {counts.ok} · 참고 {counts.info}</span><span>점검 시각: {new Date(data.checked_at).toLocaleString('ko-KR')}</span></div>
   {expired&&<Alert severity="info">이전 점검 결과입니다. 현재 상태는 ‘다시 점검’으로 확인하세요.</Alert>}
   <FormControlLabel control={<Switch checked={all} onChange={(_,checked)=>setAll(checked)}/>} label="확인된 항목·참고 안내도 보기"/>
   <div className="operations-checks">{visible.map(item=><article key={item.id} className={`operations-check ${item.status}`}><div><small>{item.scope}</small><h3>{item.title}</h3></div><Chip size="small" variant="outlined" color={item.status==='attention'?'warning':item.status==='ok'?'success':'default'} label={operationNames[item.status]}/><p>{item.message}</p><p className="operations-guidance">{item.guidance}</p>{item.observed_at&&<small>관측·기록 시각: {new Date(item.observed_at).toLocaleString('ko-KR')}</small>}{item.action&&<Button size="small" onClick={()=>onAction(item.action!)}>{operationActions[item.action]}</Button>}</article>)}</div>
   {!visible.length&&<p>선택한 표시 범위에 확인 필요·미확인 항목이 없습니다. 전체 항목의 점검 범위도 확인하세요.</p>}
   <details className="operations-notes"><summary>점검 범위와 해석</summary><ul>{data.notes.map(note=><li key={note}>{note}</li>)}</ul></details>
  </>}
 </section>;
}
