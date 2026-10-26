import type {CardSpec,ChartResult,QueryContext} from './types.ts';

export interface ChartVersion {query:string;version:number}
const queryIdentity=(card:CardSpec,context:QueryContext)=>JSON.stringify([card,context]);

export function receivedChartVersion(card:CardSpec,context:QueryContext,result:ChartResult):ChartVersion {
 const version=result.meta.dataset_version;
 return {query:queryIdentity(card,context),version:typeof version==='number'&&Number.isInteger(version)&&version>0?version:card.dataset_version};
}

export function displayedChartVersion(card:CardSpec,context:QueryContext,received?:ChartVersion):number {
 return received?.query===queryIdentity(card,context)?received.version:card.dataset_version;
}
