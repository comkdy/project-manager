from types import SimpleNamespace

import pytest

from app.query import dataset_json
from app.table_visibility import TableVisibility


@pytest.fixture
def catalog():
    sources = [SimpleNamespace(id=name, schema_name='public', table_name=name)
               for name in ('inline_data', 'epm_data')]
    relation = SimpleNamespace(id='pair', left_source_id='inline_data', right_source_id='epm_data')
    return TableVisibility(sources, [relation])


def dataset(**spec):
    return SimpleNamespace(id='joined', name='Joined', version=1, project_id=None, columns=[],
                           spec={'source_id': 'inline_data', **spec})


@pytest.mark.parametrize('sql,expected', [
    ('WITH i AS (SELECT * FROM public.inline_data), e AS (SELECT * FROM public.epm_data) '
     'SELECT i.value FROM i JOIN e ON i.lot_id=e.lot_id', ['public.inline_data', 'public.epm_data']),
    ('SELECT i.value FROM (SELECT * FROM inline_data) i JOIN epm_data e ON i.lot_id=e.lot_id',
     ['public.inline_data', 'public.epm_data']),
    ('SELECT i.value FROM inline_data i JOIN inline_data j ON i.lot_id=j.lot_id', ['public.inline_data']),
    ("WITH epm_data AS (SELECT 'public.epm_data' AS label FROM inline_data) SELECT * FROM epm_data",
     ['public.inline_data']),
    ('SELECT * FROM epm_data', ['public.epm_data']),
    ('SELECT 1', []),
])
def test_physical_sources_come_from_authored_sql_not_primary_source(catalog, sql, expected):
    item = dataset(raw_sql=sql)
    assert dataset_json(item, catalog=catalog)['source_tables'] == expected
    assert item.spec['raw_sql'] == sql


def test_legacy_join_and_invalid_sql_remain_editable(catalog):
    assert dataset_json(dataset(joins=['pair']), catalog=catalog)['source_tables'] == ['public.inline_data', 'public.epm_data']
    assert dataset_json(dataset(raw_sql='SELECT * FROM missing'), catalog=catalog)['source_tables'] is None
    # Cache-key serialization must not trigger catalog lookups or SQL parsing.
    assert 'source_tables' not in dataset_json(dataset(raw_sql='SELECT * FROM missing'))
