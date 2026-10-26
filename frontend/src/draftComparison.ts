export interface DraftDisplayField {path:string;label:string;text:string}
const labels:Record<string,string>={
 name:'이름',nameDraft:'입력 중인 이름',title:'제목',domain:'업무 영역',description:'업무 설명',kind:'종류',scope:'적용 범위',aliases:'별칭',aliasesText:'별칭 입력',unresolved:'확인할 내용',unresolvedText:'확인할 내용 입력',references:'연결한 승인 지식',item_id:'지식 식별자',version:'버전',revision:'수정 기준',note:'검토 사유',notes:'참고 사항',change_note:'변경 사유',target:'반영 대상',text:'입력 내용',
 editorMode:'작성 방식',sql:'조회 SQL',raw_sql:'조회 SQL',source_id:'원천 테이블',dataset_id:'데이터셋',dataset_version:'데이터셋 버전',columns:'출력 컬럼',column:'컬럼',expression:'계산식',filters:'조회 필터',predicates:'적용 조건',op:'조건',value:'값',value_kind:'값 형식',value_text:'입력값',role:'역할',sources:'연결 원천',joins:'연결 테이블',join_types:'연결 방식',where_sql:'행 선택 조건',having_sql:'집계 결과 조건',group_by:'그룹 기준',autoGroup:'그룹 자동 완성',
 python_script:'Python 스크립트',editor_mode:'편집 방식',x:'X축',y:'Y축',color:'색상·셀 값',trellis:'패널 구분',aggregate:'집계 방식',value_expression:'직접 계산식',value_expression_name:'계산식 이름',expressionDraft:'입력 중인 계산식',options:'표시 설정',palette:'색상 팔레트',bins:'구간 수',sort:'정렬',trellis_columns:'패널 열 수',x_min:'X축 최솟값',x_max:'X축 최댓값',y_min:'Y축 최솟값',y_max:'Y축 최댓값',x_font_size:'X축 글자 크기',y_font_size:'Y축 글자 크기',x_scale:'X축 눈금',y_scale:'Y축 눈금',x_label:'X축 이름',y_label:'Y축 이름',value_format:'값 표시',
 period:'조회 기간',use_dashboard:'공통 기간 사용',gantt:'일정 설정',data_mode:'일정 데이터 구성',line_filters:'시작선 필터',key_filters:'핵심 공정 필터',as_of:'기준 시각',status_source_id:'현재 상태 테이블',tat_source_id:'소요 시간 테이블',history_source_id:'완료 이력 테이블',started_column:'시작 시각 컬럼',
 pivot:'피벗 설정',keys:'구분 컬럼',operation_column:'공정 컬럼',value_column:'측정값 컬럼',time_column:'시각 컬럼',tie_breaker:'동일 시각 구분',outputs:'피벗 출력',require_all:'모든 공정 필요',
 formula:'계산식',numerator:'분자',denominator:'분모',grain:'계산 대상 단위',unit:'출력 단위',time_basis:'시간 기준',time_window:'기간 규칙',timezone:'시간대',exclusions:'제외 조건',retest_policy:'재검사 처리',deduplication:'중복 처리',zero_policy:'분모 0 처리',aggregation:'재집계 규칙',column_notes:'컬럼 설명',evidence:'확인 근거',duplication_notes:'중복 집계 주의사항',join_notes:'연결 조건 설명',question:'검토한 질문',assumptions:'전제 조건',expected_result:'기대 결과·검토 근거',
 reason:'질문 이유',topic:'질문 주제',subject:'세부 주제',targetKind:'질문 대상 유형',columnKey:'질문 대상 컬럼',targetValue:'질문 대상 값',id:'식별자',status:'상태',
};
const hidden=new Set(['dirty','targetRevision','editingName','sqlOrigin','renderer','script_input']);
const transparent=new Set(['proposal','content','card','spec','binding','details']);
const names:Record<string,Record<string,string>>={kind:{term:'용어',metric:'지표',data:'데이터 설명',relation:'테이블 관계',query:'검증된 질의',bar:'막대',line:'선',scatter:'산점도',histogram:'히스토그램',box:'상자',wafer:'Wafer map',gantt:'간트',kpi:'KPI',column:'컬럼',number:'숫자',operator:'연산자'},scope:{common:'모든 DB의 공통 정의',database:'현재 DB 공용'},editorMode:{form:'간편 작성기',sql:'SQL 편집'},editor_mode:{simple:'간편 편집기',python:'Python 스크립트'},aggregate:{none:'집계 없음',count:'개수',sum:'합계',avg:'평균',min:'최솟값',max:'최댓값',yield:'수율'},op:{eq:'같음',ne:'다름',gt:'초과',gte:'이상',lt:'미만',lte:'이하',in:'목록에 포함',not_in:'목록에서 제외',contains:'문자열 포함',is_null:'값 없음',is_not_null:'값 있음'},sort:{asc:'오름차순',desc:'내림차순'},x_scale:{linear:'일반',log:'로그'},y_scale:{linear:'일반',log:'로그'},value_format:{number:'숫자',percent:'백분율'},targetKind:{dataset:'데이터셋 전체',column:'컬럼',column_value:'컬럼 안의 특정 값'},status:{draft:'초안',approved:'승인됨',retired:'폐기됨'},data_mode:{dataset:'저장된 데이터셋',tables:'테이블 연결'}};
const label=(key:string)=>labels[key]??key.replace(/([a-z])([A-Z])/g,'$1 $2').replace(/_/g,' ');
function scalar(value:unknown,key:string):string {if(value===null||value===undefined||value==='')return '입력 없음';if(typeof value==='boolean')return value?'사용':'사용 안 함';return names[key]?.[String(value)]??String(value)}
export function describeDraft(value:unknown):DraftDisplayField[]{
 const fields:DraftDisplayField[]=[];
 function walk(current:unknown,path:string,prefix:string,key:string){
  if(Array.isArray(current)){
   if(current.every(item=>item===null||typeof item!=='object')){fields.push({path,label:prefix||'입력 내용',text:current.length?current.map(item=>scalar(item,key)).join('\n'):'입력 없음'});return}
   current.forEach((item,index)=>{if(item&&typeof item==='object'&&typeof item.label==='string'&&'value' in item)walk(item.value,`${path}.${index}`,item.label,'');else walk(item,`${path}.${index}`,`${prefix} ${index+1}`.trim(),key)});return;
  }
  if(current&&typeof current==='object'){
   for(const [child,item] of Object.entries(current)){if(hidden.has(child)||item===undefined)continue;if(child==='nameDraft'&&item===(current as Record<string,unknown>).name)continue;walk(item,`${path}.${child}`,transparent.has(child)?prefix:[prefix,label(child)].filter(Boolean).join(' · '),child)}return;
  }
  fields.push({path,label:prefix||'입력 내용',text:scalar(current,key)});
 }
 walk(value,'','','');return fields;
}
export function compareDraftFields(mine:unknown,latest:unknown){
 const a=describeDraft(mine),b=describeDraft(latest),left=new Map(a.map(field=>[field.path,field])),right=new Map(b.map(field=>[field.path,field]));
 return [...new Set([...left.keys(),...right.keys()])].map(path=>{const mineText=left.get(path)?.text??'입력 없음',latestText=right.get(path)?.text??'입력 없음';return {path,label:(left.get(path)??right.get(path))!.label,mine:mineText,latest:latestText,changed:mineText!==latestText}});
}
