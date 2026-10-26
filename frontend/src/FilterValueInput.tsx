import {useEffect,useRef,useState} from 'react';
import {TextField} from '@mui/material';
import type {Filter} from './types';
import {formatFilterValue,isListFilter,parseFilterValue} from './filterValues';

export default function FilterValueInput({filter,type,disabled,onChange}:{filter:Filter;type:string;disabled:boolean;onChange:(value:unknown)=>void}){
 const [text,setText]=useState(()=>formatFilterValue(filter.value,filter.op));
 const [error,setError]=useState<string|null>(()=>parseFilterValue(formatFilterValue(filter.value,filter.op),filter.op,type).error);
 const lastValue=useRef(filter.value);
 useEffect(()=>{
  if(lastValue.current!==filter.value){
   lastValue.current=filter.value;
   const text=formatFilterValue(filter.value,filter.op);
   setText(text);setError(parseFilterValue(text,filter.op,type).error);
  }
 },[filter.value,filter.op]);
 return <TextField disabled={disabled} label={isListFilter(filter.op)?'값 (쉼표로 구분)':'값'}
  value={text} error={!!error} helperText={error} onChange={event=>{
   const text=event.target.value,result=parseFilterValue(text,filter.op,type);
   setText(text);setError(result.error);lastValue.current=result.value;onChange(result.value);
  }}/>;
}
