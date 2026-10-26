import {useState} from 'react';
import {Alert,Button,Checkbox,Chip,Dialog,DialogActions,DialogContent,DialogTitle,FormControlLabel} from '@mui/material';
import {useQuery,useQueryClient} from '@tanstack/react-query';
import {api} from './api';
import {ErrorNotice,Loading} from './components';
import Periods from './AnalysisSchedulePeriods';
import type {ScheduleHistory,ScheduleComparison,SeriesEvidence,HistoryRun} from './analysisHistory.generated';
import type {AuthSession} from './types';
import {historyMatches,comparisonChangeNames,evidenceStatusNames} from './analysisHistoryState';
import {activityStatusNames} from './workspaceActivity';

const stamp=(value:string|null|undefined)=>value?new Date(value).toLocaleString('ko-KR'):'기록 없음';
const number=(value:number|null)=>value===null?'—':value.toLocaleString('ko-KR',{maximumSignificantDigits:7});

function Evidence({value,kind}:{value:SeriesEvidence|null;kind:string}){
 if(!value)return <span className="history-muted">계열 없음</span>;
 return <div className="history-evidence"><strong>{value.status?evidenceStatusNames[value.status]??value.status:'기본 통계'}</strong>
 <span>{kind==='trend'?'기준':kind==='split'?'A':'전체'} 평균 {number(value.mean_a)} · 유효 표본 {number(value.samples_a)}</span>
 {kind!=='statistics'&&<><span>{kind==='trend'?'비교':'B'} 평균 {number(value.mean_b)} · 유효 표본 {number(value.samples_b)}</span><span>평균 차이 {number(value.delta)}</span><small>최소 표본 {number(value.min_samples)} · 변화량 기준 {number(value.absolute_delta)}</small></>}
 </div>;
}

function RunCard({title,run,request,onOpenRun}:{title:string;run:HistoryRun|null;request:ScheduleComparison['previous_request'];onOpenRun:(id:string)=>void}){
 return <article className="history-run-card"><h3>{title}</h3>{run&&request?<><Chip size="small" label={activityStatusNames[run.status]}/><p>완료: {stamp(run.completed_at)}<br/>원천 조회: {stamp(run.queried_at)}</p><details><summary>실행 기간·조건 확인</summary><Periods request={request}/></details><Button size="small" onClick={()=>onOpenRun(run.id)}>이 결과·질문 열기</Button></>:<p>비교할 이전 결과가 없거나 현재 범위에서 열 수 없습니다.</p>}</article>;
}

export default function AnalysisHistoryDialog({workspace,scheduleId,name,initialRunId,onClose,onOpenRun}:{workspace:string;scheduleId:string;name:string;initialRunId?:string;onClose:()=>void;onOpenRun:(id:string)=>void}){
 const actor=useQueryClient().getQueryData<AuthSession>(['auth-session'])?.user;
 const [runId,setRunId]=useState(initialRunId??null),[cursors,setCursors]=useState<Array<string|null>>([null]);
 const [offset,setOffset]=useState(0),[differences,setDifferences]=useState(true);
 const before=cursors[cursors.length-1];
 const history=useQuery({queryKey:['analytics-history',workspace,scheduleId,actor?.id,actor?.role,before],
  queryFn:({signal})=>api<ScheduleHistory>(`/analytics/schedules/${scheduleId}/history${before?`?before=${encodeURIComponent(before)}`:''}`,{signal}),enabled:!runId,retry:false,refetchInterval:15000,refetchIntervalInBackground:false});
 const comparison=useQuery({queryKey:['analytics-comparison',workspace,scheduleId,actor?.id,actor?.role,runId,offset,differences],
  queryFn:({signal})=>api<ScheduleComparison>(`/analytics/schedules/${scheduleId}/runs/${runId}/comparison?offset=${offset}&differences_only=${differences}`,{signal}),enabled:!!runId,retry:false});
 const list=!history.isError&&historyMatches(history.data,workspace,scheduleId)?history.data:undefined;
 const data=!comparison.isError&&historyMatches(comparison.data,workspace,scheduleId,runId??undefined)?comparison.data:undefined;
 function compare(id:string){setOffset(0);setDifferences(true);setRunId(id)}
 return <Dialog open fullWidth maxWidth="lg" onClose={onClose} aria-labelledby="analysis-history-title"><DialogTitle id="analysis-history-title">{name} · {runId?'결과 비교':'실행 이력'}</DialogTitle><DialogContent>
 <div className="history-toolbar">{runId&&<Button onClick={()=>setRunId(null)}>실행 이력으로</Button>}<Button disabled={runId?comparison.isFetching:history.isFetching} onClick={()=>void (runId?comparison.refetch():history.refetch())}>이력 새로고침</Button></div>
 <p className="activity-help">저장된 실행 결과를 확인합니다. 원천을 다시 조회하거나 분석을 시작하지 않습니다. 결과를 열면 기존 질문·승인과 재시도·취소로 이어집니다.</p>
 <ErrorNotice error={runId?comparison.error:history.error}/>
 {runId?(comparison.isPending?<Loading label="비교 근거를 불러오는 중"/>:data&&<>
  {!data.comparable&&<Alert severity="warning">{data.reasons.join(' ')} 두 결과의 조건을 먼저 확인하세요.</Alert>}
  <div className="history-run-pair"><RunCard title="이전 결과" run={data.previous} request={data.previous_request} onOpenRun={onOpenRun}/><RunCard title="선택한 결과" run={data.current} request={data.current_request} onOpenRun={onOpenRun}/></div>
  {data.comparable&&<Alert severity="info">전체 {data.total_series}개 계열 중 달라진 항목 {data.changed_count}개 · 변화 알림에 해당하는 항목 {data.notification_change_count}개. 수치만 변동한 경우는 알림 대상에서 제외합니다.</Alert>}
  <FormControlLabel control={<Checkbox checked={differences} disabled={!data.comparable} onChange={e=>{setDifferences(e.target.checked);setOffset(0)}}/>} label="달라진 항목만 보기"/>
  <div className="activity-table-wrap"><table className="activity-table history-table"><caption>PARA별 저장 결과와 판정 근거</caption><thead><tr><th>측정 계열</th><th>이전 결과</th><th>선택한 결과</th><th>비교 내용</th></tr></thead><tbody>{data.rows.map(row=><tr key={JSON.stringify([row.family,row.operation,row.parameter])}><th scope="row">{row.parameter}<small>{row.family.toUpperCase()} {row.operation}</small></th><td><Evidence value={row.previous} kind={data.previous_request?.kind??data.current_request.kind}/></td><td><Evidence value={row.current} kind={data.current_request.kind}/></td><td>{comparisonChangeNames[row.change]}{row.notification_change&&<small className="history-alert-reason">변화 알림 근거</small>}</td></tr>)}{!data.rows.length&&<tr><td colSpan={4}>{data.total_series?'표시할 차이가 없습니다. 전체 항목을 보려면 필터를 해제하세요.':'두 결과에서 확인할 측정 계열이 없습니다.'}</td></tr>}</tbody></table></div>
  <div className="history-pagination"><Button disabled={!offset||comparison.isFetching} onClick={()=>setOffset(Math.max(0,offset-100))}>이전 항목</Button><span>{data.matched_count}개 중 {data.rows.length?offset+1:0}–{offset+data.rows.length}</span><Button disabled={!data.has_more||comparison.isFetching} onClick={()=>setOffset(offset+100)}>다음 항목</Button></div>
  <p className="activity-help">기간이 이동한 기술 통계의 비교입니다. 통계적 유의성·규격 이탈·원인을 판정하지 않습니다. 승인 근거는 실행 당시 연결한 버전을 보존합니다.</p>
 </>):(history.isPending?<Loading label="실행 이력을 불러오는 중"/>:list&&<>
  {list.hidden_count>0&&<Alert severity="info">원천·프로젝트 범위가 변경된 실행 {list.hidden_count}건은 현재 범위에서 표시할 수 없습니다.</Alert>}
  <div className="history-list">{list.items.map(run=><article className="history-run" key={run.id}><div><Chip size="small" label={activityStatusNames[run.status]}/>{run.is_template&&<Chip size="small" variant="outlined" label="현재 조건의 원래 분석"/>}{run.retry_of&&<Chip size="small" variant="outlined" label="재시도"/>}<h3>{run.scheduled_for?`예약 ${stamp(run.scheduled_for)}`:`요청 ${stamp(run.created_at)}`}</h3><p>완료: {stamp(run.completed_at)} · 소요: {run.duration_seconds===null?'기록 없음':`${number(run.duration_seconds)}초`} · 연결된 질문 {run.question_count}개</p></div><div className="schedule-actions"><Button size="small" onClick={()=>onOpenRun(run.id)}>결과·질문·작업 제어</Button>{!run.is_template&&run.status==='completed'&&<Button size="small" onClick={()=>compare(run.id)}>이전 결과와 비교</Button>}{run.retry_of&&<Button size="small" onClick={()=>onOpenRun(run.retry_of!)}>재시도 원본</Button>}{run.retry_run_id&&<Button size="small" onClick={()=>onOpenRun(run.retry_run_id!)}>재시도 결과</Button>}</div></article>)}</div>
  {!list.items.length&&<p>표시할 실행 이력이 없습니다.</p>}
  <div className="history-pagination"><Button disabled={cursors.length===1||history.isFetching} onClick={()=>setCursors(v=>v.slice(0,-1))}>이전 이력</Button><span>{cursors.length}쪽 · 최신 실행부터 표시</span><Button disabled={!list.next_cursor||history.isFetching} onClick={()=>setCursors(v=>[...v,list.next_cursor])}>이전 실행 더 보기</Button></div>
 </>)}
 </DialogContent><DialogActions><Button onClick={onClose}>닫기</Button></DialogActions></Dialog>;
}
