"""Chat-selected statistical/ML jobs on the existing bounded worker and review path."""
import json
from copy import deepcopy

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from . import db as database
from .analytics.models import AnalysisRun
from .analytics.proposals import live_editor, lock_active_workspace
from .analytics.service import project_scope, serialize_creation
from .auth import require_editor
from .chat_grounding import GroundingQuestion, resolve_project
from .chat_knowledge import checked_knowledge
from .evaluation_analysis import (Search, SelectedCard, _compile, _case, _quota,
    checked_run, execution_binding, numeric_type, validate_settings, view)
from .evaluation_recommendation import PREFIX, validate_plan
from .models import Audit, Source
from .semantic.collector_models import CollectedDraft, KnowledgeQuestion
from .semantic.collector_service import inventory
from .semantic.models import KnowledgeItem, KnowledgeVersion
from .semantic.service import digest

router = APIRouter(prefix='/api/chat/data-analysis', tags=['chat data analysis'])


def text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def context_for(request, actor_id):
    from .assistant import proposal_context
    from .evaluation_chat import evaluation_answer
    from .measurement_items import item_context
    context = proposal_context(request, actor_id)
    selected = request.dataset_id or (request.card.dataset_id if request.card else None)
    datasets = [d for d in context['datasets'] if d.get('project_id') == request.project_id
                and (not selected or d['id'] == selected)]
    if not datasets:
        raise GroundingQuestion('현재 범위에서 분석할 데이터셋을 찾지 못했습니다. 데이터 관리에서 비교할 데이터셋을 선택하거나, 챗에서 필요한 데이터셋 생성을 요청해 주세요.')
    user_text = '\n'.join([m.content for m in request.history[-6:] if m.role == 'user'] + [request.message])
    facts = [{'reference': 'user', 'kind': 'user', 'text': user_text}]
    for d in datasets:
        facts.append({'reference': 'dataset:' + d['id'], 'kind': 'dataset',
                      'text': text({k: d.get(k) for k in ('name', 'spec', 'columns')})})
    for item in context.get('semantic_context', {}).get('items', []):
        facts.append({'reference': 'knowledge:' + item['id'], 'kind': 'knowledge', 'text': text(item)})
    cases = evaluation_answer(request).get('cases', [])
    for case in cases:
        facts.append({'reference': 'case:' + case['id'], 'kind': 'case',
                      'text': text({k: case.get(k) for k in ('name', 'details', 'description')})})
    payload = {'datasets': datasets, 'selected_dataset_id': selected,
               'selected_card': request.card.model_dump(mode='json') if request.card else None,
               'project_id': request.project_id, 'period_start': request.period_start,
               'facts': facts, 'knowledge': context.get('knowledge'),
               'measurement_items': {d['id']: item_context(d, request.card.filters if request.card else []) for d in datasets}}
    if len(text(payload)) > 160000:
        raise GroundingQuestion('분석 후보가 너무 많습니다. 데이터 관리에서 분석할 데이터셋을 먼저 선택해 주세요.')
    return payload, context, cases


def validate(value, payload):
    dataset = next((d for d in payload['datasets'] if d['id'] == value.dataset_id
                    and d['version'] == value.dataset_version), None)
    if dataset is None:
        raise ValueError('제공된 현재 데이터셋 ID와 버전을 선택하세요.')
    facts = [f for f in payload['facts'] if not f['reference'].startswith('dataset:')
             or f['reference'] == 'dataset:' + dataset['id']]
    validate_plan(value.plan, facts, dataset['columns'], allow_structural_evidence=True)
    if not value.plan.questions:
        from .measurement_items import validate_items
        validate_items(dataset, value.plan.settings, [v.filter.model_dump(mode='json') for v in value.filters] +
            ((payload.get('selected_card') or {}).get('filters') or []))
    by_ref = {f['reference']: f for f in facts}
    for item in value.filters:
        evidence, condition = item.evidence, item.filter
        fact = by_ref.get(evidence.reference)
        if (not fact or fact['kind'] not in {'user', 'clarification'} or not evidence.quote
                or evidence.quote not in fact['text']):
            raise ValueError('추가 필터에는 사용자가 명시한 값의 원문 근거가 필요합니다.')
        values = condition.value if isinstance(condition.value, list) else [condition.value]
        if not values or any(str(v).casefold() not in evidence.quote.casefold() for v in values):
            raise ValueError('필터 값을 추측하지 마세요. 명시된 값만 사용하거나 질문하세요.')
        if condition.column not in {c['name'] for c in dataset['columns']}:
            raise ValueError('필터에 실제 출력 컬럼을 사용하세요.')
    return dataset


def facts_for_run(db, run):
    """Pinned source meaning, plus only reviewed answers to this run's questions."""
    origin = run.request['_chat_analysis']
    if origin.get('knowledge_context_id'):
        checked_knowledge(origin['knowledge_context_id'], run.created_by, run.request.get('project_id'), db=db)
    dataset = run.request['_evaluation_dataset']
    for reference in origin.get('case_refs', []):
        _case(db, reference, dataset, run.request.get('project_id'))
    facts = deepcopy(origin['facts'])
    basis = {f['reference']: digest(f) for f in facts}
    question_ids = set(run.question_ids or []) | set(run.request.get('_evaluation_plan_questions') or [])
    for identity in sorted(question_ids):
        question = db.get(KnowledgeQuestion, identity)
        draft = db.scalar(select(CollectedDraft).where(CollectedDraft.question_id == identity,
            CollectedDraft.status == 'approved').order_by(CollectedDraft.created_at.desc()).limit(1))
        if not question or question.status != 'answered' or not draft:
            continue
        item = db.get(KnowledgeItem, draft.item_id)
        version = db.get(KnowledgeVersion, (draft.item_id, draft.item_version))
        if not item or item.published_version != draft.item_version or not version or version.status != 'approved':
            raise HTTPException(409, '분석에 사용한 확인 답변이 변경되었습니다. 확인 요청에서 다시 검토하세요.')
        fact = {'reference': 'clarification:' + identity, 'kind': 'clarification', 'text': text(version.content)}
        facts.append(fact)
        basis[fact['reference']] = digest(fact)
    return facts, basis


def queue_ready(db, run, settings):
    actor = live_editor(db, run.created_by)
    _quota(db, actor)
    validate_settings(settings, run.request['_evaluation_columns'])
    run.request = {**run.request, 'settings': settings.model_dump(mode='json')}
    run.status, run.revision = 'queued', run.revision + 1
    db.add(Audit(user_id=actor.id, action='chat.data_analysis.execute', entity_id=run.id,
                 details={'method': settings.method}))


def create(value, request, actor_id, payload, context, cases, *, origin_project_id):
    dataset = validate(value, payload)
    card = SelectedCard.model_validate(request.card.model_dump(include=set(SelectedCard.model_fields))) if request.card else SelectedCard(
        dataset_id=dataset['id'], dataset_version=dataset['version'])
    card = card.model_copy(update={'filters': [*card.filters, *[v.filter for v in value.filters]]})
    search = Search(query=request.message, project_id=request.project_id, dataset_id=dataset['id'],
                    card=card, period_start=request.period_start)
    # No measurement execution before validation; the worker owns the calculation.
    compiled, _, _, period = _compile(search, check_types=True)
    with database.SessionLocal.begin() as db:
        serialize_creation(db)
        lock_active_workspace(db)
        actor = live_editor(db, actor_id)
        _quota(db, actor, preparing=True)
        selected = inventory(db).get(dataset['id'])
        if not selected or compiled['_evaluation_definition_digest'] != digest({k: selected[k] for k in ('spec', 'columns', 'version')}):
            raise HTTPException(409, '분석 준비 중 데이터셋이 변경되었습니다. 다시 요청하세요.')
        if selected.get('project_id') != request.project_id:
            raise HTTPException(409, '다른 프로젝트의 데이터셋을 분석할 수 없습니다.')
        used = {c.evidence.reference for c in value.plan.columns}
        if value.plan.row_basis:
            used.add(value.plan.row_basis.reference)
        refs = [{'id': c['id'], 'version': c['version']} for c in cases if 'case:' + c['id'] in used]
        facts = [f for f in payload['facts'] if f['reference'] in used or f['reference'] in {'user', 'dataset:' + selected['id']}]
        knowledge = context.get('knowledge')
        if knowledge and not knowledge.get('blocking') and knowledge['resolution'] != 'needs_clarification':
            checked_knowledge(knowledge['context_id'], actor.id, request.project_id, db=db)
        elif knowledge:
            raise GroundingQuestion('관련 업무 용어의 정의가 모호합니다. AI 지식에서 사용할 정의를 확인한 뒤 다시 분석해 주세요.')
        sources = [db.get(Source, s['id']) for s in selected['sources']]
        stored = {'kind': 'evaluation', 'project_id': request.project_id, 'settings': None,
            '_workspace_token': database.workspace_token(), '_owner_role': actor.role,
            '_project_scope': project_scope(db, request.project_id), '_evaluation_dataset': selected,
            '_evaluation_case': None, '_evaluation_search': search.model_dump(mode='json'),
            '_evaluation_binding': execution_binding(db, selected),
            '_evaluation_columns': [{**c, 'numeric': numeric_type(c['type'])} for c in compiled['columns']],
            '_evaluation_scope': {'filters': [f.model_dump(mode='json') for f in card.filters],
                'period_start': request.period_start.model_dump(mode='json') if request.period_start else None,
                'period': period, 'card_title': card.title},
            '_chat_analysis': {'version': 1, 'question': request.message, 'origin_project_id': origin_project_id,
                'facts': facts, 'case_refs': refs, 'knowledge_context_id': knowledge['context_id'] if knowledge else None}}
        run = AnalysisRun(connection_key=database.source_database_key(), kind='evaluation', status='needs_input',
            request=stored, created_by=actor.id, question_ids=[], source_snapshot={s.id: {
                'source_id': s.id, 'name': s.name, 'schema_name': s.schema_name, 'table_name': s.table_name,
                'columns': s.columns} for s in sources})
        db.add(run)
        db.flush()
        plan = value.plan.model_dump(mode='json', exclude={'questions'})
        plan.update(status='waiting' if value.plan.questions else 'ready', message='')
        if value.plan.questions:
            waiting = db.scalar(select(func.count()).select_from(KnowledgeQuestion).where(
                KnowledgeQuestion.connection_key == run.connection_key, KnowledgeQuestion.status != 'answered'))
            if waiting + len(value.plan.questions) > 100:
                raise HTTPException(429, '기존 확인 요청을 처리한 뒤 다시 분석해 주세요.')
            ids = []
            for clarification in value.plan.questions:
                q = KnowledgeQuestion(connection_key=run.connection_key, created_by=actor.id,
                    fingerprint=digest([PREFIX, run.id, clarification.question]), dataset=selected,
                    targets=[{'kind': 'column', 'column': c, 'source_id': '', 'value': ''} for c in clarification.columns]
                        or [{'kind': 'dataset', 'column': '', 'source_id': '', 'value': ''}],
                    kind='term', topic='other', subject=PREFIX + run.id, title=clarification.question,
                    reason=clarification.reason + ' 답변을 검토·승인한 뒤 이 분석을 이어갈 수 있습니다.', status='open')
                db.add(q)
                db.flush()
                ids.append(q.id)
            run.question_ids = ids
            plan['settings'] = None
        stored = {**stored, '_evaluation_recommendation': plan,
                  '_evaluation_plan_basis': {f['reference']: digest(f) for f in facts if f['reference'] in used}}
        run.request = stored
        if not value.plan.questions:
            queue_ready(db, run, value.plan.settings)
        db.add(Audit(user_id=actor.id, action='chat.data_analysis.prepare', entity_id=run.id,
                     details={'dataset_id': selected['id']}))
        return run.id


def answer(db, run, actor):
    current = view(db, run, actor)
    recommendation = current.get('recommendation') or {}
    result = current.get('result')
    explanation = recommendation.get('summary') or '현재 데이터 분석 상태를 확인합니다.'
    conclusions, next_checks = [], []
    if result:
        def label(column):
            return result.get('item_labels', {}).get(column, column)
        def number(v):
            return '계산 불가' if v is None else format(v, '.8g')
        if result.get('correlation'):
            c = result['correlation']
            conclusions.append(f"{label(c['x'])}와 {label(c['y'])}의 유효한 수치 쌍 {c['pair_count']}개에서 Pearson 상관계수는 {number(c['pearson'])}입니다.")
            if c['pearson'] is not None:
                direction = '함께 증가하는' if c['pearson'] > 0 else '반대 방향으로 변하는' if c['pearson'] < 0 else '선형으로 함께 변하지 않는'
                conclusions.append(f'이 표본에서는 두 항목이 {direction} 관계가 관측됐습니다. 상관만으로 원인이나 인과 효과를 확정할 수는 없습니다.')
            next_checks.append('장비·Recipe·기간을 나눠도 이 관계가 유지되는지 확인하면 적용 범위를 좁힐 수 있습니다.')
        elif result.get('ml'):
            ml = result['ml']
            conclusions.append(ml['interpretation'])
            if ml.get('metrics'):
                m = ml['metrics']
                conclusions.append(f"검증 데이터에서 평균 예측 오차는 ML {number(m['mae'])}, 단순 중앙값 기준 {number(m['baseline_mae'])}입니다.")
            important = [v['column'] for v in ml.get('importance', []) if v['mae_increase'] > 0][:3]
            if important:
                conclusions.append('검증 표본의 예측에 기여한 상위 후보는 ' + ', '.join(label(c) for c in important) + '입니다. 변수 중요도는 인과 효과가 아닙니다.')
            next_checks.append('다른 기간·장비에서도 예측 성능이 유지되는지 확인하고, 서로 연관된 설명 변수의 중요도를 함께 검토하세요.')
        elif result.get('groups'):
            for metric in run.request['settings']['metrics']:
                values = [(str(g['value']), m['mean']) for g in result['groups'] for m in g['metrics']
                          if m['column'] == metric and m['mean'] is not None]
                if values:
                    low, high = min(values, key=lambda v: v[1]), max(values, key=lambda v: v[1])
                    conclusions.append(f"{label(metric)} 평균은 {low[0]}에서 {number(low[1])}, {high[0]}에서 {number(high[1])}입니다. 그룹별 표본 수와 다른 공정 조건도 함께 확인해야 합니다.")
            next_checks.append('그룹별 표본 수·분산·기간 차이를 확인하세요. 관측한 평균 차이만으로 Recipe나 장비의 인과 효과를 확정할 수 없습니다.')
        if not result.get('row_count'):
            conclusions.append('현재 조건에 해당하는 데이터가 없습니다. 범위를 임의로 넓히지 않았습니다.')
        explanation = '\n'.join(conclusions) or result.get('summary', '계산 결과를 확인하세요.')
    elif run.status == 'needs_input':
        explanation = '분석에 필요한 연결이 모호해 계산을 시작하지 않았습니다. 확인 요청에 답변한 뒤 검토·승인하고 분석을 이어가세요.'
    elif run.status == 'failed':
        explanation = current['error'] or '분석을 완료하지 못했습니다.'
    elif run.status in {'cancelled', 'cancelling'}:
        explanation = '분석이 취소되었거나 취소 처리 중입니다. 결과와 지식을 새로 반영하지 않습니다.'
    else:
        explanation += '\n현재 데이터로 계산하고 있습니다. 완료되면 이 메시지에 결과가 표시됩니다.'
    return {'kind': 'data_analysis', 'run_id': run.id, 'project_id': current['project_id'],
        'status': run.status, 'explanation': explanation, 'next_checks': next_checks,
        'dataset': current['dataset'], 'settings': current['settings'], 'scope': current['scope'],
        'result': result, 'review': current['review'], 'revision': current['revision'],
        'workspace_token': current['workspace_token'], 'questions': recommendation.get('questions', []),
        'plan': {'summary': recommendation.get('summary'), 'columns': recommendation.get('columns', []),
                 'row_basis': recommendation.get('row_basis'), 'limitations': recommendation.get('limitations', [])},
        'notice': '이번 질문에 대해 현재 데이터를 계산한 결과입니다. 지식 승인은 별도이며 관찰·예측 관계를 원인으로 확정하지 않습니다.'}


def read(identity, actor_id, origin_project_id):
    with database.SessionLocal() as db:
        actor = live_editor(db, actor_id)
        run = checked_run(db, identity, actor)
        origin = run.request.get('_chat_analysis')
        if not origin or origin.get('origin_project_id') != origin_project_id or run.created_by != actor_id:
            raise HTTPException(404, '현재 대화 범위의 분석이 아닙니다.')
        return answer(db, run, actor)


def restore(response, actor_id, project_id):
    try:
        return read(response['run_id'], actor_id, project_id)
    except HTTPException:
        return {'kind': 'data_analysis', 'status': 'unavailable',
                'explanation': '데이터·지식·접근 범위가 변경되어 이전 계산 결과를 표시할 수 없습니다. 현재 조건으로 다시 분석해 주세요.'}


@router.get('/{identity}')
def get_answer(identity: str, project_id: int | None = None, user=Depends(require_editor)):
    return read(identity, user.id, project_id)


async def run_workflow(request, adapter):
    from .catalog import projects_list
    from .analytics.worker import wake_worker
    try:
        projects = await run_in_threadpool(projects_list)
        project = resolve_project(request, {'projects': projects})
        effective = request.model_copy(update={'project_id': project['id']}) if project else request
        payload, context, cases = await run_in_threadpool(context_for, effective, adapter.actor_id)
        yield 'status', {'message': '현재 데이터셋과 승인 지식을 바탕으로 통계·ML 분석 방법을 고르고 있습니다.'}
        value, _ = await adapter.run('data_analysis_plan', {'data_analysis_context': payload},
                                    validate=lambda v: validate(v, payload))
        identity = await run_in_threadpool(create, value, effective, adapter.actor_id, payload, context, cases,
                                          origin_project_id=request.project_id)
        wake_worker()
        result = await run_in_threadpool(read, identity, adapter.actor_id, request.project_id)
        yield 'data_analysis', result
    except GroundingQuestion as question:
        yield 'clarification', {'kind': 'clarification', 'question': question.question, 'options': question.options}
