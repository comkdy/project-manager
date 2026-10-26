import {useEffect,useId,useImperativeHandle,useRef,useState,type Ref} from 'react';
import {X} from 'lucide-react';
import type {Column,ValueToken} from './types';
import {allowedDraft,columnSuggestions,expressionIssue,expressionNumber,parseExpressionText,tokenLabel} from './cardExpressions';

export type ExpressionInputHandle={complete:()=>ValueToken[]|null};

type Props={commitRef?:Ref<ExpressionInputHandle>;tokens:ValueToken[];columns:Column[];draft:string;onDraft:(value:string)=>void;onChange:(tokens:ValueToken[])=>void};

export default function CardExpressionInput({tokens,columns,draft,onDraft,onChange,commitRef}:Props){
 const input=useRef<HTMLInputElement>(null),list=useRef<HTMLDivElement>(null),listId=useId(),helpId=useId();
 const composing=useRef(false),[composition,setComposition]=useState<string|null>(null);
 const [cursor,setCursor]=useState(tokens.length),[replacing,setReplacing]=useState<number|null>(null);
 const [focused,setFocused]=useState(false),[dismissed,setDismissed]=useState(false),[active,setActive]=useState(0),[rejected,setRejected]=useState('');
 const at=Math.min(cursor,tokens.length),end=at+(replacing===at?1:0),issue=expressionIssue(tokens,columns);
 const suggestions=columnSuggestions(draft,columns);
 const canSuggest=!!suggestions.length&&!expressionIssue([...tokens.slice(0,at),{kind:'column',name:suggestions[0].name},...tokens.slice(end)],columns,true);
 const showSuggestions=focused&&!dismissed&&!!draft.trim()&&canSuggest&&!expressionNumber.test(draft)&&draft!=='.';
 const activeIndex=Math.min(active,Math.max(0,suggestions.length-1));
 const focus=()=>input.current?.focus();
 const reject=(message:string)=>setRejected(message);
 useEffect(()=>{if(replacing!==null){input.current?.focus();input.current?.select()}},[replacing]);
 useEffect(()=>{if(showSuggestions)list.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({block:'nearest'})},[activeIndex,showSuggestions,draft]);
 function insert(additions:ValueToken[],restoreFocus=true){
  const next=[...tokens.slice(0,at),...additions,...tokens.slice(end)];
  const error=expressionIssue(next,columns,true);
  if(error){reject(error);return false}
  onChange(next);setCursor(at+additions.length);setReplacing(null);onDraft('');setRejected('');setActive(0);setDismissed(false);if(restoreFocus)focus();return true;
 }
 function pending(useSuggestion=false):ValueToken[]|null{
  if(!draft.trim())return [];
  const exact=columns.find(c=>c.name===draft.trim());
  if(useSuggestion&&showSuggestions&&suggestions[activeIndex])return [{kind:'column',name:suggestions[activeIndex].name}];
  if(exact)return [{kind:'column',name:exact.name}];
  if(expressionNumber.test(draft.trim()))return [{kind:'number',value:draft.trim()}];
  reject('자동완성에서 컬럼을 선택하거나 숫자 입력을 완료하세요.');return null;
 }
 useImperativeHandle(commitRef,()=>({complete:()=>{
  const additions=pending();if(additions===null)return null;
  const next=additions.length?[...tokens.slice(0,at),...additions,...tokens.slice(end)]:tokens;
  const error=expressionIssue(next,columns);if(error){reject(error);return null}
  return next;
 }}));
 function operator(value:Extract<ValueToken,{kind:'operator'}>['value']){
  const additions=pending();if(additions)insert([...additions,{kind:'operator',value}]);
 }
 function remove(index:number){
  onChange(tokens.filter((_,i)=>i!==index));setCursor(Math.max(0,at-(index<at?1:0)));setReplacing(null);onDraft('');setRejected('');focus();
 }
 function editColumn(item:Extract<ValueToken,{kind:'column'}>,index:number){
  setCursor(index);setReplacing(index);onDraft(item.name);setActive(0);setRejected('');setDismissed(false);focus();
 }
 const token=(item:ValueToken,index:number)=><span className={`arithmetic-token is-${item.kind}`} key={index}>
  <button type="button" title={item.kind==='column'?'다른 컬럼으로 변경':'이 항목 뒤에 입력'} aria-label={item.kind==='column'?`${item.name} 컬럼 변경`:`${tokenLabel(item)} 뒤에 입력`} onMouseDown={e=>e.preventDefault()} onClick={()=>{if(item.kind==='column')editColumn(item,index);else{setReplacing(null);onDraft('');setCursor(index+1);focus()}}}>{item.kind==='operator'?item.value:tokenLabel(item)}</button>
  {item.kind==='column'&&<button type="button" className="arithmetic-token-remove" aria-label={`${item.name} 컬럼 삭제`} onMouseDown={e=>e.preventDefault()} onClick={()=>remove(index)}><X size={12}/></button>}
 </span>;
 return <div className="arithmetic-editor">
  <div className="arithmetic-heading"><label htmlFor={`${listId}-input`}>직접 계산식</label><button type="button" onClick={()=>{onChange([]);onDraft('');setCursor(0);setReplacing(null);setRejected('');setDismissed(false);focus()}} disabled={!tokens.length&&!draft}>비우기</button></div>
  <div className={`arithmetic-input ${focused?'focused':''}`} onClick={e=>{if(e.target===e.currentTarget){setReplacing(null);onDraft('');setCursor(tokens.length);focus()}}}>
   {tokens.slice(0,at).map(token)}
   <input ref={input} autoFocus id={`${listId}-input`} role="combobox" aria-label="계산식 입력" autoComplete="off" spellCheck={false} aria-autocomplete="list" aria-expanded={showSuggestions} aria-controls={showSuggestions?listId:undefined} aria-activedescendant={showSuggestions?`${listId}-${activeIndex}`:undefined} aria-describedby={helpId} value={composition??draft} placeholder={tokens.length?'':'컬럼명 입력 (예: p)'} style={{width:`${Math.min(24,Math.max(5,(composition??draft).length+1))}ch`}} onFocus={()=>{setFocused(true);setDismissed(false)}} onBlur={()=>{setFocused(false);if(draft.trim()){const additions=pending();if(additions)insert(additions,false)}}}
    onCompositionStart={()=>{composing.current=true;setComposition(draft)}} onCompositionEnd={e=>{composing.current=false;setComposition(null);if(allowedDraft(e.currentTarget.value,columns)){onDraft(e.currentTarget.value);setRejected('');setActive(0);setDismissed(false)}else reject('현재 데이터셋에 있는 컬럼명만 입력할 수 있습니다.')}}
    onChange={e=>{const value=e.target.value;if(composing.current){setComposition(value);return}if(allowedDraft(value,columns)){onDraft(value);setRejected('');setActive(0);setDismissed(false)}else reject('컬럼명, 숫자와 + - * / ( )만 입력할 수 있습니다.')}}
    onPaste={e=>{e.preventDefault();const text=e.clipboardData.getData('text');const selectedAll=input.current?.selectionStart===0&&input.current?.selectionEnd===draft.length;if(draft.trim()&&!selectedAll){reject('입력 중인 컬럼이나 숫자를 먼저 완성하거나 지워 주세요.');return}try{insert(parseExpressionText(text,columns))}catch(error){if(allowedDraft(text,columns)){onDraft(text);setActive(0);setDismissed(false);setRejected('')}else reject((error as Error).message)}}}
    onKeyDown={e=>{
     if(e.nativeEvent.isComposing)return;
     if(['+','-','*','/','(',')'].includes(e.key)){e.preventDefault();operator(e.key as '+');return}
     if(e.key==='ArrowDown'||e.key==='ArrowUp'){if(showSuggestions){e.preventDefault();setActive((activeIndex+(e.key==='ArrowDown'?1:-1)+suggestions.length)%suggestions.length)}return}
     if(e.key==='Escape'){
      if(showSuggestions||replacing!==null){e.preventDefault();e.stopPropagation();setDismissed(true);if(replacing!==null){setCursor(replacing+1);setReplacing(null);onDraft('')}}return;
     }
     if((e.key==='Enter'||e.key==='Tab'||e.key===' ')&&draft.trim()){
      e.preventDefault();const additions=pending(true);if(additions)insert(additions);return;
     }
     if(e.key==='Enter'){e.preventDefault();return}
     if(!draft&&['Backspace','Delete','ArrowLeft','ArrowRight','Home','End'].includes(e.key)){
      e.preventDefault();
      if(replacing!==null){if(e.key==='Backspace'||e.key==='Delete')remove(replacing);else{setReplacing(null);setCursor(replacing)}return}
      if(e.key==='Backspace'&&at>0)remove(at-1);
      else if(e.key==='Delete'&&at<tokens.length)remove(at);
      else if(e.key==='ArrowLeft')setCursor(Math.max(0,at-1));
      else if(e.key==='ArrowRight')setCursor(Math.min(tokens.length,at+1));
      else if(e.key==='Home')setCursor(0);
      else if(e.key==='End')setCursor(tokens.length);
     }
    }}/>
   {tokens.slice(end).map((item,i)=>token(item,end+i))}
  </div>
  {showSuggestions&&<div ref={list} className="arithmetic-suggestions" id={listId} role="listbox" aria-label="계산식 컬럼 자동완성">{suggestions.map((column,index)=><button type="button" role="option" key={column.name} id={`${listId}-${index}`} aria-selected={index===activeIndex} tabIndex={-1} onMouseDown={e=>e.preventDefault()} onClick={()=>insert([{kind:'column',name:column.name}])}><span>{column.name}</span><small>{column.type}</small></button>)}</div>}
  <div className="arithmetic-keyboard-hint"><span>↑ ↓ 선택 · Enter 컬럼 확정</span><span><kbd>+</kbd> <kbd>-</kbd> <kbd>*</kbd> <kbd>/</kbd> 직접 입력</span></div>
  <div id={helpId} className={`arithmetic-help ${rejected?'rejected':''}`} role="status">{rejected||issue||''}</div>
 </div>;
}
