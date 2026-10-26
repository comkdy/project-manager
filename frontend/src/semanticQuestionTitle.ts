import type {KnowledgeQuestion} from './semanticCollectionTypes';

const escape=(text:string)=>text.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
const quoted=/('[^']*'|"[^"]*"|‘[^’]*’|“[^”]*”)/g;
const identifier=(name:string)=>new RegExp(`(?<![A-Za-z0-9_.])${escape(name)}(?![A-Za-z0-9_])`,'g');
const particles:Record<string,string>={는:'은',가:'이',를:'을',와:'과',로:'으로',랑:'이랑',라는:'이라는',라고:'이라고'};
const columnLabel=(target:KnowledgeQuestion['targets'][number])=>`${target.column}${target.source_id?' 원천':''} 컬럼`;

// Add only schema-backed labels. Never rewrite quoted values as column names.
export function questionTitle(question:Pick<KnowledgeQuestion,'title'|'targets'>):string{
 const columns=[...new Map(question.targets.filter(t=>t.kind!=='dataset').map(t=>[t.column,t])).values()];
 let title=question.title.split(quoted).map((part,index)=>{
  if(index%2)return part;
  for(const target of columns){
   const pattern=new RegExp(`(?<![A-Za-z0-9_.])(?:(?:원천\\s+)?(?:컬럼|칼럼|column)\\s+)?${escape(target.column)}(?![A-Za-z0-9_])(?:\\s+(?:원천\\s+)?(?:컬럼|칼럼|column))?(으로|로|이라는|라는|이라고|라고|이랑|랑|은|는|이|가|을|를|과|와)?`,'gi');
   part=part.replace(pattern,(match,particle:string|undefined,offset:number,text:string)=>{
    const suffix=particle?particles[particle]??particle:/^\s+값/.test(text.slice(offset+match.length))?'의':'';
    return `${columnLabel(target)}${suffix}`;
   });
  }
  return part.replace(/판정되며\s+/g,'판정되며, ').replace(/(컬럼의 값)과\s+어떤 관계/g,'$1과는 어떤 관계');
 }).join('');
 const values=[...new Map(question.targets.filter(t=>t.kind==='column_value').map(t=>[t.value,t])).values()];
 for(const target of values){
  const pattern=new RegExp(`${quoted.source}|(?<![A-Za-z0-9_.])${escape(target.value)}(?![A-Za-z0-9_])`,'g');
  const columnMentioned=title.split(quoted).some((part,index)=>index%2===0&&identifier(target.column).test(part));
  title=title.replace(pattern,(value,literal:string|undefined,offset:number,text:string)=>{
   if(literal&&literal.slice(1,-1)!==target.value)return value;
   if(!literal&&columns.some(t=>t.column===value))return value;
   const before=text.slice(0,offset),after=text.slice(offset+value.length);
   const labeled=/(?:값|value)\s*$/i.test(before)||/^\s*(?:값|value)/i.test(after);
   return `${columnMentioned?'':`${target.column} 컬럼의 `}${labeled?'':'값 '}${value}`;
  });
 }
 return title;
}

export interface QuestionTitlePart {text:string;column?:string;sourceName?:string}

// Keep the column name and its label together visually without adding a row.
export function questionTitleParts(question:Pick<KnowledgeQuestion,'title'|'targets'>):QuestionTitlePart[]{
 const columns=[...new Map(question.targets.filter(t=>t.kind!=='dataset').map(t=>[columnLabel(t),t])).entries()].sort((a,b)=>b[0].length-a[0].length);
 if(!columns.length)return [{text:questionTitle(question)}];
 const pattern=new RegExp(`(?<![A-Za-z0-9_.])(?:${columns.map(([label])=>escape(label)).join('|')})(?![A-Za-z0-9_])`,'g');
 return questionTitle(question).split(quoted).flatMap((part,index)=>{
  if(index%2)return [{text:part}];
  const result:QuestionTitlePart[]=[];
  let start=0;
  for(const match of part.matchAll(pattern)){
   if(match.index>start)result.push({text:part.slice(start,match.index)});
   const target=columns.find(([label])=>label===match[0])![1];
   result.push({text:match[0],column:target.column,sourceName:target.source_name});
   start=match.index+match[0].length;
  }
  if(start<part.length)result.push({text:part.slice(start)});
  return result;
 });
}
