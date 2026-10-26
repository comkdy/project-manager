export interface LocalDraftScope {userId:string;role:string;workspace:string}
export interface LocalDraftIdentity extends LocalDraftScope {projectId:number|null;kind:string;targetId:string}
export interface LocalDraftRecord<T> {schema:1;identity:LocalDraftIdentity;baseVersion:string|number|null;value:T;savedAt:number;writer:string}
export interface DraftStorage {getItem:(key:string)=>string|null;setItem:(key:string,value:string)=>void;removeItem:(key:string)=>void}
export const draftKey=(identity:LocalDraftIdentity)=>`pm:edit-draft:v1:${encodeURIComponent(JSON.stringify([identity.userId,identity.role,identity.workspace,identity.projectId,identity.kind,identity.targetId]))}`;
const forbidden=/^(?:rows|query_result|card_result|preview|api_?key|llm_?key|password|access_token|refresh_token|authorization)$/i;
export function draftValueSafe(value:unknown):boolean {
 if(value===null||typeof value==='string'||typeof value==='boolean')return true;
 if(typeof value==='number')return Number.isFinite(value);
 if(Array.isArray(value))return value.every(draftValueSafe);
 if(typeof value==='object')return Object.entries(value as object).every(([key,item])=>!forbidden.test(key)&&(item===undefined||draftValueSafe(item)));
 return false;
}
export function readLocalDraft<T>(raw:string|null,identity:LocalDraftIdentity,validate:(value:unknown)=>boolean):LocalDraftRecord<T>|null {
 if(raw===null)return null;
 if(raw.length>600000)throw new Error('보관된 초안이 너무 커서 불러올 수 없습니다.');
 let record:LocalDraftRecord<T>;try{record=JSON.parse(raw)}catch{throw new Error('보관된 초안을 읽을 수 없습니다. 현재 편집 내용은 유지됩니다.')}
 if(record?.schema!==1||draftKey(record.identity??{} as LocalDraftIdentity)!==draftKey(identity)||!Number.isFinite(record.savedAt)||typeof record.writer!=='string'||!['string','number'].includes(typeof record.baseVersion)&&record.baseVersion!==null||!draftValueSafe(record.value)||!validate(record.value))throw new Error('보관된 초안 형식이 맞지 않습니다. 현재 편집 내용은 유지됩니다.');
 return record;
}
export function writeLocalDraft<T>(storage:DraftStorage,identity:LocalDraftIdentity,expected:string|null,record:LocalDraftRecord<T>):string {
 const key=draftKey(identity);
 if(storage.getItem(key)!==expected)throw new Error('다른 창에서 보관한 초안이 있습니다. 내용을 비교하고 선택해 주세요.');
 if(!draftValueSafe(record.value))throw new Error('조회 결과나 인증 정보가 포함된 내용은 초안으로 보관할 수 없습니다.');
 const raw=JSON.stringify(record);
 if(raw.length>500000)throw new Error('편집 내용이 커서 이 기기에 자동 보관할 수 없습니다.');
 try{storage.setItem(key,raw)}catch{throw new Error('기기 저장 공간이나 브라우저 설정으로 초안을 보관하지 못했습니다. 편집창을 닫기 전에 내용을 별도로 보관해 주세요.')}
 return raw;
}
