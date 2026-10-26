import {useEffect,useState} from 'react';
import {api} from './api';
import type {PivotSpec,SimpleEditorState} from './types';

export function useSimpleEditor(sql:string,source_id:string,enabled:boolean,pivot?:PivotSpec|null){
 const key=JSON.stringify({sql,source_id,pivot:pivot??undefined});
 const [state,setState]=useState<{key:string;value:SimpleEditorState}>();
 useEffect(()=>{
  if(!enabled)return;
  const controller=new AbortController();
  const timer=setTimeout(()=>{
   api<SimpleEditorState>('/datasets/simple-editor',{method:'POST',body:key,signal:controller.signal})
    .then(value=>{if(!controller.signal.aborted)setState({key,value})})
    .catch(error=>{if(!controller.signal.aborted)setState({key,value:{available:false,reason:error instanceof Error?error.message:'SQL을 확인할 수 없습니다.'}})});
  },250);
  return()=>{clearTimeout(timer);controller.abort()};
 },[key,enabled]);
 return {pending:enabled&&state?.key!==key,value:enabled&&state?.key===key?state.value:undefined};
}
