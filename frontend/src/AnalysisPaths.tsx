import {analysisPaths,type AnalysisFocus} from './analysisExperience';

export default function AnalysisPaths({onChoose}:{onChoose:(focus:AnalysisFocus)=>void}){
 return <div className="analysis-paths">{(['epm','inline'] as const).map((focus,index)=><button type="button" key={focus} onClick={()=>onChoose(focus)}><span className="path-number">0{index+1}</span><small>{focus==='epm'?'EPM 중심':'Inline 중심'}</small><h2>{analysisPaths[focus].question}</h2><p>{analysisPaths[focus].description}</p><strong>비교 시작하기 →</strong></button>)}</div>;
}
