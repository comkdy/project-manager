import type {CardSpec} from './types.ts';

export interface PythonConversion {card:CardSpec;simple_script:string;input_columns:{name:string;description:string}[]}
export function blankPythonCard(card:CardSpec):CardSpec{
 return {...card,editor_mode:'python',script_input:'dataset',python_script:'# df: 선택한 데이터셋의 조회 결과\n# 예: fig = px.scatter(df, x="컬럼명", y="컬럼명")\nfig = go.Figure()'};
}
function stableValue(value:unknown):unknown{
 if(Array.isArray(value))return value.map(stableValue);
 if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).filter(([,v])=>v!=null).sort(([a],[b])=>a.localeCompare(b)).map(([key,v])=>[key,stableValue(v)]));
 return value;
}
function simpleConfiguration(card:CardSpec){
 const {dataset_id,dataset_version,kind,renderer,x,y,color,trellis,aggregate,value_expression,value_expression_name,options}=card;
 return JSON.stringify(stableValue({dataset_id,dataset_version,kind,renderer,x,y,color,trellis,aggregate,value_expression,value_expression_name,options}));
}
/** Only an untouched generated script with its original chart settings can round-trip. */
export function canUseSimpleEditor(card:CardSpec,baseline?:string,original?:CardSpec){
 return card.script_input==='dataset'&&!!baseline&&!!original&&card.python_script===baseline&&simpleConfiguration(card)===simpleConfiguration(original);
}
/** Every old chart projection must be converted, including full x/y-based scripts. */
export function needsScriptExpansion(card:CardSpec){return card.script_input==='chart'}
export function applyPythonConversion(card:CardSpec,conversion:PythonConversion):CardSpec{
 if(!needsScriptExpansion(card))return card;
 const {python_script,script_input,dataset_version}=conversion.card;
 return {...card,python_script,script_input,dataset_version};
}
