"""Read-only diagnosis of the configured model's explanation response. No secrets logged."""
import asyncio,json,sys
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.assistant import ChatRequest
from app.chat_workflow import ModelAdapter
from app.llm_settings import llm_configuration,load_key,validate_llm_url
from app.evaluation_narrative import evidence_payload,validate_narrative

state=json.loads((ROOT/'validation/live-case-state.json').read_text(encoding='utf-8'))
answer=json.loads((ROOT/'validation/narrative-chat-live.json').read_text(encoding='utf-8'))['evaluation_cases']
payload=evidence_payload(answer); observations=[]
class ObservedClient(httpx.AsyncClient):
    async def post(self,*args,**kwargs):
        response=await super().post(*args,**kwargs)
        value=response.json(); choice=value.get('choices',[{}])[0]
        observations.append({'status':response.status_code,'finish_reason':choice.get('finish_reason'),
            'usage':value.get('usage'),'content':choice.get('message',{}).get('content')})
        return response

async def main():
    base,model=llm_configuration(); key,_=load_key(state['case_question']['answers'][0]['user_id'])
    request=ChatRequest(message=answer['search']['query'])
    async with ObservedClient(timeout=httpx.Timeout(90,connect=10),trust_env=False,follow_redirects=False) as client:
        adapter=ModelAdapter(client,validate_llm_url(base),model,{'Authorization':'Bearer '+key},request,{})
        try:
            plan,_=await adapter.run('evaluation_explanation',{'evidence':payload},validate=lambda p:validate_narrative(p,payload))
            print(json.dumps({'valid':True,'plan':plan.model_dump()},ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({'valid':False,'type':type(exc).__name__,'reason':str(exc) if isinstance(exc,ValueError) else 'provider failure'},ensure_ascii=False))
            if isinstance(exc.__cause__,ValueError):
                print(json.dumps({'validation_feedback':str(exc.__cause__)},ensure_ascii=False))
        finally:
            (ROOT/'validation/narrative-chat-diagnostic.json').write_text(json.dumps(observations,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps([{k:v for k,v in o.items() if k!='content'} for o in observations],ensure_ascii=False))
asyncio.run(main())
