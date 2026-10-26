"""Resolve analysis entities against visible app data before accepting a model plan."""
import re
import unicodedata

from fastapi import HTTPException


def normalized(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value or '')).casefold()


def mentions(message, names):
    hits=[]
    for name in set(names):
        key=normalized(name)
        if not key:
            continue
        pattern=r'(?<![a-z0-9_])'+r'\s*'.join(re.escape(c) for c in key)+r'(?![a-z0-9_])'
        for match in re.finditer(pattern, unicodedata.normalize('NFKC',message).casefold()):
            hits.append((name,match.start(),match.end()))
    return sorted({name for name,start,end in hits if not any(
        other_start<=start and other_end>=end and other_end-other_start>end-start
        for _,other_start,other_end in hits)})


def routing_grounding(request):
    from .catalog import projects_list
    from .analytics.chat_tools import get_analysis_catalog
    projects=[{k:p.get(k) for k in ('id','name','code','lot_cds')} for p in projects_list()]
    try:
        available=get_analysis_catalog()
    except HTTPException:
        available={'supported':False,'series':[],'recipe_operations':[], 'issues':['분석 목록을 현재 조회할 수 없습니다.']}
    # Only registered names and identities, never measurement rows or authored SQL.
    return {'projects':projects,'series':available['series'],
        'recipe_operations':available['recipe_operations'],'supported':available['supported'],
        'issues':available.get('issues',[])}


class GroundingQuestion(ValueError):
    def __init__(self, question, options=()):
        self.question=question
        self.options=list(options)[:4]
        super().__init__(question)


def project_aliases(project):
    return [str(v) for v in [project.get('name'),project.get('code'),*(project.get('lot_cds') or [])] if v]


def resolve_project(request, grounding):
    projects=grounding.get('projects',[])
    # A follow-up may inherit an explicit project from the user's prior turn,
    # but never from the assistant's previous guesses.
    messages=[request.message,*[m.content for m in reversed(request.history) if m.role=='user']]
    for message in messages:
        hits=[p for p in projects if mentions(message,project_aliases(p))]
        if not hits:
            continue
        if len(hits)>1:
            raise GroundingQuestion('질문에 여러 프로젝트가 포함돼 있습니다. 이번 분석에서 사용할 프로젝트는 무엇인가요?', [p['name'] for p in hits])
        chosen=hits[0]
        if request.project_id is not None and request.project_id!=chosen['id']:
            raise GroundingQuestion(f"현재 대화의 프로젝트와 질문의 {chosen['name']}가 다릅니다. {chosen['name']} 프로젝트에서 새 대화로 분석하거나 현재 프로젝트 이름을 지정해 주세요.")
        return chosen
    return next((p for p in projects if p['id']==request.project_id),None)


def canonical(value, names, label):
    if value is None:
        return None
    found=sorted({name for name in names if normalized(name)==normalized(value)})
    if len(found)>1:
        raise GroundingQuestion(f'{value}에 해당하는 {label}이 여러 개 있습니다. 어느 항목인가요?',found)
    if not found:
        raise ValueError(f'{label} {value!r}는 등록된 목록에 없습니다. 제공된 이름으로 다시 해석하고, 연결이 불명확하면 clarification으로 질문하세요.')
    return found[0]


def ground_analysis(call, request, grounding):
    if not grounding.get('supported'):
        raise GroundingQuestion('현재 공개된 데이터에서 분석할 측정 원천을 확인할 수 없습니다. 사용할 데이터셋을 선택해 주세요.')
    project=resolve_project(request,grounding)
    series=grounding['series']; histories=grounding['recipe_operations']
    parameters={s['parameter'] for s in series}
    changes={}
    parameter=call.parameter
    if parameter and project and normalized(parameter) in {normalized(v) for v in project_aliases(project)}:
        if any(normalized(parameter)==normalized(p) for p in parameters):
            raise GroundingQuestion(f'{parameter}가 프로젝트와 측정 항목 이름에 모두 있습니다. 어떤 의미로 쓰셨나요?', ['프로젝트 범위','측정 항목'])
        raise ValueError(f'{parameter}는 프로젝트 범위입니다. parameter에 넣지 마세요. 특정 PARA를 지정하지 않은 질문은 parameter와 family를 null로 두고 등록 공정을 해석하세요.')
    if parameter:
        changes['parameter']=canonical(parameter,parameters,'측정 항목')
        valid=[s for s in series if s['parameter']==changes['parameter'] and (call.family is None or s['family']==call.family)]
        if not valid:
            raise ValueError('측정 항목과 Inline/EPM 구분이 등록 목록과 다릅니다. 실제 series 조합을 사용하세요.')
    if call.operation:
        names={h['operation'] for h in histories} if call.analysis_kind=='split' else {s['operation'] for s in series if s['operation']}
        changes['operation']=canonical(call.operation,names,'공정')
    elif call.analysis_kind=='split':
        found=mentions(request.message,[h['operation'] for h in histories])
        if len(found)==1:
            changes['operation']=canonical(found[0],[h['operation'] for h in histories],'공정')
        elif len(found)>1:
            raise GroundingQuestion('Recipe를 비교할 공정이 여러 개입니다. 어떤 공정을 기준으로 볼까요?',found)
    operation=changes.get('operation',call.operation)
    if call.analysis_kind=='split' and operation:
        recipes=next(h['recipes'] for h in histories if h['operation']==operation)
        for field in ('recipe_a','recipe_b'):
            if getattr(call,field):
                changes[field]=canonical(getattr(call,field),[r for r in recipes if r is not None],'Recipe')
    effective=request.model_copy(update={'project_id':project['id']}) if project else request
    return call.model_copy(update=changes),effective
