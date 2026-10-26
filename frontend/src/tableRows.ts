import type {Column} from './types';

const textOrder=new Intl.Collator('ko',{numeric:true,sensitivity:'base'});

function comparable(value:unknown,type:string):number|string {
 if(typeof value==='number')return value;
 if(typeof value==='boolean')return Number(value);
 if(typeof value==='string'){
  if(/^(smallint|integer|bigint|int\d*|numeric|decimal|real|double precision|float\d*)(\b|\()/i.test(type)&&value.trim()&&Number.isFinite(Number(value)))return Number(value);
  if(/^(date|timestamp)(\b|\()/i.test(type)){
   const time=Date.parse(value);
   if(Number.isFinite(time))return time;
  }
  return value;
 }
 return JSON.stringify(value)??String(value);
}

/** Sort only the loaded page, retaining its original order for ties and reset. */
export function sortTableRows(rows:Record<string,unknown>[],columns:Column[],sort:string,direction:'asc'|'desc'){
 const column=columns.find(c=>c.name===sort);
 if(!column)return rows;
 const sign=direction==='asc'?1:-1;
 return rows.map((row,index)=>({row,index})).sort((a,b)=>{
  const left=a.row[sort],right=b.row[sort];
  // Keep missing values last in either direction, as in the server table query.
  if(left==null||right==null)return left==null&&right==null?a.index-b.index:left==null?1:-1;
  const x=comparable(left,column.type),y=comparable(right,column.type);
  const order=typeof x==='number'&&typeof y==='number'?(x<y?-1:x>y?1:0):textOrder.compare(String(x),String(y));
  return order*sign||a.index-b.index;
 }).map(item=>item.row);
}
