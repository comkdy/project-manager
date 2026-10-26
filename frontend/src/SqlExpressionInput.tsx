import {useId,useRef,useState} from 'react';
import {IconButton,Paper,Popper,TextField,Tooltip} from '@mui/material';
import {ChevronDown} from 'lucide-react';
import type {Column} from './types';
import {completionRange,insertCompletion,type CompletionRange} from './sqlCompletion';

export default function SqlExpressionInput({value,columns,disabled,onChange,autoFocus=false,label='SQL 표현식'}:{value:string;columns:Column[];disabled:boolean;onChange:(value:string)=>void;autoFocus?:boolean;label?:string}){
 const input=useRef<HTMLTextAreaElement>(null),anchor=useRef<HTMLDivElement>(null);
 const listId=useId(),[range,setRange]=useState<CompletionRange|null>(null),[active,setActive]=useState(0);
 const suggestions=range?columns.filter(c=>c.name.toLowerCase().includes(range.prefix.toLowerCase())).sort((a,b)=>Number(b.name.toLowerCase().startsWith(range.prefix.toLowerCase()))-Number(a.name.toLowerCase().startsWith(range.prefix.toLowerCase()))):[];
 const open=!disabled&&!!range&&suggestions.length>0;
 const activeIndex=Math.min(active,suggestions.length-1);
 function suggest(text:string,start:number,end:number,force=false,all=false){
  const next=completionRange(text,start,end);
  setRange(next&&(force||next.prefix)?{...next,prefix:all?'':next.prefix}:null);setActive(0);
 }
 function choose(name:string){
  if(!range||!input.current)return;
  const next=insertCompletion(value,range,name);onChange(next.text);setRange(null);
  requestAnimationFrame(()=>{input.current?.focus();input.current?.setSelectionRange(next.caret,next.caret)});
 }
 function move(next:number){setActive(next);document.getElementById(`${listId}-${next}`)?.scrollIntoView({block:'nearest'})}
 return <div className="sql-expression-input" ref={anchor}>
  <TextField fullWidth required autoFocus={autoFocus} multiline minRows={2} maxRows={8} label={label} inputRef={input} value={value} disabled={disabled} error={!value.trim()} placeholder="예: lot_id 또는 value * 1000" helperText={!value.trim()?'표현식을 입력하세요.':undefined} onChange={e=>{onChange(e.target.value);const target=e.target as HTMLTextAreaElement;suggest(target.value,target.selectionStart,target.selectionEnd)}} onKeyDown={e=>{
   if(e.nativeEvent.isComposing)return;
   if(!open)return;
   if(e.key==='ArrowDown'){e.preventDefault();move((activeIndex+1)%suggestions.length)}
   else if(e.key==='ArrowUp'){e.preventDefault();move((activeIndex+suggestions.length-1)%suggestions.length)}
   else if(e.key==='Enter'||e.key==='Tab'){e.preventDefault();choose(suggestions[activeIndex].name)}
   else if(e.key==='Escape'){e.preventDefault();e.stopPropagation();setRange(null)}
   else if(e.key==='ArrowLeft'||e.key==='ArrowRight'||e.key==='Home'||e.key==='End')setRange(null);
  }} onBlur={()=>setRange(null)} slotProps={{htmlInput:{className:'sql-input',role:'combobox','aria-label':label,'aria-autocomplete':'list','aria-expanded':open,'aria-controls':open?listId:undefined,'aria-activedescendant':open?`${listId}-${activeIndex}`:undefined,onClick:()=>setRange(null),spellCheck:false},input:{endAdornment:<Tooltip title="컬럼 자동완성"><IconButton size="small" aria-label="컬럼 자동완성" disabled={disabled} onMouseDown={e=>e.preventDefault()} onClick={()=>{const target=input.current;if(target){target.focus();suggest(value,target.selectionStart,target.selectionEnd,true,true)}}}><ChevronDown size={16}/></IconButton></Tooltip>}}}/>
  <Popper role="presentation" open={open} anchorEl={anchor.current} placement="bottom-start" className="sql-completion-popper" modifiers={[{name:'offset',options:{offset:[0,4]}},{name:'preventOverflow',options:{padding:12}}]}>
   <Paper className="sql-completion-menu" style={{width:anchor.current?.clientWidth}} elevation={5}>
    <ul id={listId} role="listbox" aria-label="사용 가능한 컬럼">{suggestions.map((c,i)=><li key={c.name} id={`${listId}-${i}`} role="option" aria-selected={i===activeIndex} className={i===activeIndex?'active':''} onMouseDown={e=>e.preventDefault()} onClick={()=>choose(c.name)}><code>{c.name}</code><small>{c.type}</small></li>)}</ul>
   </Paper>
  </Popper>
 </div>
}
