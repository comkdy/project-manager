import {useState} from 'react';
import {Alert,Autocomplete,Button,Chip,FormControlLabel,Switch,TextField,Tooltip} from '@mui/material';
import {ArrowRight,Columns3,RefreshCw} from 'lucide-react';
import {useQuery} from '@tanstack/react-query';
import {post} from './api';
import type {PivotSpec,Source} from './types';
import {isNumericType,isTimeType,pivotOutputs} from './pivotSettings';
import './pivot-editor.css';

export default function PivotEditor({source,pivot,disabled,onChange}:{source:Source;pivot:PivotSpec;disabled:boolean;onChange:(value:PivotSpec)=>void}){
 const [input,setInput]=useState('');
 const suggestions=useQuery({queryKey:['pivot-values',source.id,pivot.operation_column],
  queryFn:()=>post<{values:string[];truncated:boolean}>(`/sources/${encodeURIComponent(source.id)}/pivot-values`,{column:pivot.operation_column}),
  enabled:!!pivot.operation_column,staleTime:60_000,retry:false});
 const change=(patch:Partial<PivotSpec>)=>onChange({...pivot,...patch});
 const names=source.columns.map(c=>c.name);
 function addValues(values:string[]){change({outputs:pivotOutputs(values,pivot)});setInput('')}
 function field(label:string,value:string,options:string[],onSelect:(value:string)=>void){
  return <Autocomplete size="small" options={options} value={value||null} disabled={disabled} onChange={(_,value)=>onSelect(value??'')} renderInput={params=><TextField {...params} label={label} placeholder="컬럼 선택"/>}/>;
 }
 return <section className="pivot-editor" aria-label="피벗 설정">
  <div className="pivot-intro"><Columns3 size={17}/><div><strong>마지막 값으로 피벗</strong><span>행 기준이 같은 데이터를 모으고, 열 기준의 각 항목을 새 열로 펼칩니다.</span></div></div>
  <Autocomplete multiple size="small" options={names} value={pivot.keys} disabled={disabled} onChange={(_,keys)=>change({keys})} renderInput={params=><TextField {...params} label="행 기준" placeholder={pivot.keys.length?'컬럼 추가':'행을 구분할 컬럼 선택'}/>}/>
  <div className="pivot-fields">
   {field('열 기준',pivot.operation_column,names.filter(n=>!pivot.keys.includes(n)),operation_column=>{change({operation_column,outputs:[]});setInput('')})}
   {field('값',pivot.value_column,source.columns.filter(c=>isNumericType(c.type)&&!pivot.keys.includes(c.name)).map(c=>c.name),value_column=>change({value_column}))}
   {field('시간 기준',pivot.time_column,source.columns.filter(c=>isTimeType(c.type)&&!pivot.keys.includes(c.name)).map(c=>c.name),time_column=>change({time_column}))}
  </div>
  <div className="pivot-operation-heading"><strong>열 항목</strong><Tooltip title="열 항목 목록 다시 조회"><span><Button size="small" disabled={disabled||!pivot.operation_column||suggestions.isFetching} onClick={()=>void suggestions.refetch()} startIcon={<RefreshCw size={12}/>}>목록 새로고침</Button></span></Tooltip></div>
  <Autocomplete multiple freeSolo size="small" options={suggestions.data?.values??[]} filterSelectedOptions value={pivot.outputs.map(item=>item.value)} inputValue={input} onInputChange={(_,value)=>setInput(value)} onChange={(_,values)=>addValues(values)} disabled={disabled||!pivot.operation_column} loading={suggestions.isFetching} loadingText="열 항목 확인 중…" renderInput={params=><TextField {...params} label="표시할 열 항목" placeholder="선택하거나 직접 입력 후 Enter"/>}/>
  {(suggestions.error||suggestions.data?.truncated)&&<span className="pivot-hint">{suggestions.error?'목록을 불러오지 못했습니다. 값을 직접 입력할 수 있습니다.':'목록에는 최대 50개를 표시합니다. 다른 값은 직접 입력할 수 있습니다.'}</span>}
  {!!pivot.outputs.length&&<div className="pivot-output-list"><div className="pivot-output-labels"><span>열 항목</span><span>출력 이름</span></div>{pivot.outputs.map((item,index)=><div className="pivot-output-row" key={item.value}><Chip size="small" label={item.value} onDelete={disabled?undefined:()=>addValues(pivot.outputs.filter((_,i)=>i!==index).map(item=>item.value))}/><ArrowRight size={14}/><TextField size="small" value={item.name} disabled={disabled} onChange={event=>change({outputs:pivot.outputs.map((output,i)=>i===index?{...output,name:event.target.value}:output)})} slotProps={{htmlInput:{'aria-label':`${item.value} 출력 이름`,maxLength:63}}}/></div>)}</div>}
  <details className="pivot-details" open={!pivot.tie_breaker||undefined}><summary>동일 시각·누락 값 설정</summary><div>
   {field('동일 시각 기준',pivot.tie_breaker,names.filter(n=>![...pivot.keys,pivot.operation_column,pivot.value_column,pivot.time_column].includes(n)),tie_breaker=>change({tie_breaker}))}
   <span className="pivot-hint">시각이 같으면 이 컬럼의 큰 값이 우선됩니다. 행을 구분하는 고유 번호·순번을 선택하세요.</span>
   <FormControlLabel control={<Switch size="small" checked={pivot.require_all} disabled={disabled} onChange={(_,require_all)=>change({require_all})}/>} label="선택한 모든 열 항목에 값이 있는 행만"/>
   <span className="pivot-hint">마지막 값이 비어 있으면 이전 값으로 대체하지 않습니다.</span>
  </div></details>
  {pivot.where_sql&&<Alert severity="info">기존 SQL의 행 조건을 피벗 전 원천 데이터에 적용합니다.</Alert>}
 </section>;
}
