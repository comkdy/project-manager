import {useState} from 'react';
import {Button,Dialog,DialogActions,DialogContent,DialogTitle,MenuItem,TextField} from '@mui/material';
import {post} from './api';
import {ErrorNotice} from './components';
import {LocalDraftNotice,useLocalDraft} from './localDraft';
import type {Project} from './types';
import {blankEvaluationInput,evaluationRegistrationBody,validEvaluationInput} from './evaluationKnowledge';
import type {EvaluationRegistration as RegistrationResult} from './evaluationKnowledge';

export default function EvaluationRegistration({projects,active,close,saved}:{workspace:string;projects:Project[];active:boolean;close:()=>void;saved:(result:RegistrationResult)=>Promise<void>}){
 const [value,setValue]=useState(blankEvaluationInput),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(null);
 const draft=useLocalDraft({kind:'knowledge-evaluation',targetId:'new',projectId:null,baseVersion:1,value,validate:validEvaluationInput});
 const projectId='projectId' in value?value.projectId:undefined;
 const canSubmit=projectId!==undefined&&(projectId===null||projects.some(p=>p.id===projectId))&&!!value.text.trim()&&value.text.length<=6000&&!busy&&!draft.blocked;
 async function submit(){
  if(!canSubmit)return;
  setBusy(true);setError(null);
  try{
   const result=await post<RegistrationResult>('/semantic/collection/evaluations',evaluationRegistrationBody(value,projects));
   draft.clearSaved();
   await saved(result);
  }catch(e){setError(e)}finally{setBusy(false)}
 }
 return <Dialog open={active} onClose={()=>!busy&&close()} fullWidth maxWidth="md" aria-labelledby="evaluation-registration-title">
  <DialogTitle id="evaluation-registration-title">평가 사례 등록</DialogTitle>
  <DialogContent>
   <p>알고 있는 관계를 한 줄로 적어도 됩니다. AI가 원문을 바탕으로 정리하고, 확인 요청에서 보완·승인합니다.</p>
   <LocalDraftNotice disabled={busy} active={active} draft={draft} onRestore={record=>setValue(record.value)}/>
   <ErrorNotice error={error}/>
   <TextField fullWidth select required label="지식의 적용 범위" value={projectId===undefined?'':projectId===null?'common':String(projectId)} disabled={busy} sx={{mt:1,mb:2}} onChange={e=>{draft.markEdited();setValue(v=>({text:v.text,projectId:e.target.value==='common'?null:Number(e.target.value)}))}} helperText="데이터셋을 고르지 않아도 됩니다. 공통은 여러 프로젝트에서 참고할 지식입니다.">
    {projectId===undefined&&<MenuItem value="" disabled>보관한 원문을 복구했습니다. 적용 범위를 선택하세요.</MenuItem>}
    <MenuItem value="common">공통</MenuItem>
    {projects.map(p=><MenuItem key={p.id} value={String(p.id)}>{p.name}</MenuItem>)}
   </TextField>
   <TextField fullWidth required multiline minRows={7} label="평가 내용과 관측 결과" value={value.text} disabled={busy} onChange={e=>{draft.markEdited();setValue(v=>({...v,text:e.target.value}))}} inputProps={{maxLength:6000}}
    placeholder="예: Operation A의 Recipe 변경은 Inline A CD의 alpha에 영향을 줍니다.\n증가·감소 방향, 변화량, 비교 조건은 알고 있을 때만 덧붙여 주세요."
    helperText={`${value.text.length.toLocaleString()} / 6,000자 · 언급하지 않은 방향·영향량은 미정으로 보관합니다. 데이터 분석 결과는 별도 근거로 검토합니다.`}/>
   <p className="knowledge-help">기존 AI 지식으로 용어를 연결하고, 모호한 부분만 확인 요청에서 묻습니다. 승인은 보고 내용의 확인이며 원인 확정을 뜻하지 않습니다.</p>
  </DialogContent>
  <DialogActions><Button disabled={busy} onClick={close}>닫기</Button><Button variant="contained" disabled={!canSubmit} onClick={()=>void submit()}>{busy?'사례 등록 중…':'등록하고 확인 요청에서 확인'}</Button></DialogActions>
 </Dialog>;
}
