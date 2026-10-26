import {useState} from 'react';
import {Alert,Button,FormControlLabel,Radio,RadioGroup} from '@mui/material';
import type {ChatKnowledge,KnowledgeDefinitionRef} from './chatKnowledge.generated';
import {kindNames} from './semanticTypes';
import {knowledgeChoicesComplete,selectKnowledgeDefinition} from './chatKnowledgeState';
import './chat-knowledge.css';

export default function ChatKnowledgePanel({knowledge,restored=false,disabled=false,onSelect}:{knowledge:ChatKnowledge;restored?:boolean;disabled?:boolean;onSelect?:(refs:KnowledgeDefinitionRef[])=>void}){
 const choiceKey=(ref:KnowledgeDefinitionRef)=>`${ref.item_id}:${ref.version}`;
 const [selected,setSelected]=useState<KnowledgeDefinitionRef[]>(knowledge.selected_definitions??[]);
 if(knowledge.availability==='unavailable')return <Alert severity="warning">이 응답의 승인 지식이나 DB 범위가 변경되었습니다. 현재 기준으로 다시 질문해 주세요.</Alert>;
 const choicesDisabled=disabled||!!knowledge.blocking;
 return <section className="chat-knowledge-panel" aria-label="AI에 제공한 승인 지식">
  {knowledge.blocking&&<Alert severity="warning">관련 승인 지식의 연결이나 필수 정의를 먼저 확인해야 합니다. AI 지식에서 해당 내용을 검토한 뒤 다시 질문해 주세요.</Alert>}
  {!!knowledge.sources?.length&&<details><summary>AI에 제공한 승인 지식 · {knowledge.sources.length}개{restored?' · 당시 제공 버전':''}</summary><ul>{knowledge.sources.map(source=><li key={`${source.item_id}:${source.version}`}><strong>{source.name}</strong><span>{kindNames[source.kind]} · 정의 v{source.version}{source.binding_version!==null?` · 매핑 v${source.binding_version}`:''}{source.domain?` · ${source.domain}`:''}</span></li>)}</ul><p>생성 시 참고하도록 제공한 정의입니다. SQL·카드가 업무 기준에 맞는지는 미리보기에서 확인해 주세요.</p></details>}
  {!!knowledge.clarifications?.length&&<div className="chat-knowledge-choices"><strong>사용할 업무 정의를 선택해 주세요</strong>{knowledge.clarifications.map(group=><fieldset key={group.id} disabled={choicesDisabled||!onSelect}><legend>{group.question}</legend><RadioGroup value={group.options.find(option=>selected.some(ref=>ref.item_id===option.item_id&&ref.version===option.version))?choiceKey(group.options.find(option=>selected.some(ref=>ref.item_id===option.item_id&&ref.version===option.version))!):''} onChange={(_,id)=>{const choice=group.options.find(option=>choiceKey(option)===id);if(choice)setSelected(current=>selectKnowledgeDefinition(knowledge,group,choice,current))}}>{group.options.map(option=><FormControlLabel key={`${option.item_id}:${option.version}`} value={choiceKey(option)} control={<Radio size="small"/>} label={`${option.name} · ${option.domain||'업무 영역 미지정'} · v${option.version}`}/>)}</RadioGroup></fieldset>)}{onSelect&&<Button size="small" variant="outlined" disabled={choicesDisabled||!knowledgeChoicesComplete(knowledge,selected)} onClick={()=>onSelect(selected)}>선택한 정의로 다시 작성</Button>}</div>}
  {!!knowledge.warnings?.length&&<ul className="chat-knowledge-notes">{knowledge.warnings.map((warning,index)=><li key={index}>{warning}</li>)}</ul>}
  {!!knowledge.gaps?.length&&<ul className="chat-knowledge-notes">{knowledge.gaps.map((gap,index)=><li key={index}>{gap}</li>)}</ul>}
 </section>;
}
