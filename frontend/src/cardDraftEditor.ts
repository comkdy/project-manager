import type {ReactNode} from 'react';
import type {CardSpec} from './types';
import type {CardEditDraft} from './editorDraftValues';
export interface CardDraftEditorProps {draftNotice?:ReactNode;onEdited?:()=>void;onDraftChange?:(value:CardEditDraft)=>void;onBusyChange?:(busy:boolean)=>void;recoveredExpression?:string}
export interface LatestCardDraft {card:CardSpec|null;version:number}
