import {useEffect,useRef,useState} from 'react';
import {Button,Chip,IconButton} from '@mui/material';
import {Plus,Trash2,X} from 'lucide-react';
import type {Column} from './types';
import SqlExpressionInput from './SqlExpressionInput';

export default function GroupColumns({groups,inputs,disabled,onChange}:{groups:string[];inputs:Column[];disabled:boolean;onChange:(groups:string[])=>void}){
 const [selected,setSelected]=useState<number|null>(null);
 const detail=useRef<HTMLElement>(null);
 useEffect(()=>{detail.current?.scrollIntoView({block:'nearest'})},[selected]);
 const expression=selected===null?undefined:groups[selected];
 return <div className="group-columns">
  <div className="output-column-buttons" role="group" aria-label="group 목록">
   {groups.map((value,i)=><Chip component="button" type="button" key={i} label={value||'새 group'} variant={selected===i?'filled':'outlined'} color={selected===i?'primary':'default'} disabled={disabled} onClick={()=>setSelected(selected===i?null:i)} aria-label={`${value||'새 group'} group 편집`} aria-expanded={selected===i} aria-controls={selected===i?'group-column-detail':undefined}/>)}
   <Button size="small" startIcon={<Plus size={14}/>} disabled={disabled||groups.length>=30} onClick={()=>{onChange([...groups,'']);setSelected(groups.length)}}>group 추가</Button>
  </div>
  {expression!==undefined&&<section ref={detail} className="output-column-detail" id="group-column-detail" aria-label="선택한 group 상세">
   <div className="output-column-detail-heading"><strong>{expression||'새 group'}</strong><IconButton size="small" aria-label="group 상세 닫기" onClick={()=>setSelected(null)}><X size={16}/></IconButton></div>
   <SqlExpressionInput key={selected} label="그룹 표현식" value={expression} columns={inputs} disabled={disabled} autoFocus={!expression} onChange={value=>onChange(groups.map((g,i)=>i===selected?value:g))}/>
   <Button size="small" color="error" startIcon={<Trash2 size={14}/>} disabled={disabled} onClick={()=>{onChange(groups.filter((_,i)=>i!==selected));setSelected(null)}}>group에서 제외</Button>
  </section>}
 </div>
}
