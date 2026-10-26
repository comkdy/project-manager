"""Bounded database-side descriptive calculations; no raw rows sent to an LLM."""
import json
import math

from fastapi import HTTPException
from sqlalchemy import text

from .analytics.data import analysis_connection
from .expressions import quote_output
from .query import clean
from .evaluation_analysis import MAX_GROUPS, MAX_ROWS, METHOD_VERSION, WARNINGS


def finite(value):
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise HTTPException(422, "계산 결과가 수치 범위를 벗어났습니다. 값의 단위와 범위를 확인하세요.")
    return number


def value_sql(name):
    col = quote_output(name)
    return f"CASE WHEN CAST({col} AS text) NOT IN ('NaN', 'Infinity', '-Infinity') THEN CAST({col} AS double precision) END"


def _summary(result, stored):
    selected = stored["_evaluation_dataset"]
    scope = stored["_evaluation_scope"]
    filters = json.dumps(scope["filters"], ensure_ascii=False, separators=(",", ":"))
    if len(filters) > 700:
        filters = filters[:700] + "… (전체 조건은 연결된 실행에서 확인)"
    lines = [f"데이터셋: {selected['name']} (버전 {selected['version']}).",
             f"조회 시각: {result['captured_at']}. 데이터셋 행 수: {result['row_count']}.",
             f"적용 필터: {filters}."]
    if scope.get("period"):
        start = scope["period"]["start"]
        lines.append(f"조회 기간: {start['year']}년 ISO {start['week']}주 월요일 00:00(한국시간) 이후, 기준 {scope['period']['label']}.")
    else:
        lines.append("별도 대시보드 기간 조건 없음. 저장된 데이터셋의 조건을 그대로 사용했습니다.")
    def fmt(value):
        return "계산 불가" if value is None else format(value, ".8g")
    if result["method"] == "group_compare":
        lines.append(f"그룹별 관측 통계: {stored['settings']['group_column']}. 전체 {len(result['groups'])}개 그룹.")
        for group in result["groups"][:8]:
            label = "결측 그룹" if group["value"] is None else str(group["value"])[:100]
            for metric in group["metrics"]:
                lines.append(f"{label} / {metric['column']}: 유효 {metric['valid_count']}행, 제외 {metric['missing_count']}행, "
                             f"평균 {fmt(metric['mean'])}, 표본 표준편차 {fmt(metric['stddev'])}, "
                             f"최솟값 {fmt(metric['min'])}, 최댓값 {fmt(metric['max'])}.")
        if len(result["groups"]) > 8:
            lines.append("위에는 앞 8개 그룹만 요약했습니다. 나머지 그룹은 연결된 실행의 결과 표에서 확인하세요.")
        for item in result["deltas"]:
            lines.append(f"{item['column']}: {item['comparison'][:100]} 평균 − {item['baseline'][:100]} 평균 = {fmt(item['delta'])}; "
                         f"기준 평균 대비 {fmt(item['percent_change'])}%.")
    else:
        item = result["correlation"]
        lines.append(f"{item['x']}와 {item['y']}: 같은 행의 유효한 수치 쌍 {item['pair_count']}개, 제외 {item['excluded_count']}행. "
                     f"Pearson 상관계수 {fmt(item['pearson'])}.")
    lines.append("관측 통계이며 독립 실험 수·통계적 유의성·인과관계를 확정하지 않습니다.")
    summary = "\n".join(lines)
    if len(summary) > 5800:
        raise HTTPException(422, "결과 요약이 너무 깁니다. 그룹이나 컬럼 범위를 줄여 주세요.")
    return summary


def compute(cte, params, settings, columns, stored, checkpoint=lambda: None):
    from .measurement_items import validate_items
    identities = validate_items(stored['_evaluation_dataset'], settings, stored['_evaluation_scope']['filters'])
    output_names = {c["name"].lower().split("__")[-1] for c in columns}
    source_names = {name.lower() for source in stored["_evaluation_dataset"]["sources"] for name in source["columns"]}
    if (any(m.lower().split("__")[-1] == "value" and m not in identities['labels'] for m in [*settings.metrics, *settings.features])
            and source_names.intersection({"parameter", "para"}) and not output_names.intersection({"parameter", "para"})):
        raise HTTPException(422, "측정값 value에서 PARA 구분이 빠져 있어 여러 PARA가 섞였는지 확인할 수 없습니다. PARA 컬럼을 포함해 한 값으로 필터하거나, PARA별 수치 컬럼으로 구성한 데이터셋을 선택하세요.")
    params = {**params, "evaluation_row_limit": MAX_ROWS + 1, "evaluation_group_limit": MAX_GROUPS + 1}
    # One repeatable-read transaction ties counts, comparisons and provenance to
    # the same source snapshot. Only aggregate rows leave the database.
    bounded = cte + ", evaluation_rows AS MATERIALIZED (SELECT * FROM filtered LIMIT :evaluation_row_limit)"
    with analysis_connection() as connection:
        captured_at = clean(connection.scalar(text("SELECT transaction_timestamp()")))
        checkpoint()
        count = int(connection.scalar(text(bounded + " SELECT count(*) FROM evaluation_rows"), params))
        if count > MAX_ROWS:
            raise HTTPException(422, f"선택한 범위가 {MAX_ROWS:,}행을 초과했습니다. 조건을 좁혀 주세요. 일부 행만 계산하지 않았습니다.")
        result = {"method": settings.method, "method_version": METHOD_VERSION, "captured_at": captured_at,
                  "row_count": count, "warnings": list(WARNINGS), "knowledge_eligible": False}
        result['data_fingerprint'] = fingerprint_rows(connection, bounded, params)
        # Long measurement tables must be narrowed to a single series before
        # averaging values or computing pairs. Null identifiers are ambiguous.
        identifiers = [c["name"] for c in columns if c["name"].lower().split("__")[-1] in {"parameter", "para", "operation"}]
        for column in identifiers:
            checkpoint()
            name = quote_output(column)
            distinct, missing = connection.execute(text(bounded +
                f" SELECT count(DISTINCT {name}), count(*) FILTER (WHERE {name} IS NULL) FROM evaluation_rows"), params).one()
            if count and (distinct > 1 or missing):
                raise HTTPException(422, f"{column}에 여러 값이나 미지정 값이 포함되어 있습니다. 한 공정·PARA로 필터를 좁히거나, PARA별 수치 컬럼으로 구성한 데이터셋을 선택하세요.")
        checkpoint()
        if settings.method == "group_compare":
            group = f"CAST({quote_output(settings.group_column)} AS text)"
            expressions = []
            for index, column in enumerate(settings.metrics):
                value = value_sql(column)
                expressions += [f"count({value}) AS n{index}", f"avg({value}) AS mean{index}",
                                f"stddev_samp({value}) AS std{index}", f"min({value}) AS min{index}", f"max({value}) AS max{index}"]
            rows = connection.execute(text(bounded + f" SELECT {group} AS label, count(*) AS rows, " + ", ".join(expressions)
                + f" FROM evaluation_rows GROUP BY {group} ORDER BY {group} NULLS FIRST LIMIT :evaluation_group_limit"), params).mappings().all()
            if len(rows) > MAX_GROUPS:
                raise HTTPException(422, f"비교 그룹이 {MAX_GROUPS}개를 초과했습니다. 구분 컬럼이나 필터를 다시 선택하세요.")
            groups = []
            for row in rows:
                metrics = [{"column": column, "valid_count": row[f"n{index}"], "missing_count": row["rows"] - row[f"n{index}"],
                            "mean": finite(row[f"mean{index}"]), "stddev": finite(row[f"std{index}"]),
                            "min": finite(row[f"min{index}"]), "max": finite(row[f"max{index}"])}
                           for index, column in enumerate(settings.metrics)]
                groups.append({"value": row["label"], "row_count": row["rows"], "metrics": metrics})
            result.update(groups=groups, deltas=[])
            if settings.baseline is not None:
                selected = {g["value"]: g for g in groups}
                if settings.baseline not in selected or settings.comparison not in selected:
                    raise HTTPException(422, "입력한 기준 그룹 또는 비교 그룹이 현재 범위에 없습니다. 구분 값을 정확히 확인하세요.")
                for a, b in zip(selected[settings.baseline]["metrics"], selected[settings.comparison]["metrics"]):
                    delta = finite(b["mean"] - a["mean"]) if a["mean"] is not None and b["mean"] is not None else None
                    percent = finite(100 * delta / abs(a["mean"])) if delta is not None and a["mean"] else None
                    result["deltas"].append({"column": a["column"], "baseline": settings.baseline, "comparison": settings.comparison,
                                             "delta": delta, "percent_change": percent})
            result["knowledge_eligible"] = any(m["valid_count"] > 0 for g in groups for m in g["metrics"])
            if any(m["valid_count"] < 2 for g in groups for m in g["metrics"]):
                result["warnings"].append("유효 값이 2개 미만인 그룹은 분산을 평가할 수 없습니다.")
        elif settings.method == "correlation":
            x, y = (value_sql(column) for column in settings.metrics)
            row = connection.execute(text(bounded + f", pairs AS (SELECT {x} AS x, {y} AS y FROM evaluation_rows) "
                "SELECT count(*) AS n, corr(x,y) AS pearson, var_samp(x) AS vx, var_samp(y) AS vy "
                "FROM pairs WHERE x IS NOT NULL AND y IS NOT NULL"), params).mappings().one()
            adequate = row["n"] >= 2 and row["vx"] is not None and row["vy"] is not None and row["vx"] > 0 and row["vy"] > 0
            pearson = finite(row["pearson"]) if adequate else None
            if pearson is not None:
                pearson = max(-1.0, min(1.0, pearson))
            result["correlation"] = {"x": settings.metrics[0], "y": settings.metrics[1], "pair_count": row["n"],
                                     "excluded_count": count - row["n"], "pearson": pearson}
            result["knowledge_eligible"] = pearson is not None
            if not adequate:
                result["warnings"].append("유효한 수치 쌍이 부족하거나 값이 일정하여 상관계수를 계산할 수 없습니다.")
        else:
            from .evaluation_ml import calculate_ml, MAX_ML_ROWS
            if count > MAX_ML_ROWS:
                raise HTTPException(422, f"ML 분석은 {MAX_ML_ROWS:,}행까지 지원합니다. 데이터 범위를 좁혀 주세요.")
            selected = list(dict.fromkeys([*settings.metrics, *settings.features, *settings.unit_columns, settings.time_column]))
            rows = connection.execute(text(bounded + ' SELECT ' + ','.join(quote_output(c) for c in selected) + ' FROM evaluation_rows'), params).mappings().all()
            # Only the bounded local worker receives rows. No rows or trained model go to the LLM.
            result.update(calculate_ml(rows, settings, columns, checkpoint))
            result['warnings'].append('ML은 같은 행에 대응된 과거 설명 항목으로 미래 시점의 목표를 예측합니다. 측정 이후의 항목·중복 측정·상관된 변수의 중요도는 별도 해석이 필요합니다.')
        checkpoint()
    if not count:
        result["warnings"].append("선택한 조건에서 조회된 행이 없습니다.")
    result["summary"] = _summary(result, stored) if settings.method != 'ml_regression' else ml_summary(result, stored)
    if identities['labels']:
        result['summary'] += '\n측정·공정 항목: ' + '; '.join(f'{c} = {label}' for c, label in identities['labels'].items()) + '.'
        result['item_labels'] = identities['labels']
    return result


def fingerprint_rows(connection, bounded, params):
    return connection.scalar(text(bounded + " SELECT md5(coalesce(string_agg(h,'' ORDER BY h),'')) FROM "
        "(SELECT md5(to_jsonb(r)::text) AS h FROM evaluation_rows r) hashes"), params)


def data_fingerprint(cte, params):
    params = {**params, 'evaluation_row_limit': MAX_ROWS + 1}
    bounded = cte + ', evaluation_rows AS MATERIALIZED (SELECT * FROM filtered LIMIT :evaluation_row_limit)'
    with analysis_connection() as connection:
        count = int(connection.scalar(text(bounded + ' SELECT count(*) FROM evaluation_rows'), params))
        if count > MAX_ROWS:
            raise HTTPException(422, '확인할 데이터가 200,000행을 넘었습니다. 범위를 좁혀 주세요.')
        return {'fingerprint': fingerprint_rows(connection, bounded, params), 'row_count': count}


def ml_summary(result, stored):
    ml = result['ml']
    lines = [f"데이터셋: {stored['_evaluation_dataset']['name']} v{stored['_evaluation_dataset']['version']}; 조회 {result['captured_at']}.",
        f"목표: {ml['target']}. 설명 항목: {', '.join(stored['settings']['features'])}.",
        f"전체 {result['row_count']}행, 유효 {ml['valid_rows']}행, 결측 제외 {ml['excluded_rows']}행.", ml['interpretation']]
    if ml.get('validation'):
        v = ml['validation']
        lines.append(f"시간 순서·표본 분리 검증: 학습 {v['train_rows']}행 / 검증 {v['test_rows']}행. 경계 시점 {v['cutoff']}. 표본 중복으로 학습에서 제외 {v['purged_rows']}행.")
    for stat in ml['statistics'][:5]:
        if stat['mean'] is not None:
            lines.append(f"{stat['column']}: 평균 {stat['mean']:.6g}" + (f", 목표와 Pearson 상관 {stat['pearson_to_target']:.6g}." if stat['pearson_to_target'] is not None else '.'))
    for group in ml['groups'][:3]:
        for value in group['values'][:3]:
            lines.append(f"{group['column']}={value['value'][:60]}: {value['count']}행, 목표 평균 {value['target_mean']:.6g}.")
        if len(group['values']) > 3:
            lines.append('나머지 범주의 통계는 원본 실행에서 확인하세요.')
    if ml.get('metrics'):
        m = ml['metrics']
        lines.append(f"검증 평균 절대 오차: ML {m['mae']:.6g}, 학습 중앙값 기준 {m['baseline_mae']:.6g}. R² {m['r2']:.6g}. 기준보다 오차 감소: {m['beats_baseline']}.")
        for v in ml['importance'][:5]:
            lines.append(f"{v['column']}: 검증값을 섞었을 때 평균 절대 오차 증가 {v['mae_increase']:.6g} (변동 {v['std']:.6g}).")
    lines.append('관측·예측 관계입니다. 변수 중요도와 상관은 인과 효과가 아니며 원인을 확정하지 않습니다. 전체 조건·통계는 원본 실행에 보존됩니다.')
    return '\n'.join(lines)
