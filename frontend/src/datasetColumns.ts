import type {Column,DatasetSpec,Source} from './types';
import {columnExpression,displayExpression} from './sqlIdentifiers.ts';
export {columnExpression} from './sqlIdentifiers.ts';

export type EditorColumn=DatasetSpec['columns'][number];
export type EditorSpec=Omit<DatasetSpec,'columns'>&{columns:EditorColumn[]};

export function defaultOutputName(expression:string):string{
 const trimmed=expression.trim();
 const quoted=/^"((?:[^"]|"")+)"$/.exec(trimmed);
 return quoted?quoted[1].replace(/""/g,'"'):trimmed;
}

export function sourceColumn(column:EditorColumn,inputs:Column[]):string|undefined{
 const expression=column.expression.trim();
 if(!/^"(?:[^"]|"")+"$|^[A-Za-z_][A-Za-z0-9_]*$/.test(expression))return undefined;
 return inputs.find(input=>input.name===defaultOutputName(expression))?.name;
}

export function outputName(column:EditorColumn):string{
 return column.name.trim()||defaultOutputName(column.expression);
}

export function editorSpec(spec:DatasetSpec,catalog:Source[]):EditorSpec{
 const names=new Set([
  ...(catalog.find(s=>s.id===spec.source_id)?.columns??[]).map(c=>c.name),
  ...catalog.flatMap(s=>s.columns.map(c=>`${s.id}__${c.name}`)),
 ]);
 return {...spec,group_by:(spec.group_by??[]).map(displayExpression),columns:spec.columns.map(column=>{
  const expression=displayExpression(column.expression.trim())||(names.has(column.name)?columnExpression(column.name):'');
  return {name:column.name===defaultOutputName(expression)?'':column.name,expression};
 })};
}

export function datasetSpec(spec:EditorSpec):DatasetSpec{
 const names=new Set<string>();
 return {...spec,columns:spec.columns.map(column=>{
  const expression=column.expression.trim();
  if(!expression)throw new Error('SQL 표현식을 입력하세요.');
  const name=outputName(column);
  if(new TextEncoder().encode(name).length>63)throw new Error('결과 컬럼명이 너무 깁니다. 63바이트 이내의 출력 이름을 입력하세요.');
  if(names.has(name))throw new Error(`${name}: 출력 이름이 중복됩니다. 다른 출력 이름을 지정하세요.`);
  names.add(name);
  return {name,expression};
 })};
}
