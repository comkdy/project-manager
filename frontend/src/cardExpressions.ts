import type {CardSpec,Column,ValueToken} from './types';

export const expressionNumber=/^(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$/;
export const numericColumn=(c:Column)=>['smallint','integer','bigint','numeric','real','double precision','decimal'].includes(c.type);
export const tokenLabel=(token:ValueToken)=>token.kind==='column'?token.name:token.kind==='operator'?(({'*':'×','/':'÷'} as Record<string,string>)[token.value]??token.value):token.value;
export const expressionLabel=(tokens:ValueToken[])=>tokens.map(tokenLabel).join(' ');
export const expressionName=(card:CardSpec)=>card.value_expression_name?.trim()||'직접 계산식';
export const expressionText=(tokens:ValueToken[])=>tokens.map(t=>t.kind==='column'?`[${t.name.replace(/]/g,']]')}]`:t.value).join(' ');
export const supportsExpression=(kind:CardSpec['kind'])=>kind!=='histogram';
export const isPercent=(card:CardSpec)=>card.aggregate==='yield'||card.options.value_format==='percent';
export const yieldExpression=():ValueToken[]=>[{kind:'column',name:'passed_dies'},{kind:'operator',value:'/'},{kind:'column',name:'tested_dies'},{kind:'operator',value:'*'},{kind:'number',value:'100'}];

/** Convert legacy yield only in the editor draft, preserving weighted yield. */
export function editableValueCard(card:CardSpec):CardSpec {
 if(card.aggregate!=='yield'||!supportsExpression(card.kind))return card;
 return {...card,value_expression:yieldExpression(),value_expression_name:card.value_expression_name?.trim()||'수율',aggregate:'sum',...(card.kind==='wafer'?{color:undefined}:{y:undefined}),options:{...card.options,value_format:'percent'}};
}

/** Partial syntax permits an unfinished final operand while the user is typing. */
export function expressionIssue(tokens:ValueToken[],columns:Column[],partial=false):string|null {
 if(tokens.length>100)return '계산식은 최대 100개 항목까지 입력할 수 있습니다.';
 const names=new Set(columns.filter(numericColumn).map(c=>c.name));
 let operand=true,depth=0,hasColumn=false,unary=false;
 for(const token of tokens){
  if(token.kind==='column'||token.kind==='number'){
   if(!operand)return '컬럼과 숫자 사이에 연산자를 넣어 주세요.';
   if(token.kind==='column'){
    if(!names.has(token.name))return `현재 데이터셋에 숫자형 컬럼 ${token.name}이 없습니다.`;
    hasColumn=true;
   }else if(token.value.length>32||!expressionNumber.test(token.value))return '올바른 숫자를 입력하세요.';
   operand=false;unary=false;
  }else if(token.value==='('){
   if(!operand||++depth>20)return '괄호 앞에 연산자를 넣거나 괄호 수를 줄여 주세요.';
   unary=false;
  }else if(token.value===')'){
   if(operand||depth===0)return '닫는 괄호의 위치를 확인하세요.';
   depth--;unary=false;
  }else if(['+','-','*','/'].includes(token.value)){
   if(operand){
    if(!['+','-'].includes(token.value)||unary)return '이 위치에는 컬럼 또는 숫자를 넣어 주세요.';
    unary=true;
   }else{operand=true;unary=false}
  }else return '사칙연산과 괄호만 사용할 수 있습니다.';
 }
 if(partial)return null;
 if(!tokens.length||operand)return '계산식에 컬럼 또는 숫자를 추가하세요.';
 if(depth)return '열린 괄호를 닫아 주세요.';
 if(!hasColumn)return '데이터셋 컬럼을 하나 이상 추가하세요.';
 return null;
}

export function columnSuggestions(text:string,columns:Column[]):Column[]{
 const query=text.trim().toLocaleLowerCase();
 return columns.filter(numericColumn).filter(c=>c.name.toLocaleLowerCase().includes(query)).sort((a,b)=>Number(b.name.toLocaleLowerCase().startsWith(query))-Number(a.name.toLocaleLowerCase().startsWith(query)));
}

export function allowedDraft(text:string,columns:Column[]):boolean {
 return text.length<=160&&(text===''||(/^\d*\.?\d*$/.test(text)&&text.length<=32)||columnSuggestions(text,columns).length>0);
}

/** Whole-paste validation is atomic: unsupported input is never partly accepted. */
export function parseExpressionText(text:string,columns:Column[]):ValueToken[] {
 if(text.length>5000||/--|\/\*|\*\/|\/\/|\*\*/.test(text))throw new Error('지원하지 않는 문자열은 입력할 수 없습니다.');
 const tokens:ValueToken[]=[],names=columns.filter(numericColumn).map(c=>c.name).sort((a,b)=>b.length-a.length);
 let at=0;
 while(at<text.length){
  if(/\s/.test(text[at])){at++;continue}
  const rest=text.slice(at);
  if(rest[0]==='['){
   const match=rest.match(/^\[((?:[^\]]|\]\])+)\]/);
   const name=match?.[1].replace(/]]/g,']');
   if(!match||!name||!names.includes(name))throw new Error('현재 데이터셋의 숫자형 컬럼만 입력할 수 있습니다.');
   tokens.push({kind:'column',name});at+=match[0].length;continue;
  }
  const symbol=({'×':'*','÷':'/'} as Record<string,string>)[rest[0]]??rest[0];
  if(['+','-','*','/','(',')'].includes(symbol)){
   tokens.push({kind:'operator',value:symbol as '+'});at++;continue;
  }
  const name=names.find(n=>/^[\p{L}_][\p{L}\p{N}_]*$/u.test(n)&&rest.startsWith(n)&&!/[\p{L}\p{N}_]/u.test(rest[n.length]??''));
  if(name){tokens.push({kind:'column',name});at+=name.length;continue}
  const number=rest.match(/^(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)/)?.[0];
  if(number){tokens.push({kind:'number',value:number});at+=number.length;continue}
  throw new Error('컬럼, 숫자, + − × ÷와 괄호만 입력할 수 있습니다.');
 }
 const issue=expressionIssue(tokens,columns,true);
 if(issue)throw new Error(issue);
 return tokens;
}
