type TimedOperation={start_at:string|null;end_at:string|null;current_expected_end:string|null;status:'completed'|'current'|'upcoming'};
const timestamp=(value:string|null|undefined)=>{
 const parsed=value?Date.parse(value):NaN;
 return Number.isFinite(parsed)?parsed:null;
};

/** Display a major operation through the next major start; keep source measurements intact. */
export function operationIntervals<T extends TimedOperation>(route:readonly T[],asOf:number,planEnd:string|null=null){
 return route.map((row,index)=>{
  const start=timestamp(row.start_at),ownEnd=timestamp(row.current_expected_end??row.end_at);
  const next=route[index+1],targetEnd=timestamp(next?next.start_at:planEnd);
  // Missing/invalid timing must not invent an interval or shorten an actual operation.
  const extended=start!==null&&ownEnd!==null&&ownEnd>=start&&targetEnd!==null&&targetEnd>ownEnd;
  const end=extended?targetEnd:ownEnd;
  // The major step may be complete while its following hidden steps are still running.
  const status=extended&&row.status==='completed'&&end!>asOf?'current':row.status;
  return {row,start,end,status,extended,next:extended&&next?next:null,toPlanEnd:extended&&!next,
   hours:start!==null&&end!==null&&end>=start?(end-start)/3600000:null};
 });
}

/** Merge touching fills, while retaining separate milestone markers in the view. */
export function ribbonSegments(intervals:readonly {start:number|null;end:number|null;status:TimedOperation['status']}[]){
 const result:{start:number;end:number;status:TimedOperation['status']}[]=[];
 for(const interval of intervals){
  if(interval.start===null||interval.end===null||interval.end<=interval.start)continue;
  const previous=result.at(-1);
  if(previous&&previous.status===interval.status&&interval.start<=previous.end)
   previous.end=Math.max(previous.end,interval.end);
  else result.push({start:interval.start,end:interval.end,status:interval.status});
 }
 return result;
}
