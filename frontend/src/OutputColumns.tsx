import {useEffect,useRef,useState} from 'react';
import {Button,Chip,IconButton,TextField} from '@mui/material';
import {Plus,Trash2,X} from 'lucide-react';
import type {Column} from './types';
import {defaultOutputName,outputName,type EditorColumn} from './datasetColumns';
import SqlExpressionInput from './SqlExpressionInput';

export default function OutputColumns({columns,inputs,disabled,onChange}:{columns:EditorColumn[];inputs:Column[];disabled:boolean;onChange:(columns:EditorColumn[])=>void}){
 const [selected,setSelected]=useState<number|null>(null);
 const detail=useRef<HTMLElement>(null);
 useEffect(()=>{detail.current?.scrollIntoView({block:'nearest'})},[selected]);
 const column=selected===null?undefined:columns[selected];
 function change(patch:Partial<EditorColumn>){
  onChange(columns.map((c,i)=>i===selected?{...c,...patch}:c));
 }
 function add(){onChange([...columns,{name:'',expression:''}]);setSelected(columns.length)}
 function remove(){onChange(columns.filter((_,i)=>i!==selected));setSelected(null)}
 return <div className="output-columns">
  <div className="output-column-buttons" role="group" aria-label="column 목록">
   {columns.map((c,i)=><Chip component="button" type="button" key={i} label={outputName(c)||'새 컬럼'} variant={selected===i?'filled':'outlined'} color={selected===i?'primary':'default'} disabled={disabled} onClick={()=>setSelected(selected===i?null:i)} aria-label={`${outputName(c)||'새 컬럼'} 상세 편집`} aria-expanded={selected===i} aria-controls={selected===i?'output-column-detail':undefined}/>)}
   <Button size="small" startIcon={<Plus size={14}/>} disabled={disabled||columns.length>=100} onClick={add}>column 추가</Button>
  </div>
  {column?<section ref={detail} className="output-column-detail" id="output-column-detail" aria-label="선택한 컬럼 상세">
   <div className="output-column-detail-heading"><strong>{outputName(column)||'새 컬럼'}</strong><IconButton size="small" aria-label="컬럼 상세 닫기" onClick={()=>setSelected(null)}><X size={16}/></IconButton></div>
   <SqlExpressionInput key={selected} value={column.expression} columns={inputs} disabled={disabled} autoFocus={!column.expression} onChange={expression=>change({expression})}/>
   <TextField fullWidth label="출력 이름 (선택)" value={column.name} placeholder={defaultOutputName(column.expression)||'표현식을 그대로 사용'} disabled={disabled} onChange={e=>change({name:e.target.value})} slotProps={{inputLabel:{shrink:true}}}/>
   <Button size="small" color="error" startIcon={<Trash2 size={14}/>} disabled={disabled||columns.length===1} onClick={remove}>출력에서 제외</Button>
  </section>:null}
 </div>
}
