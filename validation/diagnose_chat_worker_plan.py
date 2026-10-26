"""Inspect real model planning and repair without executing or publishing a run."""
import asyncio
import json
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.assistant import ChatRequest
from app.chat_data_analysis import context_for,validate
from app.chat_grounding import routing_grounding
from app.chat_workflow import ModelAdapter
from app.database_settings import load_active_source
from app.llm_settings import llm_configuration,load_key,validate_llm_url

def local(path):
    with urllib.request.urlopen('http://127.0.0.1:8000/api'+path) as response:return json.load(response)

load_active_source()
project=next(p for p in local('/bootstrap')['projects'] if p['code']=='XXXA')
actor=local('/auth/me')['id']
scenario=sys.argv[1] if len(sys.argv)>1 else 'correlation'
message=('이 데이터셋에서 장비 상태를 설명 항목으로 EPM PARA3와의 관계를 ML로 분석해줘. 장비 상태가 어느 데이터나 컬럼을 뜻하는지는 아직 정하지 않았어.' if scenario=='ambiguous' else
    '이 데이터셋에서 inline_main과 epm_para3의 상관관계를 현재 데이터로 계산하고 설명해줘. 각 행은 같은 lot_id, wf_id의 wafer 단위 대응이고 inline_main은 EPM 측정 이전 값이야. 추가 기간이나 필터 없이 데이터셋 전체 범위를 사용해줘.')
request=ChatRequest(message=message,
    project_id=project['id'],dataset_id='aa3f3390-57a2-41b9-a185-a045dd3757c3')
payload,_,_=context_for(request,actor)

def assert_no_rows(value):
    if isinstance(value,dict):
        assert not {'rows','sample_rows','preview','query_result','records','dataframe'} & value.keys()
        for child in value.values():assert_no_rows(child)
    elif isinstance(value,list):
        for child in value:assert_no_rows(child)

assert_no_rows(payload)
for fact in payload['facts']:
    if fact['kind'] not in {'user','clarification'}:assert_no_rows(json.loads(fact['text']))
url,model=llm_configuration()
assert urlsplit(url).hostname=='openrouter.ai'
key,_=load_key(actor)
records=[]
class AuditedClient:
    def __init__(self,client):self.client=client
    async def post(self,endpoint,*,headers,json):
        assert urlsplit(endpoint).hostname=='openrouter.ai'
        response=await self.client.post(endpoint,headers=headers,json=json)
        records.append({'request':json,'response':response.json(),'raw_measurement_rows_sent':False})
        return response

async def main():
    async with httpx.AsyncClient(timeout=90,trust_env=False,follow_redirects=False) as client:
        adapter=ModelAdapter(AuditedClient(client),validate_llm_url(url),model,{'Authorization':'Bearer '+key},request,
            {'project_id':project['id'],'selected_dataset_id':request.dataset_id,'selected_card':None,'analysis_grounding':routing_grounding(request)})
        try:
            value,_=await adapter.run('data_analysis_plan',{'data_analysis_context':payload},validate=lambda v:validate(v,payload))
            print('VALIDATED '+value.plan.summary)
        except Exception as exc:print(type(exc).__name__+': '+str(exc))
    filename='chat-worker-plan-diagnostic.json' if scenario=='correlation' else f'chat-worker-{scenario}-diagnostic.json'
    (ROOT/'validation'/filename).write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    for i,record in enumerate(records):
        message=record['response'].get('choices',[{}])[0].get('message',{})
        print(json.dumps({'attempt':i+1,'content':message.get('content'),'finish_reason':record['response'].get('choices',[{}])[0].get('finish_reason'),
            'repair_feedback':record['request']['messages'][-1]['content'][:180] if i else None},ensure_ascii=False))

asyncio.run(main())
