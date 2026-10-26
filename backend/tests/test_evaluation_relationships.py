"""A one-line relationship stays separate from mapping and measured evidence."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.semantic import collector_config as llm
from app.semantic.collector_schemas import ExtractedKnowledge, EvaluationMention
from app.semantic.evaluation_claims import computed_findings, grounded_claims
from app.semantic.evaluation_extraction import EvaluationPassages, prepare, restore
from app.semantic.schemas import Content, EvaluationClaim
from app.evaluation_narrative import evidence_payload
from test_evaluation_cases import cases
from test_evaluation_natural_flow import definition, projects, register_case
from test_semantic_collection import BASE, approve, draft, env
from test_evaluation_analysis import setup, execute, fake_result
from test_evaluation_datasets import prepared, start, prepare as prepare_dataset, existing_plan

TEXT = 'A operation은 Inline A CD의 alpha에 영향을 준다.'


def claim(answer_id='first', **changes):
    return {'source_term': 'A operation', 'target_term': 'Inline A CD의 alpha',
            'relationship': 'reported_influence', 'answer_id': answer_id, 'quote': TEXT,
            'direction': None, 'magnitude': None, **changes}


def provider(*, linked=False, clarified=False, mention_endpoints=True):
    def complete(prompt, payload, schema):
        original, latest = payload['answers'][0], payload['answers'][-1]
        terms = [('A operation', 'process'), ('Inline A CD의 alpha', 'width')]
        mentions = [EvaluationMention(term=term, answer_id=latest['id'] if clarified else original['id'],
            quote=latest['text'] if clarified else original['text'], knowledge_ids=[identity] if linked else [],
            explanation=latest['text'] if clarified else '') for term, identity in terms] if mention_endpoints else []
        return ExtractedKnowledge(name='A operation 관계', claims=[claim(original['id'])], mentions=mentions)
    return complete


def test_one_line_approval_preserves_unknowns_mapping_and_chat_evidence(env, monkeypatch):
    definition(env, 'process', 'A operation', aliases=['A operation'])
    definition(env, aliases=['Inline A CD의 alpha'])
    q = register_case(env, monkeypatch, project_id=17, text=TEXT, model=provider(linked=True))
    assert q['status'] == 'review' and not q['followups']
    d = draft(env)
    assert not d['issues']
    stored = d['proposal']['content']['details']['claims'][0]
    assert stored['direction'] is None and stored['magnitude'] is None
    assert 'inline_observations' not in d['proposal']['content']['details']
    assert len(q['interpretation']['terms']) == 2
    assert approve(env, d).status_code == 200
    found = cases(env, query='A operation alpha', project_id=17)['cases']
    assert len(found) == 1 and found[0]['details']['claims'] == [stored]
    assert cases(env, query='A operation alpha', project_id=18)['cases'] == []
    payload = evidence_payload({'cases': found})
    assert payload['sources'][0]['reported_relationships'] == [stored]
    assert payload['sources'][0]['kind'] == 'reported_relationship'
    assert any('관계 진술' in line for line in found[0]['limitations'])
    assert any(TEXT in f['text'] for f in payload['facts'])


def test_omitted_mapping_mentions_still_ask_then_natural_reply_can_approve(env, monkeypatch):
    q = register_case(env, monkeypatch, text=TEXT, model=provider(mention_endpoints=False))
    assert len(q['followups']) == 2
    assert {t['term'] for t in q['interpretation']['terms']} == {'A operation', 'Inline A CD의 alpha'}
    old = draft(env)
    monkeypatch.setattr(llm, 'complete', provider(clarified=True))
    response = env.client.post(BASE + f"/questions/{q['id']}/answers", json={
        'expected_revision': q['revision'], 'text': 'A operation은 전공정이고 Inline A CD의 alpha는 그 직후 선폭이다. 데이터 컬럼은 아직 미확인이다.'})
    assert response.status_code == 202
    current = env.client.get(BASE + '/questions/' + q['id']).json()
    assert not current['followups'] and all(t['status'] == 'explained' for t in current['interpretation']['terms'])
    assert approve(env, old).status_code == 409
    assert approve(env, draft(env)).status_code == 200
    assert current['interpretation']['details']['claims'][0]['magnitude'] is None


def test_edited_relationship_cannot_bypass_natural_answer_review(env, monkeypatch):
    register_case(env, monkeypatch, text=TEXT, model=provider())
    d = draft(env)
    changed = deepcopy(d['proposal'])
    changed['content']['details']['claims'][0]['relationship'] = 'co_movement'
    response = env.client.post(BASE + '/drafts/' + d['id'], json={
        'expected_revision': d['revision'], 'proposal': changed, 'note': '관계 수동 변경'})
    assert response.status_code == 200
    assert any('자연어 답변' in issue for issue in response.json()['issues'])
    assert approve(env, response.json()).status_code == 422


def test_direction_and_size_use_their_own_answer_not_existing_knowledge():
    answer = {'id': 'first', 'text': TEXT}
    reply = {'id': 'reply', 'text': 'R1 대비 R2에서 alpha가 2 nm 감소했다.'}
    _, passages = prepare({'answers': [answer, reply]})
    value = restore(EvaluationPassages(name='관계', claims=[{
        'source_term': 'A operation', 'target_term': 'Inline A CD의 alpha',
        'relationship': 'reported_influence', 'passage_id': 'a1p1',
        'direction': {'value': '감소', 'passage_id': 'a2p2'},
        'magnitude': {'value': '2 nm', 'passage_id': 'a2p2'}}]), passages)
    stored = grounded_claims(value.claims, [answer, reply])[0]
    assert stored['answer_id'] == 'first' and stored['magnitude']['answer_id'] == 'reply'
    assert stored['magnitude']['value'] == '2 nm'
    with pytest.raises(ValueError):
        grounded_claims(value.claims, [answer])
    with pytest.raises(ValueError):
        EvaluationClaim(**claim(target_term='EPM의 추정된 항목'))


def test_extraction_without_direction_never_fills_it_from_other_payload_data():
    payload = {'answers': [{'id': 'first', 'text': TEXT}], 'dataset': {'raw_rows': ['must-not-send']}}
    request, passages = prepare(payload)
    assert 'must-not-send' not in str(request)
    value = restore(EvaluationPassages(name='관계', claims=[{
        'source_term': 'A operation', 'target_term': 'Inline A CD의 alpha',
        'relationship': 'reported_influence', 'passage_id': 'a1p1'}]), passages)
    assert value.claims[0].direction is None and value.claims[0].magnitude is None


def test_legacy_content_normalization_does_not_change_immutable_results():
    saved = Content(name='이전 분석', details={'analysis_result': '원본 값'}).for_kind('evaluation')
    assert 'claims' not in saved['details']
    assert Content.model_validate(saved).for_kind('evaluation') == saved


def test_new_relationship_reuses_dataset_execution_result_review_and_knowledge(prepared, monkeypatch):
    from app import db as database
    from app.analytics import worker
    from app.analytics.models import AnalysisRun
    from app.evaluation_recommendation import facts_for
    from app.semantic.models import KnowledgeVersion
    env = prepared
    definition(env, 'process', 'A operation', aliases=['A operation'], bound=False)
    definition(env, aliases=['Inline A CD의 alpha'], bound=False)
    register_case(env, monkeypatch, text=TEXT, model=provider(linked=True))
    d = draft(env)
    approved = approve(env, d)
    assert approved.status_code == 200, approved.text
    env.reference = {'id': approved.json()['item_id'], 'version': approved.json()['item_version']}
    original = deepcopy(d['proposal']['content']['details']['claims'])
    monkeypatch.setattr(llm, 'complete', lambda prompt, payload, schema: existing_plan(payload))
    selected = start(env)
    assert selected['status'] == 'ready' and selected['dataset_id'] == 'a-data'
    response = prepare_dataset(env, selected)
    assert response.status_code == 201, response.text
    run = response.json()
    with env.sessions() as db:
        facts, _ = facts_for(db, db.get(AnalysisRun, run['id']))
        assert 'claims' in facts[0]['text'] and 'null' in facts[0]['text']
    assert execute(env, run).status_code == 202
    def calculate(*args):
        return fake_result(args[4]) | {'deltas': [{'column': 'alpha', 'baseline': 'Recipe1', 'comparison': 'Recipe2', 'delta': 2, 'percent_change': 100}]}
    monkeypatch.setattr('app.evaluation_compute.compute', calculate)
    assert worker.tick()
    done = env.client.get('/api/evaluation-analysis/' + run['id']).json()
    reviewed = env.client.post('/api/evaluation-analysis/' + run['id'] + '/submit-review', json={
        'expected_revision': done['revision'], 'workspace_token': database.workspace_token()})
    assert reviewed.status_code in (200, 201), reviewed.text
    result_draft = env.client.get(BASE + '/drafts/' + reviewed.json()['draft_id']).json()
    assert not result_draft['issues']
    finding = result_draft['question']['execution_evidence']['findings']
    assert finding['observed_differences'][0]['delta'] == 2 and finding['causal_effect'] is None
    assert approve(env, result_draft).status_code == 200
    with env.sessions() as db:
        saved = db.get(KnowledgeVersion, (env.reference['id'], env.reference['version']))
        assert saved.content['details']['claims'] == original
    found = cases(env, query='A operation alpha', dataset_id='a-data')['cases']
    assert any(c.get('execution_evidence', {}).get('run_id') == run['id'] for c in found)


@pytest.mark.parametrize('delta,direction', [(2, 'increase'), (-2, 'decrease'), (0, 'unchanged'), (None, None)])
def test_only_explicit_group_comparison_has_observed_direction(delta, direction):
    result = {'method': 'group_compare', 'deltas': [{'column': 'alpha', 'baseline': 'R1', 'comparison': 'R2', 'delta': delta, 'percent_change': None}]}
    original = deepcopy(result)
    findings = computed_findings(SimpleNamespace(result=result), {'labels': {'alpha': 'Inline oper A CD / alpha'}})
    assert findings['observed_differences'][0]['direction'] == direction
    assert findings['observed_differences'][0]['item'] == 'Inline oper A CD / alpha'
    assert findings['causal_effect'] is None and result == original


@pytest.mark.parametrize('method', ['correlation', 'ml_regression', 'group_compare'])
def test_correlation_importance_and_unspecified_baseline_are_not_effects(method):
    findings = computed_findings(SimpleNamespace(result={'method': method, 'ml': {'importance': 0.8}, 'correlation': {'pearson': 0.99}}), {})
    assert findings['causal_effect'] is None and findings['observed_differences'] == []
