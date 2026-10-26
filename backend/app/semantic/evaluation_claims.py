"""Reported relationships and computed observations have separate provenance."""
from .schemas import EvaluationClaim


def grounded_claims(claims, answers):
    by_id = {answer['id']: answer['text'] for answer in answers}
    result = []
    for value in claims:
        claim = EvaluationClaim.model_validate(value)
        for entry in (claim, claim.direction, claim.magnitude):
            if entry and (entry.answer_id not in by_id or entry.quote not in by_id[entry.answer_id]):
                raise ValueError('관계·방향·영향량의 답변 원문을 확인할 수 없습니다.')
        result.append(claim.model_dump())
    return result


def claims_issues(db, details):
    from .collector_models import KnowledgeAnswer
    claims = details.get('claims', [])
    if not claims:
        return []
    try:
        identities = {entry['answer_id'] for claim in claims
                      for entry in (claim, claim.get('direction'), claim.get('magnitude')) if entry}
        answers = [db.get(KnowledgeAnswer, identity) for identity in identities]
        if any(answer is None for answer in answers) or len({a.question_id for a in answers}) != 1:
            raise ValueError('서로 다른 요청의 원문을 합칠 수 없습니다.')
        grounded_claims(claims, [{'id': a.id, 'text': a.text} for a in answers])
    except (ValueError, KeyError, TypeError):
        return ['관계·방향·영향량은 같은 확인 요청의 자연어 답변을 근거로 정리해야 합니다.']
    return []


def computed_findings(run, identities):
    """Read-only projection of a completed run; never backfill a reported claim."""
    result = run.result
    method = result.get('method') or getattr(run, 'request', {}).get('settings', {}).get('method')
    labels = identities.get('labels', {})
    findings = {'method': method, 'causal_effect': None, 'observed_differences': [],
                'notice': '인과 효과는 계산하지 않았습니다.'}
    if method == 'group_compare':
        for delta in result.get('deltas', []):
            value = delta.get('delta')
            findings['observed_differences'].append({**delta, 'item': labels.get(delta['column'], delta['column']),
                'direction': None if value is None else 'increase' if value > 0 else 'decrease' if value < 0 else 'unchanged'})
        findings['notice'] = ('비교 그룹 평균 − 기준 그룹 평균인 관측 차이입니다. 조건을 통제한 인과 효과는 아닙니다.'
            if findings['observed_differences'] else '기준·비교 그룹을 지정한 차이는 계산하지 않았습니다. 방향·영향량은 미정입니다.')
    elif method == 'correlation':
        findings['notice'] = '상관계수는 함께 변하는 정도입니다. 공정 변경의 영향량이나 인과 효과가 아닙니다.'
    elif method == 'ml_regression':
        findings['notice'] = 'ML 중요도는 예측 오차에 대한 기여도입니다. 증가·감소 방향이나 공정 변경의 영향량은 계산하지 않았습니다.'
    return findings
