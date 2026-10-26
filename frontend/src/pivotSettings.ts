import type {DatasetSpec,PivotSpec,Source} from './types.ts';

export const isNumericType=(type:string)=>/^(smallint|integer|bigint|int[248]?|numeric|decimal|real|double precision|float[48]?)(\([^)]*\))?$/i.test(type);
export const isTimeType=(type:string)=>/^(date|timestamptz(\(\d+\))?|timestamp(\(\d+\))?( with(out)? time zone)?)$/i.test(type);

export function initialPivot(source:Source,spec:DatasetSpec):PivotSpec{
 const names=new Set(source.columns.map(c=>c.name));
 return {keys:['lot_code','wf_id','die_x','die_y'].filter(key=>names.has(key)),
  operation_column:names.has('operation')?'operation':'',value_column:names.has('value')?'value':'',
  time_column:source.columns.find(c=>c.name==='measured_at'&&isTimeType(c.type))?.name??(source.columns.filter(c=>isTimeType(c.type)).length===1?source.columns.find(c=>isTimeType(c.type))!.name:''),
  tie_breaker:source.primary_key??'',outputs:[],require_all:true,filters:structuredClone(spec.filters),where_sql:spec.where_sql??null};
}

export function pivotOutputs(values:string[],pivot:PivotSpec){
 const existing=new Map(pivot.outputs.map(item=>[item.value,item]));
 const used=new Set([...pivot.keys,...pivot.outputs.filter(item=>values.includes(item.value)).map(item=>item.name)]);
 return [...new Set(values)].map(value=>{
  const saved=existing.get(value);if(saved)return saved;
  const base=`value_${value.replace(/[^\p{L}\p{N}_]/gu,'_').toLowerCase()}`;
  let name=base;
  while(new TextEncoder().encode(name).length>48)name=name.slice(0,-1);
  const stem=name;let count=2;while(used.has(name))name=`${stem}_${count++}`;
  used.add(name);return {value,name};
 });
}

export function pivotIssue(pivot:PivotSpec,source?:Source):string|null{
 if(!source)return '원천 테이블을 선택하세요.';
 if(!pivot.keys.length)return '행 기준 컬럼을 선택하세요.';
 if(pivot.keys.length>20)return '행 기준은 최대 20개입니다.';
 if(!pivot.operation_column||!pivot.value_column||!pivot.time_column)return '열 기준, 값, 시간 기준 컬럼을 선택하세요.';
 if(!pivot.tie_breaker)return '시각이 같은 행을 구분할 고유 번호·순번 컬럼을 선택하세요.';
 const selected=[...pivot.keys,pivot.operation_column,pivot.value_column,pivot.time_column,pivot.tie_breaker];
 if(new Set(selected).size!==selected.length)return '행 기준과 각 역할에는 서로 다른 컬럼을 선택하세요.';
 if(selected.some(name=>!source.columns.some(c=>c.name===name)))return '원천 테이블에서 삭제된 컬럼이 있습니다. 다시 선택하세요.';
 if(!isNumericType(source.columns.find(c=>c.name===pivot.value_column)!.type))return '값에는 숫자 컬럼을 선택하세요.';
 if(!isTimeType(source.columns.find(c=>c.name===pivot.time_column)!.type))return '시간 기준에는 날짜·시간 컬럼을 선택하세요.';
 if(!pivot.outputs.length)return '표시할 열 항목을 추가하세요.';
 if(pivot.outputs.length>30)return '열 항목은 최대 30개까지 펼칠 수 있습니다.';
 if(pivot.outputs.some(item=>!item.value||item.value.length>500))return '열 항목은 1~500자로 입력하세요.';
 if(pivot.outputs.some(item=>!item.name.trim()||item.name.startsWith('__')||/[\x00-\x1f\x7f]/.test(item.name)||new TextEncoder().encode(item.name).length>63))return '출력 이름은 __로 시작하지 않는 63바이트 이내 이름으로 입력하세요.';
 if(new Set(pivot.outputs.map(item=>item.value)).size!==pivot.outputs.length)return '열 항목이 중복됩니다.';
 const names=[...pivot.keys,...pivot.outputs.map(item=>item.name)];
 if(new Set(names).size!==names.length)return '출력 이름이 행 기준 또는 다른 출력 이름과 중복됩니다.';
 return null;
}
