"""Configured model, synthetic language/definitions only; no business DB writes."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.semantic import collector_config as llm
from app.semantic.collector_schemas import ExtractedKnowledge
from app.semantic.collector_service import grounded_proposal
from app.semantic.evaluation_terms import resolve_mentions, term_questions


def main():
    from app.semantic.evaluation_extraction import EvaluationPassages
    original_validate = EvaluationPassages.model_validate_json
    def capture(cls, text, *args, **kwargs):
        (ROOT / 'validation/relationship-provider-last-wire.json').write_text(text, encoding='utf-8')
        return original_validate(text, *args, **kwargs)
    EvaluationPassages.model_validate_json = classmethod(capture)
    examples = [
        ('unknown', 'A operation은 Inline A CD의 alpha에 영향을 준다.'),
        ('explicit', 'A operation의 Recipe를 R1에서 R2로 바꾸니 Inline A CD의 alpha가 2 nm 감소했다.'),
        ('chain', 'A operation의 Recipe는 Inline A CD의 alpha에 영향을 준다. Inline A CD의 alpha는 EPM parameter1에 영향을 준다.'),
        ('association', 'Inline A CD의 alpha와 EPM parameter1은 함께 증가했고 상관계수는 0.9였다.'),
    ]
    results = []
    if '--association-only' in sys.argv:
        examples = examples[-1:]
    for name, text in examples:
        # A supplied prior direction must NOT populate the new user's statement.
        approved = [{'id': 'synthetic-a', 'version': 1, 'kind': 'term', 'name': 'A operation', 'aliases': ['A operation'],
            'description': 'A 공정이다. 별도 과거 평가에서 5 nm 증가가 보고되었다.',
            'binding_version': None, 'binding_fingerprint': '', 'binding': None}]
        answers = [{'id': 'synthetic-' + name, 'text': text}]
        payload = {'kind': 'evaluation', 'answers': answers, 'resolve_terms': True,
                   'approved_knowledge': approved, 'case_context': {'project_id': None}}
        extracted = llm.complete('', payload, ExtractedKnowledge)
        proposal, _ = grounded_proposal(SimpleNamespace(kind='evaluation'), answers, extracted)
        claims = proposal['content']['details'].get('claims', [])
        terms = resolve_mentions(answers, extracted.mentions, {d['id']: d for d in approved})
        results.append({'example': name, 'text': text, 'claims': claims, 'terms': terms, 'questions': term_questions(terms)})
        (ROOT / 'validation/relationship-provider-response.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        assert claims, name
        if name in {'unknown', 'chain'}:
            assert all(c['direction'] is None and c['magnitude'] is None for c in claims), name
        if name == 'unknown':
            assert len(claims) == 1 and term_questions(terms)
        if name == 'explicit':
            assert any(c['direction'] and '감소' in c['direction']['value'] and c['magnitude'] and '2' in c['magnitude']['value'] for c in claims)
        if name == 'chain':
            assert len(claims) == 2
            assert not any('Recipe' in c['source_term'] and 'EPM' in c['target_term'] for c in claims)
        if name == 'association':
            assert all(c['relationship'] == 'co_movement' and c['magnitude'] is None for c in claims)
        print(name + ': passed', flush=True)
    report = {'model': llm.configuration()[2], 'examples': len(results), 'passed': True,
              'source_rows_sent': 0, 'business_metadata_writes': 0,
              'checks': ['unknown remains unknown despite prior knowledge', 'explicit source direction/size', 'no transitive edge', 'correlation is not magnitude', 'unknown mapping asks a question']}
    (ROOT / 'validation/relationship-provider.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
