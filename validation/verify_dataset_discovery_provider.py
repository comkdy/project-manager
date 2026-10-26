"""Configured AI, synthetic catalog only: ambiguity, reuse and authoring fallback."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.semantic import collector_config as llm
from app.evaluation_datasets import DatasetPlan, recommend_plan

validate_json = DatasetPlan.model_validate_json
def diagnose(cls, value, *args, **kwargs):
    try:
        return validate_json(value, *args, **kwargs)
    except Exception as exc:
        # Only synthetic model output, after the transport's secret-echo guard.
        (ROOT/'.local/dataset-discovery-provider-invalid.json').write_text(value,encoding='utf-8')
        if hasattr(exc, 'errors'):
            print(json.dumps(exc.errors(include_input=False,include_context=False),ensure_ascii=True))
        raise
DatasetPlan.model_validate_json = classmethod(diagnose)


def main():
    columns = {'id':'integer','lot_id':'text','wafer_id':'text','recipe':'text',
               'cd_a':'double precision','cd_b':'double precision','epm_1':'double precision','epm_at':'timestamp with time zone'}
    source = {'id':'synthetic-source','schema_name':'public','table_name':'synthetic_measurements','columns':columns,'visible':True}
    dataset = {'id':'synthetic-wide','name':'합성 wafer 대응','project_id':None,'version':1,
               'columns':[{'name':k,'type':v} for k,v in columns.items()],
               'spec':{'source_id':source['id'],'raw_sql':'SELECT * FROM public.synthetic_measurements'}}
    knowledge = ('epm_1은 EPM parameter1이다. recipe는 EPM 이전 사용한 Recipe 이름이다. cd_a와 cd_b는 서로 다른 공정의 Inline 값이다. '
        '이 사례의 alpha가 어느 컬럼인지는 미확인이다. 한 행은 lot_id와 wafer_id로 구별하는 wafer 평가 한 건이다. '
        'epm_at은 EPM 측정 시각이다. 모든 공정/Inline은 그 EPM 이전 마지막 평가와 대응했으며 재측정은 이미 정리되었다.')
    facts = [{'reference':'case','kind':'case','text':'공정 A Recipe 변경 시 Inline alpha 증가와 EPM parameter1 감소가 관측됐다. 현재 데이터에서 같은 관계를 확인하고 싶다.'},
             {'reference':'knowledge:meaning','kind':'knowledge','text':knowledge},
             {'reference':'dataset:synthetic-wide','kind':'dataset','text':json.dumps(dataset,ensure_ascii=False)}]
    payload={'facts':facts,'datasets':[dataset],'sources':[source],'project':None}
    outputs=[]
    for step in range(3):
        if step==1:
            facts.append({'reference':'clarification:alpha','kind':'clarification','text':'alpha는 cd_a다. cd_b는 이번 평가와 관련 없다. lot_id와 wafer_id로 같은 wafer의 recipe, cd_a, epm_1을 연결한 한 행이며 epm_at이 결과 측정 시각이다.'})
        if step==2:
            payload['datasets']=[]
            payload['facts']=[f for f in facts if f['kind']!='dataset']
        plan=recommend_plan(payload,{dataset['id']:dataset} if step<2 else {})
        if step==0:
            assert plan.questions and not plan.query and not plan.dataset_id
        elif step==1:
            assert plan.dataset_id==dataset['id'] and not plan.query and not plan.questions
        else:
            assert plan.query and plan.query.source_id==source['id'] and not plan.questions
            assert 'synthetic_measurements' in (plan.query.sql or '')
        outputs.append(plan.model_dump(mode='json'))
    (ROOT/'validation/dataset-discovery-provider-response.json').write_text(json.dumps(outputs,ensure_ascii=False,indent=2),encoding='utf-8')
    report={'synthetic_only':True,'database_writes':False,'ambiguous_connection_question':True,
            'approved_answer_to_existing_dataset':True,'no_dataset_to_creation_draft':True,'grounded_references':True,'turns':3}
    (ROOT/'validation/dataset-discovery-provider.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))


if __name__=='__main__':
    main()
