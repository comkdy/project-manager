import type {CardSpec,Dashboard} from './types';
export const cardConflictSnapshot=(board:Dashboard,cardId:string)=>({card:board.cards.find(card=>card.id===cardId)??null,version:board.version});
export function prepareCardSave(board:Dashboard,card:CardSpec,expectedVersion?:number):Pick<Dashboard,'version'|'cards'|'layout'>|null {
 if(expectedVersion!==undefined&&board.version!==expectedVersion)return null;
 const found=board.cards.some(value=>value.id===card.id);
 return {version:expectedVersion??board.version,cards:found?board.cards.map(value=>value.id===card.id?card:value):[...board.cards,card],layout:found?board.layout:[...board.layout,{i:card.id,x:0,y:Math.max(0,...board.layout.map(item=>item.y+item.h)),w:card.kind==='gantt'?12:6,h:card.kind==='gantt'?10:5}]};
}
