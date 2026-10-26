type FlagItem={x:number;label:string};
/** Two non-overlapping lanes; dense neighbours remain discoverable in a group. */
export function layoutKeyFlags<T extends FlagItem>(items:readonly T[],left:number,right:number){
 const result:{x:number;labelX:number;width:number;lane:number;items:T[]}[]=[];
 const ends=[left-4,left-4],available=Math.max(1,right-left);
 for(const item of [...items].sort((a,b)=>a.x-b.x)){
  const width=Math.min(available,120,Math.max(62,14+[...item.label].reduce((n,c)=>n+(/[\u0000-\u007f]/.test(c)?6:11),0)));
  const natural=Math.max(left,Math.min(item.x,right-width));
  const lane=ends.findIndex(end=>natural>=end+4);
  if(lane>=0){result.push({x:item.x,labelX:natural,width,lane,items:[item]});ends[lane]=natural+width}
  else {
   const nearest=result.reduce((best,group)=>Math.abs(group.x-item.x)<Math.abs(best.x-item.x)?group:best);
   nearest.items.push(item);
  }
 }
 return result;
}

export function flagLabel(label:string,count:number,width:number){
 const suffix=count>1?` +${count-1}`:'';
 const capacity=Math.max(0,width-14-suffix.length*6);
 if([...label].reduce((n,c)=>n+(/[\u0000-\u007f]/.test(c)?6:11),0)<=capacity)return label+suffix;
 let used=0,text='';
 for(const char of label){const size=/[\u0000-\u007f]/.test(char)?6:11;if(used+size>capacity-6)return text+'…'+suffix;text+=char;used+=size}
 return text+suffix;
}
