import type {DatabaseStatus} from './types.ts';

interface StatusQuery {data?:DatabaseStatus;isFetching:boolean;isError:boolean;dataUpdatedAt:number}
interface Signal {state:'connected'|'checking'|'error'|'idle';label:string}

export function databaseStatusSignal(query:StatusQuery,active=false):Signal {
 if(query.isError){
  const last=query.data?.status==='connected'&&query.dataUpdatedAt?` · 마지막 확인 ${new Date(query.dataUpdatedAt).toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit'})}`:'';
  return {state:'error',label:`연결 상태 확인 실패${last}`};
 }
 if(query.isFetching&&(active||!query.data))return {state:'checking',label:'DB 연결 확인 중'};
 if(query.data?.status==='connected')return {state:'connected',label:active?'연결됨 · 활성 DB':`${query.data.database??'DB'} 연결됨`};
 return {state:'error',label:query.data?.status==='not_configured'?'DB 미연결':'DB 연결 오류'};
}
