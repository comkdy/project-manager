// The displayed local value has millisecond precision. Keep the source timestamp
// unchanged when it was only displayed, including any finer database precision.
export function analysisDateInput(value:string|undefined){
 if(!value)return '';
 const date=new Date(value);
 return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,-1);
}

export function analysisRequestDate(value:string,original?:string){
 return original&&analysisDateInput(original)===value?original:new Date(value).toISOString();
}
