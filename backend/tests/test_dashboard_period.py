from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.dashboard_period import apply_period, filter_source, period_columns
from app.schemas import CardSpec, DatasetSpec, ISOWeek, QueryContext

SOURCE = dict(id='events', schema_name='public', table_name='events', primary_key='id', project_column=None,
              columns=[{'name': 'occurred_at', 'type': 'timestamp with time zone'}, {'name': 'value', 'type': 'integer'}])
START = ISOWeek(year=2026, week=40)


def card(**changes):
    return CardSpec(id='period', title='Period', dataset_id='events', dataset_version=1, kind='kpi', aggregate='count', **changes)


def compiled(sql='SELECT sum(value) AS total FROM public.events'):
    return dict(source=deepcopy(SOURCE), sources=[deepcopy(SOURCE)], direct_sql=sql, spec=DatasetSpec(source_id='events', raw_sql=sql))


@pytest.mark.parametrize('value', [{'year':2026,'week':53}, {'year':2020,'week':53}, {'year':2027,'week':1}])
def test_validates_actual_iso_week(value):
    assert ISOWeek(**value).week == value['week']


@pytest.mark.parametrize('value', [{'year':2025,'week':53}, {'year':2026,'week':0}, {'year':2026,'week':54}])
def test_rejects_invalid_week(value):
    with pytest.raises(ValidationError): ISOWeek(**value)


def test_period_precedes_aggregation_and_does_not_mutate_authored_sql():
    original = compiled()
    before = deepcopy(original)
    result, meta = apply_period(original, card(), QueryContext(period_start=START))
    assert original == before
    assert 'FROM (SELECT * FROM public.events WHERE' in result['direct_sql']
    assert '2026-09-28T00:00:00+09:00' in result['direct_sql']
    assert meta['column'] == 'occurred_at'


def test_common_period_off_and_no_context_keep_authored_query():
    original = compiled()
    assert apply_period(original, card(), QueryContext()) == (original, None)
    # Legacy individual ranges are ignored when the common filter is off.
    assert apply_period(original, card(period={'use_dashboard':False, 'start':{'year':2020,'week':53}}), QueryContext(period_start=START)) == (original, None)
    assert card(period={'use_dashboard':False}).period.model_dump() == {'use_dashboard':False, 'source_id':None, 'column':None}


def test_disabled_period_removes_only_dashboard_date_predicate():
    original = compiled('SELECT sum(value) AS total FROM public.events WHERE value > 2')
    configured = card(period={'use_dashboard':False, 'source_id':'events', 'column':'occurred_at'})
    assert apply_period(original, configured, QueryContext(period_start=START)) == (original, None)


def test_date_column_is_schema_validated_and_ambiguity_is_explicit():
    value=compiled()
    assert period_columns(value)['default']['column']=='occurred_at'
    value['sources'][0]['columns'].append({'name':'created_at','type':'date'})
    assert period_columns(value)['default'] is None
    with pytest.raises(HTTPException, match='기준 컬럼'): apply_period(value,card(),QueryContext(period_start=START))
    with pytest.raises(HTTPException): apply_period(value,card(period={'source_id':'events','column':'bad; DROP TABLE events'}),QueryContext(period_start=START))
    result,meta=apply_period(value,card(period={'source_id':'events','column':'created_at'}),QueryContext(period_start=START))
    assert '"created_at"' in result['direct_sql']
    assert ' AS DATE)' in result['direct_sql']
    value['sources'][0]['columns']=[]
    with pytest.raises(HTTPException,match='날짜'): apply_period(value,card(),QueryContext(period_start=START))


def test_cte_names_do_not_get_mistaken_for_source_tables():
    sql='WITH events AS (SELECT * FROM public.events) SELECT sum(value) FROM events'
    transformed=filter_source(sql,SOURCE,{'column':'occurred_at','type':'date'},START)
    assert transformed.count('>=')==1
    assert transformed.endswith('SELECT SUM(value) FROM events')


def test_heatmap_is_not_an_accepted_card_type():
    with pytest.raises(ValidationError): CardSpec(**(card().model_dump()|{'kind':'heatmap'}))


@pytest.mark.parametrize('sql,expected', [
    ('SELECT sum(value) AS result FROM public.events', 5),
    ('SELECT sum(e.value) AS result FROM public.events e WHERE e.value < 10', 5),
    ('WITH filtered AS (SELECT value FROM public.events) SELECT sum(value) AS result FROM filtered', 5),
    ('SELECT sum(public.events.value) AS result FROM public.events', 5),
    ('SELECT sum(value) AS result FROM (SELECT public.events.* FROM public.events) e', 5),
    ('SELECT sum(a.value) AS result FROM public.events a LEFT JOIN public.events b ON a.value=b.value', 5),
    ('SELECT sum(value) AS result FROM (SELECT * FROM public.events UNION ALL SELECT * FROM public.events) t', 10),
    ('SELECT sum(e.value) AS result FROM public.events e WHERE EXISTS (SELECT 1 FROM public.events b WHERE b.value=e.value)', 5),
])
def test_postgres_boundaries_ctes_joins_and_unions(sql,expected):
    from app.query import execute_rows
    from app.db import settings
    if not settings.demo_mode: pytest.skip('Managed local DB required')
    transformed=filter_source(sql,SOURCE,{'column':'occurred_at','type':'timestamp with time zone'},START)
    # Synthetic rows live only in this SELECT; no source or metadata writes.
    rows="(VALUES (TIMESTAMPTZ '2026-09-27 14:59:59+00',100), (TIMESTAMPTZ '2026-09-27 15:00:00+00',2), (TIMESTAMPTZ '2026-10-02 00:00+09',3), (NULL,200)) AS fixture(occurred_at,value)"
    transformed=transformed.replace('public.events',rows)
    assert execute_rows(transformed)[0]['result']==expected
