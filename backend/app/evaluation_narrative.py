"""Cited interpretations of approved evidence, never new measurements or actions."""
import hashlib
import json
import re
from typing import Literal

from pydantic import Field

from .schemas import StrictModel

FIELDS = {'description': '사례 요약', 'operation': '공정', 'recipe_changes': 'Recipe 평가',
          'inline_observations': 'Inline 관측', 'epm_observations': 'EPM 관측',
          'analysis_result': '승인된 계산 결과', 'conditions': '적용 조건',
          'evidence': '평가 근거', 'limitations': '해석 범위'}
NOTICE = '승인된 기록을 해석한 설명입니다. 이번 질문에서 새로 계산한 결과나 확정된 원인은 아닙니다.'
UNAVAILABLE = '자연어 설명의 근거를 확인하지 못했습니다. 아래 승인 자료와 추가 분석 제안은 계속 확인할 수 있습니다.'


class Citation(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=20)


class Statement(StrictModel):
    text: str = Field(min_length=1, max_length=700)
    citations: list[Citation] = Field(min_length=1, max_length=5)


class Narrative(StrictModel):
    kind: Literal['evaluation_explanation']
    findings: list[Statement] = Field(min_length=1, max_length=3)
    next_checks: list[Statement] = Field(min_length=1, max_length=2)
    caveats: list[Statement] = Field(min_length=1, max_length=2)


def evidence_chunks(value):
    """Offer small, unchanged passages; the model selects IDs, never copies quotes."""
    remaining = value.strip()
    while remaining:
        end = len(remaining)
        if end > 1400:
            boundary = remaining.rfind('\n', 0, 1400)
            if boundary < 400:
                boundary = remaining.rfind(' ', 0, 1400)
            end = boundary if boundary >= 400 else 1400
        yield remaining[:end]
        remaining = remaining[end:].lstrip()


def evidence_payload(answer):
    sources, facts = [], []
    # Stable ordering means a harmless retrieval rank change does not change citations.
    for case in sorted(answer.get('cases', []), key=lambda c: (c['id'], c['version'])):
        execution = case.get('execution_evidence')
        if execution and execution.get('unavailable'):
            continue
        reference = {'id': case['id'], 'version': case['version']}
        sources.append({'case_ref': reference, 'name': case['name'],
                        'kind': 'approved_computation' if execution else 'reported_relationship' if case.get('details', {}).get('claims') else 'reported_observation',
                        'dataset': case.get('dataset'), 'case_context': case.get('case_context'),
                        'reported_relationships': case.get('details', {}).get('claims', []),
                        'relationship_notice': '관계 진술의 null 방향·영향량은 미정이다. 계산 근거로 원문을 채우거나 예측 중요도를 영향량으로 해석하지 않는다.',
                        'execution': execution, 'limitations': case.get('limitations', [])})
        for key, label in FIELDS.items():
            value = case.get('description', '') if key == 'description' else case.get('details', {}).get(key, '')
            if not isinstance(value, str) or not value.strip():
                continue
            for excerpt in evidence_chunks(value):
                facts.append({'id': f'E{len(facts)+1}', 'case_ref': reference, 'case_name': case['name'],
                              'field': key, 'label': label, 'text': excerpt})
        if execution and execution.get('measurement_items', {}).get('items'):
            labels = execution['measurement_items']['labels']
            facts.append({'id': f'E{len(facts)+1}', 'case_ref': reference, 'case_name': case['name'],
                'field': 'measurement_identity', 'label': '원본 분석의 측정·공정 항목',
                'text': '원본 실행에 보존된 데이터셋 SQL의 항목 식별: ' + '; '.join(f'{c} = {label}' for c, label in labels.items()) +
                '. Inline은 operation+parameter 단위이며 같은 parameter를 다른 공정까지 일반화하지 않습니다.'})
    payload = {'sources': sources, 'facts': facts, 'scope': answer.get('search', {}),
               'scope_notices': answer.get('warnings', [])}
    if len(json.dumps(payload, ensure_ascii=False)) > 65000:
        raise ValueError('승인 근거가 너무 깁니다. 공정·PARA 범위를 좁혀 주세요.')
    return payload


def fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def numbers(text):
    # Numeric claims must be copied from their cited evidence, not freshly calculated.
    return set(re.findall(r'(?<![\w.])[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?%?', text))


def validate_narrative(plan, payload):
    facts = {f['id']: f for f in payload['facts']}
    known_inline = [item for source in payload['sources']
        for item in (source.get('execution') or {}).get('measurement_items', {}).get('items', []) if item['family']=='inline']
    errors = []
    for index, statement in enumerate([*plan.findings, *plan.next_checks, *plan.caveats], 1):
        quotes = []
        for citation in statement.citations:
            fact = facts.get(citation.evidence_id)
            if not fact:
                errors.append(f'{index}번째 문단의 {citation.evidence_id} 근거가 없습니다. 제공된 facts.id를 선택하세요.')
            else:
                quotes.append(fact['text'])
        unsupported = numbers(statement.text) - numbers(' '.join(quotes))
        cited_cases = {facts[c.evidence_id]['case_ref']['id'] for c in statement.citations if c.evidence_id in facts}
        inline_items = [item for source in payload['sources'] if source['case_ref']['id'] in cited_cases
            for item in (source.get('execution') or {}).get('measurement_items', {}).get('items', []) if item['family'] == 'inline']
        mentions_inline = ('inline' in statement.text.lower() or '인라인' in statement.text or
            any(re.search(r'(?<![A-Za-z0-9_])' + re.escape(item['parameter']) + r'(?![A-Za-z0-9_])', statement.text, re.I) for item in known_inline))
        if known_inline and mentions_inline:
            if inline_items and not any(item['operation'].casefold() in statement.text.casefold() and item['parameter'].casefold() in statement.text.casefold() for item in inline_items):
                errors.append(f'{index}번째 문단에서 Inline 항목의 공정명을 생략하지 마세요. operation+parameter 쌍으로 설명하세요: ' +
                    ', '.join(item['label'] for item in inline_items))
            elif not inline_items and not re.search(r'공정.{0,12}(미확인|불명확|확인되지|명시되지|알 수 없)', statement.text):
                errors.append(f'{index}번째 문단은 측정 항목이 확인된 계산 근거를 인용하고 operation+parameter를 함께 명시하세요. 과거 사례의 측정 공정을 모르면 공정 미확인이라고 밝히고 MAIN을 일반화하지 마세요.')
        ratios = re.findall(r'(?:수십|수백|수천|몇|두|세|네|한|십|백|천)\s*배', statement.text)
        if any(ratio not in ' '.join(quotes) for ratio in ratios):
            errors.append(f'{index}번째 문단에서 근거에 없는 배수·비율을 새로 계산하거나 추정하지 마세요. 관측된 수치 또는 정성적 비교만 설명하세요.')
        if unsupported:
            errors.append(f'{index}번째 문단의 수치 {", ".join(sorted(unsupported))}는 해당 인용에 없습니다. 내부 식별자는 생략하고 실제 수치는 정확한 근거를 인용하세요. 새 계산·반올림은 하지 마세요.')
        if re.search(r'https?://|```|<script', statement.text, re.I):
            errors.append(f'{index}번째 문단을 링크나 실행 코드가 없는 자연어로 작성하세요.')
        # Fail closed on common categorical causal claims; the prompt also requires
        # observational language and a mandatory server-owned scope notice.
        for sentence in re.split(r'[.!?。\n]|(?<=다)[,;]', statement.text):
            if re.search(r'원인(?:은|이다|입니다|임|으로\s*확정)|인과.{0,12}(입증|증명|확정)|때문에.{0,15}(발생|변했|증가했|감소했)|causes?\b|causal\s+(?:effect|proof)', sentence, re.I):
                if not re.search(r'않|못|없|아[니닙닌님닐]|불가|미확정|not |cannot ', sentence, re.I):
                    errors.append(f'{index}번째 문단에서 관측·예측 관계를 원인으로 단정하지 말고, 사실과 추가 검증할 가설을 구분하세요.')
            if re.search(r'일관된?\s*경향|일관되게|단조(?:롭게|\s*증가|\s*감소)|예외\s*없이', sentence):
                if not re.search(r'확인.{0,8}필요|검토|여부|인지|않|없|아[니닙닌님닐]', sentence) and not re.search(r'일관|단조|예외\s*없이', ' '.join(quotes)):
                    errors.append(f'{index}번째 문단에서 그룹별 차이를 일관되거나 단조로운 경향으로 확대하지 마세요. 근거에 명시된 관측과 예측 기여만 설명하세요.')
    if errors:
        raise ValueError('\n'.join(errors))
    return plan


def attach_narrative(answer, plan, payload):
    validate_narrative(plan, payload)
    if fingerprint(evidence_payload(answer)) != fingerprint(payload):
        raise ValueError('설명 준비 중 승인 근거 또는 데이터 범위가 변경되었습니다.')
    facts = {f['id']: f for f in payload['facts']}
    def render(statement):
        return {'text': statement.text, 'citations': [
            {**{k: facts[c.evidence_id][k] for k in ('case_ref', 'case_name', 'field', 'label')}, 'quote': facts[c.evidence_id]['text']}
            for c in statement.citations]}
    answer['narrative'] = {'status': 'ready', 'notice': NOTICE,
                          **{key: [render(s) for s in getattr(plan, key)] for key in ('findings', 'next_checks', 'caveats')}}
    answer['explanation'] = '승인된 사례와 분석 결과를 바탕으로 보면 다음과 같습니다.'
    # Kept in private history only; restored against current evidence before display.
    answer['_narrative_record'] = {'version': 1, 'basis': fingerprint(payload), 'plan': plan.model_dump(mode='json')}
    return answer


def restore_narrative(answer, record):
    try:
        payload = evidence_payload(answer)
        if record.get('version') != 1 or record.get('basis') != fingerprint(payload):
            raise ValueError('changed evidence')
        return attach_narrative(answer, Narrative.model_validate(record['plan']), payload)
    except (ValueError, KeyError, TypeError):
        answer['narrative'] = {'status': 'unavailable', 'message': '승인 근거나 데이터 범위가 바뀌어 이전 설명을 표시하지 않습니다. 현재 조건으로 다시 질문해 주세요.'}
        return answer
