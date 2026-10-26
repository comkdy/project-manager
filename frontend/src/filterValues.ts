import type {Filter} from './types';

const numericType=/^(smallint|integer|bigint|int[248]?|smallserial|serial|bigserial|numeric|decimal|real|double precision|float[48]?)(\b|\()/i;
export const isListFilter=(op:Filter['op'])=>op==='in'||op==='not_in';

function scalar(text:string,type:string):unknown {
 if(!numericType.test(type)||!text.trim())return text;
 if(!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(text.trim()))throw new Error('숫자를 입력하세요.');
 const value=Number(text);
 if(!Number.isFinite(value))throw new Error('유효한 숫자를 입력하세요.');
 // Keep large IDs and precise decimals as strings for PostgreSQL to convert.
 const digits=text.replace(/[eE].*$/,'').replace(/\D/g,'').replace(/^0+/,'');
 return (Number.isInteger(value)&&!Number.isSafeInteger(value))||digits.length>15||(value===0&&/[1-9]/.test(digits))?text.trim():value;
}

function list(text:string):string[] {
 if(!text.trim())return [];
 const values:string[]=[];
 let index=0;
 while(index<text.length){
  while(/\s/.test(text[index]??'')&&index<text.length)index++;
  let value='';
  if(text[index]==='"'){
   index++;let closed=false;
   while(index<text.length){
    if(text[index]==='"'){
     if(text[index+1]==='"'){value+='"';index+=2;continue;}
     index++;closed=true;break;
    }
    value+=text[index++];
   }
   if(!closed)throw new Error('값의 큰따옴표를 닫아 주세요.');
   while(/\s/.test(text[index]??'')&&index<text.length)index++;
   if(index<text.length&&text[index]!==',')throw new Error('값은 쉼표로 구분하세요.');
  }else{
   while(index<text.length&&text[index]!==',')value+=text[index++];
   value=value.trim();
   if(!value)throw new Error('쉼표 사이에 값을 입력하세요.');
  }
  values.push(value);
  if(index<text.length){index++;if(!text.slice(index).trim())throw new Error('쉼표 뒤에 값을 입력하세요.');}
 }
 if(values.length>100)throw new Error('값은 100개까지 입력할 수 있습니다.');
 return values;
}

export function parseFilterValue(text:string,op:Filter['op'],type:string):{value:unknown;error:string|null} {
 try{return {value:isListFilter(op)?list(text).map(value=>scalar(value,type)):op==='contains'?text:scalar(text,type),error:null};}
 // Never submit a partially parsed list or silently replace an invalid number with null.
 catch(error){return {value:text,error:(error as Error).message};}
}

export function formatFilterValue(value:unknown,op:Filter['op']):string {
 if(!isListFilter(op)||!Array.isArray(value))return String(value??'');
 return value.map(item=>{
  const text=String(item??'');
  return !text||/[,"\r\n]/.test(text)||text.trim()!==text?'"'+text.replace(/"/g,'""')+'"':text;
 }).join(', ');
}

export function changeFilterOperator(filter:Filter,op:Filter['op'],type:string):Filter {
 const value=isListFilter(op)?(Array.isArray(filter.value)?filter.value:filter.value===''?[]:[filter.value])
  :Array.isArray(filter.value)?filter.value[0]??'':filter.value;
 return {...filter,op,value:parseFilterValue(formatFilterValue(value,op),op,type).value};
}
