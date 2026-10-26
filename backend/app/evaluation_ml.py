"""Bounded local prediction experiment; no causal claims or automatic publication."""
import numpy as np
import pandas as pd
from fastapi import HTTPException
from sklearn import __version__ as sklearn_version
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

MAX_ML_ROWS = 20_000
METHOD_VERSION = 'knowledge-forest-time-group-v1'
SEED = 1729


def calculate_ml(rows, settings, columns, checkpoint=lambda: None):
    from .evaluation_analysis import numeric_type
    target, features = settings.metrics[0], settings.features
    frame = pd.DataFrame([dict(r) for r in rows], columns=[target, *features, *settings.unit_columns, settings.time_column])
    numeric = [c for c in features if numeric_type(next(v['type'] for v in columns if v['name'] == c))]
    categorical = [c for c in features if c not in numeric]
    for col in [target, *numeric]:
        frame[col] = pd.to_numeric(frame[col], errors='coerce').replace([np.inf, -np.inf], np.nan)
    frame[settings.time_column] = pd.to_datetime(frame[settings.time_column], utc=True, errors='coerce')
    clean = frame.dropna().copy()
    for col in categorical:
        clean[col] = clean[col].astype(str)
        if clean[col].nunique() > 30 or clean[col].str.len().max() > 500:
            raise HTTPException(422, f'{col}: 범주가 너무 많습니다. ID 대신 Recipe·장비 등 30개 이하의 구분을 선택하세요.')
    stats = []
    for col in [target, *numeric]:
        values = clean[col]
        correlation = clean[col].corr(clean[target]) if col != target and len(clean) > 1 and values.nunique() > 1 and clean[target].nunique() > 1 else None
        stats.append({'column': col, 'count': len(values), 'mean': safe(values.mean()),
                      'stddev': safe(values.std()), 'pearson_to_target': safe(correlation)})
    groups = [{'column': col, 'values': [{'value': str(key), 'count': len(values), 'target_mean': float(values[target].mean())}
               for key, values in clean.groupby(col, sort=True)]} for col in categorical]
    ml = {'target': target, 'valid_rows': len(clean), 'excluded_rows': len(frame) - len(clean),
          'statistics': stats, 'groups': groups, 'validation': None, 'metrics': None, 'importance': [],
          'estimator': 'RandomForestRegressor', 'library_version': sklearn_version, 'seed': SEED,
          'status': 'insufficient_data', 'interpretation': '독립된 과거·미래 표본이 부족하여 ML 관계를 판정하지 않았습니다.'}
    result = {'method_version': METHOD_VERSION, 'ml': ml, 'knowledge_eligible': bool(len(clean))}
    if len(clean) < 60 or clean[target].nunique() < 2:
        return result
    clean = clean.sort_values([settings.time_column, *settings.unit_columns, *features, target], kind='stable')
    times = clean[settings.time_column].drop_duplicates().sort_values()
    if len(times) < 4:
        return result
    cutoff = times.iloc[min(len(times) - 1, max(1, int(len(times) * .8)))]
    test = clean[clean[settings.time_column] >= cutoff]
    train = clean[clean[settings.time_column] < cutoff]
    # A Lot/wafer group straddling the time boundary belongs exclusively to validation.
    test_units = set(test[settings.unit_columns].itertuples(index=False, name=None))
    keep = [unit not in test_units for unit in train[settings.unit_columns].itertuples(index=False, name=None)]
    purged = len(train) - sum(keep)
    train = train.loc[keep]
    ml['validation'] = {'strategy': 'future_time_disjoint_units', 'cutoff': cutoff.isoformat(),
        'train_rows': len(train), 'test_rows': len(test), 'purged_rows': purged,
        'train_units': len(train[settings.unit_columns].drop_duplicates()), 'test_units': len(test_units),
        'unit_columns': settings.unit_columns, 'time_column': settings.time_column,
        'train_latest': train[settings.time_column].max().isoformat() if len(train) else None,
        'test_earliest': test[settings.time_column].min().isoformat() if len(test) else None}
    if len(train) < 40 or len(test) < 15 or ml['validation']['train_units'] < 8 or len(test_units) < 3 or train[target].nunique() < 2 or test[target].nunique() < 2:
        return result
    checkpoint()
    transforms = []
    if numeric:
        transforms.append(('numbers', 'passthrough', numeric))
    if categorical:
        transforms.append(('categories', OneHotEncoder(handle_unknown='ignore', sparse_output=False), categorical))
    model = Pipeline([('columns', ColumnTransformer(transforms)), ('forest', RandomForestRegressor(
        n_estimators=60, max_depth=8, min_samples_leaf=3, n_jobs=1, random_state=SEED))])
    model.fit(train[features], train[target])
    checkpoint()
    prediction = model.predict(test[features])
    mae = float(mean_absolute_error(test[target], prediction))
    baseline = float(mean_absolute_error(test[target], np.full(len(test), train[target].median())))
    better = mae < baseline
    ml['metrics'] = {'mae': mae, 'baseline_mae': baseline, 'r2': float(r2_score(test[target], prediction)), 'beats_baseline': better}
    # Original feature columns are permuted, keeping categorical dummy groups intact.
    importance = permutation_importance(model, test[features], test[target], scoring='neg_mean_absolute_error',
                                       n_repeats=3, random_state=SEED, n_jobs=1)
    checkpoint()
    ml['importance'] = sorted([{'column': col, 'mae_increase': float(importance.importances_mean[i]),
                               'std': float(importance.importances_std[i])} for i, col in enumerate(features)],
                              key=lambda v: -v['mae_increase']) if better else []
    ml['status'] = 'validated' if better else 'not_better_than_baseline'
    ml['interpretation'] = ('분리한 미래 표본에서 단순 중앙값 예측보다 오차가 작았습니다. 표시된 항목은 이 모델의 예측에 도움이 된 후보이며 원인 확정이 아닙니다.'
        if better else '분리한 미래 표본에서 단순 중앙값 예측보다 낫지 않았습니다. 영향 요인 순위를 제시하지 않습니다.')
    return result


def safe(value):
    return float(value) if value is not None and np.isfinite(value) else None
