import {Alert,Button,IconButton,MenuItem,TextField,Tooltip} from '@mui/material';
import {Plus,Trash2,Database,ArrowRight,ArrowUp,ArrowDown,ArrowUpDown,LoaderCircle} from 'lucide-react';
import {flexRender,getCoreRowModel,useReactTable,type ColumnDef} from '@tanstack/react-table';
import {useMemo} from 'react';
import type {Column,Filter} from './types';
import FilterValueInput from './FilterValueInput';
import {changeFilterOperator,formatFilterValue,parseFilterValue} from './filterValues';
export function ErrorNotice({error}:{error:unknown}){return error?<Alert severity="error" sx={{my:1}}>{error instanceof Error?error.message:String(error)}</Alert>:null}
export function Loading({label='데이터를 불러오는 중'}:{label?:string}){return <div className="loading-state"><LoaderCircle size={25} className="spin"/><span>{label}</span></div>}
export function Empty({title,description,action}:{title:string;description:string;action?:React.ReactNode}){return <div className="empty-state"><div className="empty-icon"><Database size={28}/></div><h3>{title}</h3><p>{description}</p>{action}</div>}
export function DataGrid({columns,rows,maxHeight=370,sort,direction='asc',onSort,loading=false}:{columns:Column[];rows:Record<string,unknown>[];maxHeight?:number|string;sort?:string;direction?:'asc'|'desc';onSort?:(column:string)=>void;loading?:boolean}){
 const defs=useMemo<ColumnDef<Record<string,unknown>>[]>(()=>columns.map(c=>({id:c.name,accessorFn:row=>row[c.name],header:()=> <span>{c.name}<small>{c.type}</small></span>,cell:info=>{const value=info.getValue();return value==null?<span className="null-value">NULL</span>:typeof value==='number'?value.toLocaleString('en-US',{maximumFractionDigits:5}):typeof value==='object'?JSON.stringify(value):String(value)}})),[columns]);
 const table=useReactTable({data:rows,columns:defs,getCoreRowModel:getCoreRowModel()});
 return <div className="data-table-wrap" style={{maxHeight}} aria-busy={loading}><table className="data-table"><thead>{table.getHeaderGroups().map(group=><tr key={group.id}>{group.headers.map(header=>{
  const active=sort===header.column.id,SortIcon=active?(direction==='asc'?ArrowUp:ArrowDown):ArrowUpDown;
  const action=active?(direction==='asc'?'내림차순 정렬':'정렬 해제 (기본 순서)'):'오름차순 정렬';
  const label=flexRender(header.column.columnDef.header,header.getContext());
  return <th key={header.id} scope="col" className={onSort?'sortable-column':undefined} aria-sort={onSort?(active?(direction==='asc'?'ascending':'descending'):'none'):undefined}>{onSort?<button type="button" className={`column-sort ${active?'active':''}`} onClick={()=>onSort(header.column.id)} aria-label={`${header.column.id}: ${action}`} title={action}>{label}<SortIcon size={13} aria-hidden="true"/></button>:label}</th>
 })}</tr>)}</thead><tbody>{table.getRowModel().rows.map(row=><tr key={row.id}>{row.getVisibleCells().map(cell=><td key={cell.id}>{flexRender(cell.column.columnDef.cell,cell.getContext())}</td>)}</tr>)}</tbody></table>{loading?<Loading/>:!rows.length&&<div className="table-empty">조건에 맞는 데이터가 없습니다.</div>}</div>
}
export function FilterBuilder({columns,filters,onChange,disabled=false}:{columns:Column[];filters:Filter[];onChange:(f:Filter[])=>void;disabled?:boolean}){
 function change(i:number,patch:Partial<Filter>){onChange(filters.map((f,j)=>i===j?{...f,...patch}:f))}
 return <div className="filter-builder">{filters.map((f,i)=>{
  const type=columns.find(c=>c.name===f.column)?.type??'';
  return <div className="filter-row" key={i}>
   <TextField disabled={disabled} select label="컬럼" value={f.column} onChange={e=>{
    const column=e.target.value,type=columns.find(c=>c.name===column)?.type??'';
    change(i,{column,value:parseFilterValue(formatFilterValue(f.value,f.op),f.op,type).value});
   }}>{columns.map(c=><MenuItem key={c.name} value={c.name}>{c.name}</MenuItem>)}</TextField>
   <TextField disabled={disabled} select label="조건" value={f.op} onChange={e=>change(i,changeFilterOperator(f,e.target.value as Filter['op'],type))}>
    {[['eq','='],['ne','≠'],['gt','>'],['gte','≥'],['lt','<'],['lte','≤'],['in','포함 (IN)'],['not_in','포함하지 않음 (NOT IN)'],['contains','문자열 포함']].map(([value,label])=><MenuItem value={value} key={value}>{label}</MenuItem>)}
   </TextField>
   <FilterValueInput key={`${f.column}:${f.op}`} filter={f} type={type} disabled={disabled} onChange={value=>change(i,{value})}/>
   <Tooltip title="조건 삭제"><IconButton disabled={disabled} aria-label="조건 삭제" onClick={()=>onChange(filters.filter((_,j)=>i!==j))}><Trash2 size={16}/></IconButton></Tooltip>
  </div>;
 })}<Button size="small" startIcon={<Plus size={14}/>} disabled={disabled||!columns.length||filters.length>=30} onClick={()=>onChange([...filters,{column:columns[0]?.name??'',op:'eq',value:''}])}>필터 추가</Button></div>
}
export function SectionLabel({children,action}:{children:React.ReactNode;action?:React.ReactNode}){return <div className="section-label"><span>{children}</span>{action}</div>}
export function ArrowLabel({children}:{children:React.ReactNode}){return <span className="arrow-label">{children}<ArrowRight size={14}/></span>}
