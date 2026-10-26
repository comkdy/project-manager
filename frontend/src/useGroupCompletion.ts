import {useEffect,useState} from 'react';
import {api} from './api';
import {displayExpression} from './sqlIdentifiers.ts';

type Result={complete:boolean;group_by:string[]};
type State={key:string;result?:Result;error?:unknown};

export function useGroupCompletion(expressions:string[],inputs:string[],having:string|null|undefined,enabled:boolean){
 const key=JSON.stringify({expressions,input_columns:inputs,having_sql:having??null});
 const [state,setState]=useState<State>();
 useEffect(()=>{
  if(!enabled)return;
  const controller=new AbortController();
  const timer=setTimeout(()=>{
   api<Result>('/datasets/group-completion',{method:'POST',body:key,signal:controller.signal})
    .then(result=>{if(!controller.signal.aborted)setState({key,result})})
    .catch(error=>{if(!controller.signal.aborted)setState({key,error})});
  },250);
  return()=>{clearTimeout(timer);controller.abort()};
 },[key,enabled]);
 return {
  pending:enabled&&state?.key!==key,
  groups:enabled&&state?.key===key&&state.result?.complete?state.result.group_by.map(displayExpression):undefined,
  error:enabled&&state?.key===key?state.error:undefined,
 };
}
