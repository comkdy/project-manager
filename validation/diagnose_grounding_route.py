"""Inspect the configured router with registered names, without logging credentials."""
import asyncio
import json
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.assistant import ChatRequest
from app.chat_workflow import ModelAdapter
from app.llm_settings import llm_configuration, load_key, validate_llm_url


def get(path):
    with urllib.request.urlopen('http://127.0.0.1:8000/api' + path) as response:
        return json.load(response)


state = json.loads((ROOT / 'validation/live-case-state.json').read_text(encoding='utf-8'))
catalog = get('/analytics/catalog')
catalog['projects'] = get('/bootstrap')['projects']
observations = []


class ObservedClient(httpx.AsyncClient):
    async def post(self, *args, **kwargs):
        response = await super().post(*args, **kwargs)
        choice = response.json().get('choices', [{}])[0]
        observations.append({'status': response.status_code, 'finish': choice.get('finish_reason'),
            'content': choice.get('message', {}).get('content')})
        return response


async def main():
    base, model = llm_configuration()
    key, _ = load_key(state['case_question']['answers'][0]['user_id'])
    cases = [
        ('XXXA에서 operA 에 따른 특성 변화가 관측돼?', 'analysis'),
        ('XXXB의 oper B에서 레시피별로 특성이 다르게 나와?', 'analysis'),
        ('XXXA oper A의 Recipe별 EPM PARA1 평균을 비교해줘.', 'analysis'),
        ('승인된 평가 사례와 ML 결과를 바탕으로 중요한 항목과 다음 확인 사항을 설명해줘.', 'evaluation_cases'),
        ('XXXA의 EPM PARA1을 볼 수 있는 데이터셋을 만들어줘.', 'dataset'),
        ('지금 데이터셋으로 Recipe별 비교 카드를 만들어줘.', 'card'),
        ('wafer_his에서 oper A를 조회할 SQL을 작성해줘.', 'query'),
    ]
    results = []
    async with ObservedClient(timeout=90, trust_env=False, follow_redirects=False) as client:
        for message, expected in cases:
            request = ChatRequest(message=message, dataset_id='auto_69e8016a5d10b686efccb1e7dd7153968b3df965ea2f9612')
            adapter = ModelAdapter(client, validate_llm_url(base), model, {'Authorization': 'Bearer ' + key}, request,
                {'project_id': None, 'selected_dataset_id': request.dataset_id, 'selected_card': None},
                routing_loader=lambda: catalog)
            result, _ = await adapter.run('route')
            actual = result.intent if result.kind == 'route' else result.kind
            results.append({'message': message, 'expected': expected, 'actual': actual,
                'project_id': getattr(adapter, 'analysis_request', request).project_id, 'plan': result.model_dump()})
        report = {'provider': urlsplit(base).hostname, 'model': model, 'catalog_series': len(catalog['series']),
            'catalog_operations': len(catalog['recipe_operations']), 'project_names': [p['name'] for p in catalog['projects']],
            'results': results, 'observations': observations}
        (ROOT / 'validation/chat-grounding-diagnostic.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps([{k: v for k, v in r.items() if k != 'plan'} for r in results], ensure_ascii=False))
        assert all(r['actual'] == r['expected'] for r in results)


asyncio.run(main())
