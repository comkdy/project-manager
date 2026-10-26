"""Real configured AI, synthetic metadata only; no metadata/source writes."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.semantic import collector_config as llm
from app.evaluation_recommendation import PROMPT, Plan, validate_plan


def main():
    columns = [{'name': c, 'type': t} for c,t in [('recipe','text'),('equipment','text'),
        ('cd_a','double precision'),('cd_b','double precision'),('epm_1','double precision'),
        ('lot_id','text'),('wafer_id','text'),('epm_at','timestamp with time zone')]]
    facts = [
        {'reference':'case','kind':'case','label':'합성 공정 A 사례',
         'text':'공정 A에서 recipe를 변경하자 Inline alpha가 증가하고 EPM parameter1이 감소했다. equipment별 상태와 alpha를 함께 고려해 parameter1의 관계를 확인하고 싶다.'},
        {'reference':'knowledge:known','kind':'knowledge','label':'승인된 합성 데이터 설명',
         'text':'epm_1은 EPM parameter1 수치다. recipe와 equipment는 A 공정에서 사용한 Recipe 이름과 장비 구분이며 EPM 측정 전에 확정된다. cd_a와 cd_b는 서로 다른 공정의 Inline 선폭이다. 둘 다 EPM 측정 전 비파괴 계측값이다. alpha가 어느 선폭인지는 미확인이다. 한 행은 lot_id와 wafer_id가 가리키는 wafer의 평가 1건이며 공정 이력과 Inline은 해당 wafer의 EPM 이전 마지막 공정/계측과 대응했다. wafer 번호는 Lot마다 반복된다. 같은 Lot 표본끼리 연관될 수 있다. epm_at은 해당 결과의 EPM 측정 시각이다.'},
        {'reference':'dataset','kind':'dataset','label':'합성 평가 대응 데이터','text':json.dumps({'columns':columns},ensure_ascii=False)}]
    results=[]
    for step in range(2):
        plan=llm.complete(PROMPT,{'facts':facts,'columns':columns,'scope':{'project_id':1,'filters':[]}},Plan)
        validate_plan(plan,facts,columns)
        if step==0:
            assert plan.questions, 'Ambiguous alpha must be clarified'
        else:
            assert not plan.questions and plan.settings is not None, 'Approved explanation should resolve setup'
            assert plan.settings.method=='ml_regression', 'Multi-factor request has enough ML metadata'
            assert plan.settings.metrics==['epm_1'] and 'cd_a' in plan.settings.features and 'cd_b' not in plan.settings.features
            assert plan.settings.unit_columns==['lot_id'] and plan.settings.time_column=='epm_at'
        results.append(plan.model_dump(mode='json'))
        facts.append({'reference':'clarification:alpha','kind':'clarification','label':'검토·승인된 alpha 연결',
            'text':'이 사례의 alpha는 공정 A의 Inline 선폭 cd_a다. cd_b는 다른 공정이므로 이번 설명 변수에서 제외한다. cd_a, recipe, equipment는 모두 epm_1 측정 전에 확정된 값이다. lot_id를 독립 표본 묶음으로 사용하고 epm_at으로 과거 학습/이후 검증을 나눈다.'})
    (ROOT/'validation/analysis-recommendation-provider-response.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    report={'synthetic_only':True,'database_writes':False,'ambiguous_mapping_question':True,
        'approved_answer_to_ml_plan':True,'grounded_columns_and_quotes':True,'turns':2}
    (ROOT/'validation/analysis-recommendation-provider.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))


if __name__=='__main__':
    main()
