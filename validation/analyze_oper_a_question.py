"""Wafer-level exploratory comparisons with Lot fixed effects; no causal claim."""
import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
source=json.loads((ROOT/'validation/oper-a-question-data.json').read_text(encoding='utf-8'))
frame=pd.DataFrame(source['wafer_rows'])
clean=frame[(frame.visits==1)&(frame.recipes==1)&(frame.unmatched_sites==0)&(frame.valid_sites>0)].copy()
assert len(clean)==len(frame)
items=[]
for (family,operation,parameter),part in clean.groupby(['family','operation','parameter']):
    grouped=part.groupby('recipe').value.agg(['count','mean','std']).reset_index()
    y=part.value.to_numpy(float)
    total=float(np.sum((y-y.mean())**2))
    between=sum(float(r['count'])*(float(r['mean'])-y.mean())**2 for r in grouped.to_dict('records'))
    # The same Lot may contain wafers with different Recipes. Remove each Lot's
    # mean from both the outcome and recipe dummies before fitting contrasts.
    x=pd.get_dummies(part.recipe,dtype=float).reindex(columns=['RCP2','RCP3'],fill_value=0)
    within_x=x-x.groupby(part.lot_id).transform('mean')
    within_y=part.value-part.groupby('lot_id').value.transform('mean')
    rank=int(np.linalg.matrix_rank(within_x.to_numpy()))
    beta=np.linalg.lstsq(within_x,within_y,rcond=None)[0] if rank==2 else None
    sse0=float(np.sum(within_y.to_numpy()**2))
    sse=float(np.sum((within_y.to_numpy()-within_x.to_numpy()@beta)**2)) if beta is not None else None
    items.append({'family':family,'operation':operation,'parameter':parameter,'wafers':len(part),
        'lots':int(part.lot_id.nunique()),'groups':grouped.to_dict('records'),
        'mean_range':float(grouped['mean'].max()-grouped['mean'].min()),
        'range_over_sd':float((grouped['mean'].max()-grouped['mean'].min())/part.value.std()),
        'between_recipe_variance_fraction':float(between/total) if total else None,
        'within_lot_recipe_r2':float(1-sse/sse0) if sse is not None and sse0 else None,
        'within_lot_contrasts_vs_rcp1':dict(zip(['RCP2','RCP3'],map(float,beta))) if beta is not None else None,
        'first_measurement':part.first_at.min(),'last_measurement':part.measured_at.max()})
report={'question':source['question'],'scope':{k:source[k] for k in ('interpretation','period_start','period_basis')},
    'method':'최근 측정값을 위치별로 선택해 wafer 평균을 만든 뒤 Recipe별 비교. 같은 Lot의 평균 차이를 제거한 선형 비교도 병행. 탐색 결과이며 인과·규격·유의성 판정 아님.',
    'excluded_wafer_metric_rows':len(frame)-len(clean),
    'metrics':sorted(items,key=lambda x:-(x['within_lot_recipe_r2'] or 0))}
(ROOT/'validation/oper-a-question-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
