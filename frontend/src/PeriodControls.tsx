import {useState} from 'react';
import {Alert,Button,FormControlLabel,MenuItem,Popover,Switch,TextField} from '@mui/material';
import {CalendarDays,ChevronDown} from 'lucide-react';
import {useQuery} from '@tanstack/react-query';
import {api} from './api';
import type {CardPeriod,CardSpec,ISOWeek,QueryContext} from './types';
import {recentFourWeeks,weekMonday,weeksInYear} from './dashboardPeriod';
import {ErrorNotice} from './components';
import './period-controls.css';

export function DashboardPeriodPicker({value,onChange}:{value:ISOWeek;onChange:(v:ISOWeek)=>void}){
 const [anchor,setAnchor]=useState<HTMLElement|null>(null);
 const [draft,setDraft]=useState(value);
 const close=()=>setAnchor(null);
 return <>
  <Button className="board-period-button" color="secondary" startIcon={<CalendarDays size={15}/>} endIcon={<ChevronDown size={13}/>} aria-label={`조회 기간 설정: ${value.year}년 WW${String(value.week).padStart(2,'0')}부터`} aria-haspopup="dialog" aria-expanded={!!anchor} onClick={event=>{setDraft(value);setAnchor(event.currentTarget)}}>
   <span className="board-period-label">조회 기간</span><strong>{value.year} · WW{String(value.week).padStart(2,'0')}</strong><span className="board-period-until">부터</span>
  </Button>
  <Popover open={!!anchor} anchorEl={anchor} onClose={close} anchorOrigin={{vertical:'bottom',horizontal:'right'}} transformOrigin={{vertical:'top',horizontal:'right'}} slotProps={{paper:{className:'board-period-popover',role:'dialog','aria-label':'조회 기간 설정'}}}>
   <div className="board-period-title"><CalendarDays size={16}/><strong>조회 기간</strong><Button size="small" onClick={()=>{onChange(recentFourWeeks());close()}}>최근 4주</Button></div>
   <WeekPicker value={draft} onChange={setDraft}/>
   <div className="board-period-bottom"><span>{weekMonday(draft).replaceAll('-','.')}부터 현재까지</span><Button size="small" variant="contained" onClick={()=>{onChange(draft);close()}}>적용</Button></div>
  </Popover>
 </>;
}

export function WeekPicker({value,onChange,label='조회 시작'}:{value:ISOWeek;onChange:(v:ISOWeek)=>void;label?:string}){
 const [yearText,setYearText]=useState(String(value.year));
 const [lastYear,setLastYear]=useState(value.year);
 if(lastYear!==value.year){setLastYear(value.year);setYearText(String(value.year))}
 const year=Number(yearText),valid=Number.isInteger(year)&&year>=1900&&year<=9998;
 function commitYear(){if(valid)onChange({year,week:Math.min(value.week,weeksInYear(year))});else setYearText(String(value.year))}
 return <div className="week-picker" aria-label={label} title={`${weekMonday(value)}부터 · ISO 주차 · 한국 시간`}>
  <span>{label}</span>
  <TextField size="small" label="연도" value={yearText} onChange={e=>setYearText(e.target.value)} onBlur={commitYear} onKeyDown={e=>{if(e.key==='Enter'){commitYear();e.currentTarget.blur()}}} error={!valid} slotProps={{htmlInput:{inputMode:'numeric',maxLength:4,'aria-label':`${label} 연도`}}}/>
  <TextField select size="small" label="WW" value={value.week} onChange={e=>onChange({...value,week:Number(e.target.value)})} slotProps={{select:{inputProps:{'aria-label':`${label} WW`}}}}>
   {Array.from({length:weeksInYear(value.year)},(_,i)=><MenuItem key={i+1} value={i+1}>WW{String(i+1).padStart(2,'0')}</MenuItem>)}
  </TextField>
 </div>
}

interface PeriodColumn {source_id:string;column:string;label:string;type:string}
export function CommonPeriodControl({period,context,onChange}:{period:CardPeriod;context:QueryContext;onChange:(p:CardPeriod)=>void}){
 return <>
  <FormControlLabel control={<Switch size="small" checked={period.use_dashboard} onChange={(_,checked)=>onChange({...period,use_dashboard:checked})}/>} label="공통 기간 적용"/>
  {period.use_dashboard&&context.period_start&&<span className="period-value">{context.period_start.year} · WW{String(context.period_start.week).padStart(2,'0')}부터</span>}
 </>;
}

export function CardPeriodControls({card,context,onChange}:{card:CardSpec;context:QueryContext;onChange:(p:CardPeriod)=>void}){
 const period=card.period??{use_dashboard:true};
 const columns=useQuery({queryKey:['period-columns',card.dataset_id,card.dataset_version,context.project_id],queryFn:()=>api<{columns:PeriodColumn[];default:PeriodColumn|null}>(`/datasets/${encodeURIComponent(card.dataset_id)}/period-columns${context.project_id!=null?`?project_id=${context.project_id}`:''}`),enabled:!!card.dataset_id});
 const key=(c:PeriodColumn)=>JSON.stringify([c.source_id,c.column]);
 const selected=columns.data?.columns.find(c=>c.source_id===period.source_id&&c.column===period.column);
 return <div className="card-period-controls">
  <CommonPeriodControl period={period} context={context} onChange={onChange}/>
  {period.use_dashboard&&<TextField select fullWidth size="small" label="기간 기준 컬럼" slotProps={{select:{displayEmpty:true},inputLabel:{shrink:true}}} value={selected?key(selected):''} onChange={e=>{const c=columns.data?.columns.find(c=>key(c)===e.target.value);onChange({...period,source_id:c?.source_id??null,column:c?.column??null})}} disabled={columns.isPending}>
   <MenuItem value="">{columns.data?.default?`자동 · ${columns.data.default.label}`:'기준 컬럼 선택'}</MenuItem>
   {columns.data?.columns.map(c=><MenuItem key={key(c)} value={key(c)}>{c.label}</MenuItem>)}
  </TextField>}
  {period.use_dashboard&&columns.data?.columns.length===0&&<Alert severity="warning">날짜·시간 컬럼이 없어 기간을 적용할 수 없습니다.</Alert>}
  <ErrorNotice error={columns.error}/>
 </div>
}
