import type {ChartRequest} from './dashboardRefresh';
export class ApiError extends Error {constructor(public status:number,message:string){super(message)}}
let workspaceToken='';
export function setWorkspaceToken(token:string){workspaceToken=token}
function workspaceHeaders():Record<string,string>{return workspaceToken?{'X-Workspace-Token':workspaceToken}:{}}
export async function api<T>(path:string, init:RequestInit = {}):Promise<T> {
 const response=await fetch(`/api${path}`,{credentials:'same-origin',...init,headers:{'Content-Type':'application/json',...workspaceHeaders(),...init.headers}});
 if(!response.ok){let message=`요청 실패 (${response.status})`;try {const data=await response.json(); message=typeof data.detail==='string'?data.detail:JSON.stringify(data.detail??data)}catch{} throw new ApiError(response.status,message)}
 if(response.status===204)return undefined as T;
 return response.json();
}
export const post=<T>(path:string,body:unknown)=>api<T>(path,{method:'POST',body:JSON.stringify(body)});
export const requestChart:ChartRequest=(body,signal)=>api('/cards/query',{method:'POST',body:JSON.stringify(body),signal});
export async function streamChat(body:unknown,onEvent:(event:string,data:any)=>void,signal:AbortSignal) {
 const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json',...workspaceHeaders()},credentials:'same-origin',body:JSON.stringify(body),signal});
 if(!r.ok){const data=await r.json();throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail))}
 const reader=r.body!.getReader(),decoder=new TextDecoder();let pending='';
 while(true){const {value,done}=await reader.read();pending+=decoder.decode(value,{stream:!done}).replace(/\r\n/g,'\n');let index:number;while((index=pending.indexOf('\n\n'))>=0){const block=pending.slice(0,index);pending=pending.slice(index+2);const lines=block.split('\n');const event=lines.find(l=>l.startsWith('event:'))?.slice(6).trim()??'message';const data=lines.filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trim()).join('\n');if(data){try{onEvent(event,JSON.parse(data))}catch{onEvent('status',{message:data})}}}if(done)break;}
}
