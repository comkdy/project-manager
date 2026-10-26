import type {CardSpec} from './types';
import {expressionName} from './cardExpressions.ts';

/** Display labels never replace the column identifiers used to query data. */
export function columnLabel(card:CardSpec,axis:'x'|'y'):string {
 return card[axis]?(card.options[`${axis}_label`]?.trim()||card[axis]!):'';
}

export function axisLabel(card:CardSpec,axis:'x'|'y'):string {
 if(axis==='x'||card.kind==='wafer')return columnLabel(card,axis);
 if(card.kind==='histogram')return '개수';
 if(card.value_expression!=null)return expressionName(card);
 if(card.aggregate==='yield')return '수율 (%)';
 if(card.aggregate==='count'||(card.kind==='kpi'&&card.aggregate==='none'&&!card.y))return '개수';
 return columnLabel(card,'y');
}
