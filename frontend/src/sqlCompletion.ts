import {columnExpression} from './sqlIdentifiers.ts';

export type CompletionRange={start:number;end:number;prefix:string};

// Complete the identifier at the caret, never text inside a SQL string literal.
export function completionRange(text:string,start:number,end=start):CompletionRange|null{
 let single=false,quoted=false,quoteStart=-1,closedStart=-1;
 for(let i=0;i<start;i++){
  if(single){if(text[i]==="'"){if(text[i+1]==="'"&&i+1<start)i++;else single=false}continue}
  if(quoted){if(text[i]==='"'){if(text[i+1]==='"'&&i+1<start)i++;else{quoted=false;if(i===start-1)closedStart=quoteStart}}continue}
  if(text[i]==="'")single=true;
  if(text[i]==='"'){quoted=true;quoteStart=i}
 }
 if(single)return null;
 if(start!==end)return {start,end,prefix:''};
 if(closedStart>=0)return {start:closedStart,end:start,prefix:text.slice(closedStart+1,start-1).replace(/""/g,'"')};
 let from=start,to=start;
 if(quoted){
  from=quoteStart;
  while(to<text.length){if(text[to]==='"'){if(text[to+1]==='"'){to+=2;continue}to++;break}to++}
  return {start:from,end:to,prefix:text.slice(from+1,start).replace(/""/g,'"')};
 }
 if(text[start]==='"'){
  to=start+1;
  while(to<text.length){if(text[to]==='"'){if(text[to+1]==='"'){to+=2;continue}to++;break}to++}
  return {start,end:to,prefix:''};
 }
 while(from>0&&/[A-Za-z0-9_]/.test(text[from-1]))from--;
 while(to<text.length&&/[A-Za-z0-9_]/.test(text[to]))to++;
 const prefix=text.slice(from,start);
 if(prefix&&/^\d/.test(prefix))return null;
 return {start:from,end:to,prefix};
}

export function insertCompletion(text:string,range:CompletionRange,name:string){
 const replacement=columnExpression(name);
 return {text:text.slice(0,range.start)+replacement+text.slice(range.end),caret:range.start+replacement.length};
}
