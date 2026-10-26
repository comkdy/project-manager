import type {ChartResult,ISOWeek} from './types.ts';

const DAY=86400000;
export function isoWeek(date:Date):ISOWeek {
 const d=new Date(Date.UTC(date.getUTCFullYear(),date.getUTCMonth(),date.getUTCDate()));
 d.setUTCDate(d.getUTCDate()+4-(d.getUTCDay()||7));
 const year=d.getUTCFullYear();
 return {year,week:Math.ceil(((d.getTime()-Date.UTC(year,0,1))/DAY+1)/7)};
}
export function recentFourWeeks(now=new Date()):ISOWeek {
 // Calendar dates in Korea, regardless of the viewer computer's time zone.
 return isoWeek(new Date(now.getTime()+9*3600000-21*DAY));
}
export function weeksInYear(year:number){return isoWeek(new Date(Date.UTC(year,11,28))).week}
export function weekMonday(value:ISOWeek){
 const d=new Date(Date.UTC(value.year,0,4));
 d.setUTCDate(d.getUTCDate()-(d.getUTCDay()||7)+1+(value.week-1)*7);
 return d.toISOString().slice(0,10);
}
export function validWeek(value:unknown):value is ISOWeek {
 const v=value as ISOWeek|undefined;
 return !!v&&Number.isInteger(v.year)&&v.year>=1900&&v.year<=9998&&Number.isInteger(v.week)&&v.week>=1&&v.week<=weeksInYear(v.year);
}
export function loadPeriod(key:string):ISOWeek {
 try {const saved=JSON.parse(sessionStorage.getItem(key)??'null');if(validWeek(saved))return saved}catch{}
 return recentFourWeeks();
}

export function chartPeriodDescription(result?:ChartResult):string {
 const period=result?.meta.period as {start?:ISOWeek;label?:string;column?:string}|undefined;
 if(!period?.start)return '기간 제한 없음';
 return `${period.start.year} · WW${String(period.start.week).padStart(2,'0')}부터 · ${period.label??period.column??'선택한 시각'} 기준`;
}
